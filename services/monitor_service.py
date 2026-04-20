"""
monitor_service.py — Servicio de monitoreo en vivo de unidades

────────────────────────────────────────────────────────────────────────────────
Responsabilidad
────────────────────────────────────────────────────────────────────────────────
  Retorna las unidades activas de una empresa con su última telemetría,
  listas para renderizar en el mapa del módulo de monitoreo.

  Incluye los campos que el drawer de unidades y el TripDrawer necesitan
  sin peticiones adicionales: vel_max, operador, grupo y el estado del
  motor (engine_state) ya resuelto por utils.engine_state.

────────────────────────────────────────────────────────────────────────────────
Optimizaciones incorporadas
────────────────────────────────────────────────────────────────────────────────
  1. Acceso a BD con context managers (`main_cursor`, `telemetry_cursor`)
     que garantizan liberación del pool aunque el bloque lance excepción.
  2. get_unit_summary_by_imei() usa una query dedicada (4 columnas) en
     lugar de la query completa de 22 columnas.
  3. Conteo de engine_state pre-calculado en el backend.
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from services.telemetry_service import (
    get_latest_positions_by_imeis,
    get_seconds_in_state_for_imei,
    to_app_iso,
)
from utils.db_cursor import main_cursor, telemetry_cursor
from utils.engine_state import EngineState, resolve_engine_state

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Tipos de respuesta (TypedDict para claridad y reutilización)
# ══════════════════════════════════════════════════════════════════════════════


class UnitsLiveCounts(TypedDict):
    """Conteos agregados del estado del motor para el badge del drawer."""

    total: int
    engine_on: int
    engine_off: int
    engine_unknown: int


class UnitsLiveResponse(TypedDict):
    """Respuesta del endpoint GET /monitor/units-live."""

    units: list[dict[str, Any]]
    counts: UnitsLiveCounts


class UnitSummaryResponse(TypedDict):
    """Respuesta del endpoint GET /monitor/unit-summary/<imei>."""

    id: int
    numero: str
    marca: str | None
    modelo: str | None
    imei: str
    vel_max: float | None
    last_report: str | None
    status: str
    engine_state: EngineState
    segundos_en_estado_actual: int | None
    hasTelemetry: bool


# ══════════════════════════════════════════════════════════════════════════════
# Query del monitor en vivo
# ══════════════════════════════════════════════════════════════════════════════

# Query principal: unidades activas + operador + grupo.
# A nivel de módulo para que el parser de Python no la re-analice en cada request.
_UNITS_BASE_QUERY = """
    SELECT
        u.id_unidad,
        u.numero                              AS numero,
        u.marca,
        u.modelo,
        u.anio,
        u.matricula,
        u.tipo,
        u.imagen,
        u.imei,
        u.chip,
        u.id_operador,
        u.status,
        u.vel_max,
        o.nombre                        AS operador,
        COALESCE(g.nombre, 'Sin Grupo') AS grupo
    FROM t_unidades u
    LEFT JOIN r_unidad_operador         ro ON ro.id_unidad_operador = u.id_unidad_operador
    LEFT JOIN t_operadores              o  ON o.id_operador         = ro.id_operador
    LEFT JOIN r_grupo_unidades_unidades rg ON rg.id_unidad           = u.id_unidad
    LEFT JOIN t_grupos_unidades         g  ON g.id_grupo_unidades    = rg.id_grupo_unidades
    WHERE u.id_empresa = %s AND u.status = 1
"""

_UNITS_SEARCH_FILTER = """
    AND (
        LOWER(u.numero)   LIKE LOWER(%s) OR
        LOWER(u.marca)    LIKE LOWER(%s) OR
        LOWER(u.modelo)   LIKE LOWER(%s) OR
        LOWER(o.nombre)   LIKE LOWER(%s)
    )
"""

_UNITS_ORDER_BY = " ORDER BY COALESCE(g.nombre, 'zzz') ASC, u.numero ASC"


def get_units_with_latest_telemetry(
    id_empresa: int,
    search: str | None = None,
) -> UnitsLiveResponse:
    """
    Retorna todas las unidades activas de la empresa con su última telemetría.

    La respuesta tiene forma:
        {
          "units":  [ { ...unidad..., "engine_state": "on"|"off"|"unknown" }, ...],
          "counts": {
            "total": 42,
            "engine_on": 18,
            "engine_off": 22,
            "engine_unknown": 2
          }
        }

    Los conteos se calculan UNA VEZ en el backend durante el ensamblado de
    la respuesta, sustituyendo el .filter().length que el UnitsDrawer del
    frontend ejecutaba en cada render.

    Args:
        id_empresa: ID de la empresa activa.
        search:     Término para filtrar por número, marca, modelo u operador.
                    Si está vacío o es None, no se aplica filtro.

    Returns:
        UnitsLiveResponse con lista de unidades y conteos agregados.
    """
    try:
        # ── Paso 1: cargar unidades + operador + grupo ─────────────────
        query = _UNITS_BASE_QUERY
        params: list[Any] = [id_empresa]
        if search:
            query += _UNITS_SEARCH_FILTER
            like = f"%{search}%"
            params.extend([like, like, like, like])
        query += _UNITS_ORDER_BY

        with main_cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

        # Primer pase: construir lista base de unidades y recopilar IMEIs.
        units: list[dict[str, Any]] = []
        imeis: list[str] = []
        for row in rows:
            unit_imei = str(row[8]).strip() if row[8] else ""
            units.append(
                {
                    "id": row[0],
                    "numero": row[1],
                    "marca": row[2],
                    "modelo": row[3],
                    "anio": row[4],
                    "matricula": row[5],
                    "tipo": row[6],
                    "imagen": row[7],
                    "imei": unit_imei,
                    "chip": row[9],
                    "id_operador": row[10],
                    "status": row[11],
                    "vel_max": float(row[12]) if row[12] is not None else None,
                    "operador": row[13],
                    "grupo": row[14],
                }
            )
            if unit_imei:
                imeis.append(unit_imei)

        # ── Paso 2: una sola query de telemetría (evita N+1) ───────────
        telemetry_list = get_latest_positions_by_imeis(imeis)
        telemetry_map = {t["imei"]: t for t in telemetry_list}

        # ── Paso 3: enriquecer con telemetría + contar engine_state ────
        counts: UnitsLiveCounts = {
            "total": len(units),
            "engine_on": 0,
            "engine_off": 0,
            "engine_unknown": 0,
        }

        enriched_units: list[dict[str, Any]] = []
        for unit in units:
            telemetry = telemetry_map.get(unit["imei"])
            # engine_state y segundos_en_estado_actual vienen pre-resueltos
            # desde get_latest_positions_by_imeis. Se exponen a nivel de la
            # unidad para que el frontend no tenga que encadenar `?.` en
            # el caso común "la unidad tiene telemetría".
            engine_state: EngineState = (
                telemetry["engine_state"] if telemetry else "unknown"
            )
            seconds_in_state: int | None = (
                telemetry["segundos_en_estado_actual"] if telemetry else None
            )

            counts[f"engine_{engine_state}"] += 1  # type: ignore[literal-required]

            enriched_units.append(
                {
                    **unit,
                    "telemetry": telemetry,
                    "engine_state": engine_state,
                    "segundos_en_estado_actual": seconds_in_state,
                }
            )

        return {"units": enriched_units, "counts": counts}

    except Exception:
        logger.exception(
            "Error en get_units_with_latest_telemetry id_empresa=%s", id_empresa
        )
        raise


# ══════════════════════════════════════════════════════════════════════════════
# Summary de unidad individual (optimizado)
# ══════════════════════════════════════════════════════════════════════════════

# Query liviana para el TripDrawer: solo lo que el summary REALMENTE usa.
# Antes se llamaba a get_latest_position_by_imei() que traía 22 columnas.
# Ahora: 3 columnas. Menor latencia + menos bytes transferidos.
_UNIT_SUMMARY_TELEMETRY_QUERY = """
    SELECT fecha_hora_gps, status, tipo_alerta
    FROM public.t_data
    WHERE imei = %s
    ORDER BY fecha_hora_gps DESC
    LIMIT 1
"""

_UNIT_STATIC_DATA_QUERY = """
    SELECT id_unidad, numero, marca, modelo, imei, vel_max
    FROM t_unidades
    WHERE imei = %s AND id_empresa = %s AND status = 1
    LIMIT 1
"""


def get_unit_summary_by_imei(
    imei: str,
    id_empresa: int,
) -> UnitSummaryResponse | None:
    """
    Retorna el resumen de una unidad para el TripDrawer.

    Incluye datos básicos de la unidad (t_unidades) + la última fecha y
    estado del motor (resuelto con engine_state). NO trae sensores extras
    (voltaje, rfid, odómetro) porque el TripDrawer no los usa.

    Args:
        imei:       IMEI de la unidad.
        id_empresa: ID de la empresa (valida pertenencia).

    Returns:
        UnitSummaryResponse si la unidad existe y pertenece a la empresa,
        None en caso contrario.
    """
    # ── Paso 1: datos estáticos desde la BD principal ──────────────────
    with main_cursor() as cursor:
        cursor.execute(_UNIT_STATIC_DATA_QUERY, (imei, id_empresa))
        unit_row = cursor.fetchone()

    if not unit_row:
        return None

    clean_imei = str(unit_row[4]).strip() if unit_row[4] else ""

    # ── Paso 2: última telemetría (liviana) desde la BD de t_data ──────
    # Se abre un cursor separado porque t_unidades y t_data viven en pools
    # distintos (BD principal vs BD de telemetría). Los context managers
    # garantizan que cada conexión vuelva a su pool correcto.
    with telemetry_cursor() as cursor:
        cursor.execute(_UNIT_SUMMARY_TELEMETRY_QUERY, (clean_imei,))
        telemetry_row = cursor.fetchone()

    # ── Paso 3: ensamblar el summary ───────────────────────────────────
    has_telemetry = telemetry_row is not None

    last_report: str | None = None
    status_raw: str | None = None
    tipo_alerta: int | None = None

    if telemetry_row is not None:
        last_report = to_app_iso(telemetry_row[0])
        status_raw = (
            (telemetry_row[1] or "").strip() if telemetry_row[1] is not None else None
        )
        tipo_alerta = telemetry_row[2]

    engine_state = resolve_engine_state(tipo_alerta, status_raw)

    # Tiempo acumulado en el estado actual — solo tiene sentido calcularlo
    # si la unidad tiene telemetría. Si el motor nunca ha registrado un
    # evento tipo_alerta ∈ {33, 34}, la función retorna None y el frontend
    # simplemente oculta la fila (Ley de Tesler: no mostrar valores vacíos).
    seconds_in_state: int | None = (
        get_seconds_in_state_for_imei(clean_imei) if has_telemetry else None
    )

    return {
        "id": unit_row[0],
        "numero": unit_row[1],
        "marca": unit_row[2],
        "modelo": unit_row[3],
        "imei": clean_imei,
        "vel_max": float(unit_row[5]) if unit_row[5] is not None else None,
        "last_report": last_report,
        # Campo `status` conservado por compatibilidad con frontend existente.
        # Nuevos consumidores deben preferir `engine_state`.
        "status": status_raw if status_raw is not None else "Sin información",
        "engine_state": engine_state,
        "segundos_en_estado_actual": seconds_in_state,
        "hasTelemetry": has_telemetry,
    }
