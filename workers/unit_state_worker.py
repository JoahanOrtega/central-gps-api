# ══════════════════════════════════════════════════════════════════════════════
# unit_state_worker.py — Detección de estados críticos de unidades
# ══════════════════════════════════════════════════════════════════════════════
#
# Responsabilidad única: detectar cuándo una unidad ENTRA a un estado crítico
# y publicar un evento en tiempo real por el mismo canal Redis → SSE que
# usan los eventos de geocercas.
#
# Estados críticos detectados (TRANSICIONES, no estados sostenidos):
#   20 → Apagado prolongado: motor apagado por más de APAGADO_PROLONGADO_SEC.
#        Vehículo posiblemente abandonado, en taller, o batería desconectada.
#   21 → Sin transmisión: el equipo GPS no reporta hace más de
#        SIN_TRANSMISION_SEC. Problema de dispositivo, red o sabotaje.
#
# ── Por qué un worker SEPARADO de poi_worker ──────────────────────────────────
# El ciclo de poi_worker:
#   a) Solo procesa empresas CON alertas POI activas — una empresa sin
#      geocercas configuradas jamás recibiría alertas de estado.
#   b) Solo evalúa unidades con GPS reciente (< 10 min) — excluiría justo
#      a las unidades sin transmisión, que son las que queremos detectar.
# Un worker propio evalúa TODAS las unidades activas sin esos filtros y
# mantiene cada responsabilidad en su archivo (Single Responsibility).
#
# ── Cooldown ──────────────────────────────────────────────────────────────────
# Sin cooldown, una unidad apagada 5 horas dispararía la alerta en CADA
# ciclo (cada 60s). El registro en memoria `_alertado` guarda qué par
# (imei, tipo) ya fue notificado; se limpia cuando la unidad SALE del
# estado crítico, de modo que una recaída futura vuelve a alertar.
#
# ⚠️ NOTA: los umbrales deben mantenerse alineados con el frontend
# (telemetry-status.ts::APAGADO_PROLONGADO_SEGS = 4h). A futuro, mover
# ambos a configuración por empresa en BD.
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import redis
from apscheduler.schedulers.background import BackgroundScheduler

from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection,
)
from services.telemetry_service import get_seconds_in_state_for_imei, to_app_iso
from utils.engine_state import resolve_engine_state

logger = logging.getLogger(__name__)

# ── Configuración (sobrescribible por variables de entorno) ───────────────────

# Cada cuántos segundos corre el ciclo de detección.
POLL_INTERVAL: int = int(os.getenv("UNIT_STATE_POLL_INTERVAL_SEC", "60"))

# Umbral de apagado prolongado — ALINEAR con telemetry-status.ts del front.
APAGADO_PROLONGADO_SEC: int = int(
    os.getenv("UNIT_STATE_APAGADO_PROLONGADO_SEC", str(4 * 60 * 60))  # 4 horas
)

# Umbral de sin transmisión — alineado con el stroke rojo del marcador (6 min).
SIN_TRANSMISION_SEC: int = int(os.getenv("UNIT_STATE_SIN_TRANSMISION_SEC", "360"))

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Mismo canal base que poi_worker — el SSE ya está suscrito a este canal.
REDIS_CHANNEL_BASE = "eventos_poi"

# ── Tipos de evento de estado ─────────────────────────────────────────────────
TIPO_APAGADO_PROLONGADO = 20
TIPO_SIN_TRANSMISION = 21

_DESCRIPCION_POR_TIPO = {
    TIPO_APAGADO_PROLONGADO: "Apagado prolongado",
    TIPO_SIN_TRANSMISION: "Sin transmisión del equipo GPS",
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


def _get_redis() -> redis.Redis:
    """Conexión Redis perezosa (mismo patrón que poi_worker)."""
    return redis.from_url(REDIS_URL, decode_responses=True)


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

            # ── 3b. Apagado prolongado ─────────────────────────────────
            # Solo si el motor está apagado según el último dato.
            if telem is not None:
                status_raw = (
                    str(telem["status"]).strip()
                    if telem.get("status") is not None
                    else None
                )
                engine_state = resolve_engine_state(
                    telem.get("tipo_alerta"), status_raw
                )

                if engine_state == "off":
                    segundos_apagada = get_seconds_in_state_for_imei(imei)
                    _evaluar_transicion(
                        unidad=unidad,
                        telem=telem,
                        tipo=TIPO_APAGADO_PROLONGADO,
                        en_estado_critico=(
                            segundos_apagada is not None
                            and segundos_apagada > APAGADO_PROLONGADO_SEC
                        ),
                    )
                else:
                    # Motor encendido → si estaba marcada, salió del estado:
                    # limpiar para que una futura recaída vuelva a alertar.
                    _alertado.discard((imei, TIPO_APAGADO_PROLONGADO))

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


def iniciar_worker() -> None:
    """Crea e inicia el scheduler. Llamar junto al iniciar_worker de POIs."""
    global _scheduler

    if os.getenv("UNIT_STATE_WORKER_ENABLED", "true").lower() == "false":
        logger.info("Unit State Worker deshabilitado.")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("Unit State Worker ya está corriendo.")
        return

    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1},
    )
    _scheduler.add_job(
        func=_ejecutar_ciclo,
        trigger="interval",
        seconds=POLL_INTERVAL,
        id="unit_state_worker",
        name="Unit State Worker",
        next_run_time=datetime.now(timezone.utc),
    )
    _scheduler.start()
    logger.info(
        "Unit State Worker iniciado — ciclo cada %ds, umbrales: off=%ds, tx=%ds",
        POLL_INTERVAL,
        APAGADO_PROLONGADO_SEC,
        SIN_TRANSMISION_SEC,
    )


def detener_worker() -> None:
    """Detiene el scheduler. Llamar en SIGTERM o atexit."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Unit State Worker detenido.")
