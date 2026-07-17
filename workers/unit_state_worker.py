# ══════════════════════════════════════════════════════════════════════════════
# unit_state_worker.py — Detección de estados críticos de unidades
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
import redis
from apscheduler.schedulers.background import BackgroundScheduler

from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection,
)
from services.telemetry_service import to_app_iso
from utils.engine_state import SIN_REPORTE_PROLONGADO_SEGS

logger = logging.getLogger(__name__)

# ── Configuración (sobrescribible por variables de entorno) ───────────────────

# Cada cuántos segundos corre el ciclo de detección.
POLL_INTERVAL: int = int(os.getenv("UNIT_STATE_POLL_INTERVAL_SEC", "60"))

# Umbral de sin reportar — mismo criterio que el marcador rojo del mapa.
# El default anterior (6 min) generaba ruido: cualquier hueco de cobertura
# disparaba la alerta. 4h = problema real de equipo, no un túnel.
SIN_TRANSMISION_SEC: int = int(
    os.getenv("UNIT_STATE_SIN_TRANSMISION_SEC", str(SIN_REPORTE_PROLONGADO_SEGS))
)

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Mismo canal base que poi_worker — el SSE ya está suscrito a este canal.
REDIS_CHANNEL_BASE = "eventos_poi"

# ── Tipos de evento de estado ─────────────────────────────────────────────────
# El 20 (Apagado prolongado) está RETIRADO de la emisión; se conserva el ID
# y su descripción solo para interpretar eventos históricos ya almacenados.
TIPO_APAGADO_PROLONGADO = 20
TIPO_SIN_TRANSMISION = 21

_DESCRIPCION_POR_TIPO = {
    TIPO_APAGADO_PROLONGADO: "Apagado prolongado",
    TIPO_SIN_TRANSMISION: "Sin reportar (más de 4 horas sin datos)",
}

# ── SQL ───────────────────────────────────────────────────────────────────────

# Todas las unidades activas del sistema con su empresa.
_SQL_UNIDADES_ACTIVAS = """
    SELECT id_unidad, id_empresa, numero, TRIM(imei) AS imei
    FROM t_unidades
    WHERE status = 1 AND imei IS NOT NULL AND TRIM(imei) <> ''
"""

# Última posición por IMEI (DISTINCT ON es eficiente con el índice de t_data).
# fecha_hora_sistema = cuándo llegó el dato al sistema (vida del dispositivo).
_SQL_ULTIMA_TELEMETRIA = """
    SELECT DISTINCT ON (imei)
        imei,
        fecha_hora_sistema,
        tipo_alerta,
        status,
        latitud,
        longitud
    FROM t_data
    WHERE imei = ANY(%(imeis)s)
    ORDER BY imei, fecha_hora_gps DESC
"""

# ── Estado en memoria: qué alertas ya se notificaron ──────────────────────────
# Clave: (imei, tipo_evento). Se limpia cuando la unidad sale del estado.
_alertado: set[tuple[str, int]] = set()

_scheduler: BackgroundScheduler | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────


# Cliente Redis compartido del módulo. Crear un cliente por publish
# (versión anterior) filtraba conexiones huérfanas — cada from_url abre
# un pool propio que nunca se cierra, y bajo gevent esas conexiones
# acumuladas interferían con el pubsub del SSE y el publish del POI worker.
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Conexión Redis perezosa con singleton de módulo."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _ahora_naive_utc6() -> datetime:
    """
    Ahora en dígitos UTC-6 SIN tzinfo — contrato del proyecto: t_data
    guarda TIMESTAMP WITHOUT TIME ZONE con dígitos UTC-6, así que las
    comparaciones deben hacerse naive contra naive (ver CONTEXTO §2).
    """
    from services.telemetry_service import now_local_naive

    return now_local_naive()


def _publicar_evento(id_empresa: int, payload: dict) -> None:
    """Publica un evento de estado en el canal Redis de la empresa."""
    try:
        r = _get_redis()
        canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
        r.publish(canal, json.dumps(payload, default=str))
        logger.info(
            "unit_state_event publicado — empresa=%s unidad=%s tipo=%s",
            id_empresa,
            payload.get("numero_unidad"),
            payload.get("tipo_evento"),
        )
    except redis.RedisError as exc:
        logger.warning(
            "Redis no disponible — alerta de estado NO enviada: %s", repr(exc)
        )


def _construir_payload(unidad: dict, tipo: int, telem: dict | None) -> dict:
    """
    Arma el payload con la MISMA forma que los eventos POI (el frontend
    reutiliza el parseo) + el discriminador `sse_event` que events_routes
    usa para emitir el tipo SSE correcto.
    """
    return {
        "sse_event": "unit_state_event",
        "tipo_evento": tipo,
        "id_empresa": unidad["id_empresa"],
        "id_unidad": unidad["id_unidad"],
        "numero_unidad": unidad["numero"],
        "descripcion": _DESCRIPCION_POR_TIPO.get(tipo, ""),
        "fecha_hora_evento": to_app_iso(_ahora_naive_utc6()),
        "latitud": (
            float(telem["latitud"])
            if telem and telem.get("latitud") is not None
            else None
        ),
        "longitud": (
            float(telem["longitud"])
            if telem and telem.get("longitud") is not None
            else None
        ),
    }


# ── Ciclo principal ───────────────────────────────────────────────────────────


def _ejecutar_ciclo() -> None:
    """Captura errores para que el scheduler continúe (patrón poi_worker)."""
    try:
        _ciclo_interno()
    except Exception as exc:
        logger.error(
            "Error catastrófico en ciclo unit_state_worker: %s",
            repr(exc),
            exc_info=True,
        )


def _ciclo_interno() -> None:
    """
    1. Lee todas las unidades activas (BD principal).
    2. Lee la última telemetría por IMEI (BD telemetría, un solo query).
    3. Evalúa transiciones a estado crítico y publica eventos.
    4. Limpia el registro de cooldown cuando una unidad sale del estado.
    """
    conn_main = conn_telem = None

    try:
        conn_main = get_db_connection()
        conn_telem = get_db_telemetry_connection()
        cur_main = conn_main.cursor()
        cur_telem = conn_telem.cursor()

        # ── 1. Unidades activas ────────────────────────────────────────
        cur_main.execute(_SQL_UNIDADES_ACTIVAS)
        cols_u = [d[0] for d in cur_main.description]
        unidades = [dict(zip(cols_u, row)) for row in cur_main.fetchall()]
        if not unidades:
            return

        # ── 2. Última telemetría por IMEI (batch, un solo query) ──────
        imeis = [u["imei"] for u in unidades]
        cur_telem.execute(_SQL_ULTIMA_TELEMETRIA, {"imeis": imeis})
        cols_t = [d[0] for d in cur_telem.description]
        telem_por_imei: dict[str, dict] = {
            row[cols_t.index("imei")].strip(): dict(zip(cols_t, row))
            for row in cur_telem.fetchall()
        }

        ahora = _ahora_naive_utc6()

        # ── 3. Evaluar cada unidad ─────────────────────────────────────
        for unidad in unidades:
            imei = unidad["imei"]
            telem = telem_por_imei.get(imei)

            # ── 3a. Sin transmisión ────────────────────────────────────
            # Sin fila en t_data no alertamos: nunca ha transmitido
            # (unidad recién dada de alta) — alertar sería falso positivo.
            if telem and telem.get("fecha_hora_sistema"):
                segundos_sistema = (ahora - telem["fecha_hora_sistema"]).total_seconds()
                _evaluar_transicion(
                    unidad=unidad,
                    telem=telem,
                    tipo=TIPO_SIN_TRANSMISION,
                    en_estado_critico=segundos_sistema > SIN_TRANSMISION_SEC,
                )


    finally:
        if conn_main:
            release_db_connection(conn_main)
        if conn_telem:
            release_db_telemetry_connection(conn_telem)


def _evaluar_transicion(
    unidad: dict,
    telem: dict | None,
    tipo: int,
    en_estado_critico: bool,
) -> None:
    """
    Detecta la TRANSICIÓN al estado crítico:
      - Entra al estado + no estaba alertada → publicar y marcar.
      - Sale del estado → desmarcar (rearma la alerta para el futuro).
      - Sigue dentro y ya alertada → silencio (cooldown).
    """
    imei = unidad["imei"]
    clave = (imei, tipo)

    if en_estado_critico:
        if clave not in _alertado:
            _alertado.add(clave)
            _publicar_evento(
                unidad["id_empresa"],
                _construir_payload(unidad, tipo, telem),
            )
    else:
        _alertado.discard(clave)


# ── Arranque / parada (mismo patrón que poi_worker) ───────────────────────────


def registrar_en_scheduler(scheduler) -> None:
    """
    Registra el job de estados críticos en un scheduler EXISTENTE
    (el del POI worker). No crea scheduler propio — ver get_scheduler()
    en poi_worker para la razón.
    """
    if os.getenv("UNIT_STATE_WORKER_ENABLED", "true").lower() == "false":
        logger.info("Unit State Worker deshabilitado.")
        return

    scheduler.add_job(
        func=_ejecutar_ciclo,
        trigger="interval",
        seconds=POLL_INTERVAL,
        id="unit_state_worker",
        name="Unit State Worker",
        # Desfase de 7s para no disparar en el mismo segundo que el POI job
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=7),
    )
    logger.info(
        "Unit State Worker registrado — ciclo cada %ds, umbral sin-reporte=%ds",
        POLL_INTERVAL,
        SIN_TRANSMISION_SEC,
    )