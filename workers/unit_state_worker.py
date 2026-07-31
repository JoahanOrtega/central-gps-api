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
from services.notification_service import crear_para_empresa, limpiar_antiguas
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

# Cinturón anti-flapping: no repetir la misma alerta antes de este tiempo.
REALERT_MIN_SEC: int = int(os.getenv("UNIT_STATE_REALERT_MIN_SEC", "3600"))

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

# Última telemetría por IMEI (batch, un solo query). TimescaleDB.
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
    ORDER BY imei, fecha_hora_sistema DESC, fecha_hora_gps DESC
"""

# Inserta en t_alertas_whatsapp para todos los destinos activos de la empresa (BD principal).
# Si no existen destinos activos para la empresa, inserta un registro con id_destino_whatsapp = NULL.
_SQL_INSERT_ALERTA_WHATSAPP = """
    WITH destinos AS (
        SELECT id_destino_whatsapp
        FROM public.t_destinos_whatsapp
        WHERE id_empresa = %(id_empresa)s
          AND status = 1
    )
    INSERT INTO public.t_alertas_whatsapp (
        id_empresa,
        id_destino_whatsapp,
        tipo_alerta,
        mensaje,
        fecha_evento,
        status
    )
    SELECT
        %(id_empresa)s,
        d.id_destino_whatsapp,
        %(tipo_alerta)s,
        %(mensaje)s,
        %(fecha_evento)s,
        0
    FROM (SELECT 1) dummy
    LEFT JOIN destinos d ON TRUE
    ON CONFLICT DO NOTHING
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


def _insertar_alerta_whatsapp(id_empresa: int, payload: dict) -> None:
    """
    Inserta registros de alerta para WhatsApp en t_alertas_whatsapp para
    todos los destinos activos de la empresa.
    """
    from utils.db_cursor import main_cursor

    num_unidad = payload.get("numero_unidad", "Unidad")
    desc = payload.get("descripcion", "Evento de estado")
    mensaje = f"Unidad {num_unidad} - {desc}"

    # Para cumplir con CHECK (tipo_alerta IN ('geocerca', 'velocidad'))
    tipo_alerta = "velocidad"

    fecha_str = payload.get("fecha_hora_evento")
    try:
        fecha_evento = (
            datetime.fromisoformat(fecha_str)
            if isinstance(fecha_str, str)
            else _ahora_naive_utc6()
        )
    except Exception:
        fecha_evento = _ahora_naive_utc6()

    try:
        with main_cursor() as cursor:
            cursor.execute(
                _SQL_INSERT_ALERTA_WHATSAPP,
                {
                    "id_empresa": id_empresa,
                    "tipo_alerta": tipo_alerta,
                    "mensaje": mensaje,
                    "fecha_evento": fecha_evento,
                },
            )
    except Exception as exc:
        logger.warning("No se pudo insertar alerta WhatsApp: %s", repr(exc))


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

        # Persistir la notificación para que la campanita del usuario la vea
        try:
            crear_para_empresa(
                id_empresa=id_empresa,
                tipo=int(payload.get("tipo_evento") or 0),
                titulo=f"{payload.get('numero_unidad', 'Unidad')}: "
                f"{payload.get('descripcion', 'Evento de estado')}",
                mensaje=payload.get("descripcion"),
                id_unidad=payload.get("id_unidad"),
            )
        except Exception as exc:
            logger.warning("No se pudo persistir la notificación: %s", repr(exc))

        # Persistir la alerta de WhatsApp para todos los destinos activos de la empresa
        try:
            _insertar_alerta_whatsapp(id_empresa, payload)
        except Exception as exc:
            logger.warning("No se pudo persistir la alerta de WhatsApp: %s", repr(exc))

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


# Hidratación de purga diaria (para no acumular notificaciones antiguas)
_ultimo_dia_purga: str | None = None


def _purga_diaria() -> None:
    global _ultimo_dia_purga
    hoy = datetime.now().strftime("%Y-%m-%d")
    if _ultimo_dia_purga == hoy:
        return
    _ultimo_dia_purga = hoy
    try:
        limpiar_antiguas()
    except Exception as exc:
        logger.warning("Purga de notificaciones falló: %s", repr(exc))


def _ciclo_interno() -> None:
    """
    1. Lee todas las unidades activas (BD principal).
    2. Lee la última telemetría por IMEI (BD telemetría, un solo query).
    3. Evalúa transiciones a estado crítico y publica eventos.
    4. Limpia el registro de cooldown cuando una unidad sale del estado.
    """
    _purga_diaria()

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


# Hidratación de cooldown (para no alertar de nuevo un episodio ya notificado)
_max_fecha_notif: dict[tuple[int, int], object] | None = None


def _cargar_max_fechas_notificacion() -> dict[tuple[int, int], object]:
    """Última fecha de notificación por (id_unidad, tipo). Una vez por vida."""
    from utils.db_cursor import main_cursor

    fechas: dict[tuple[int, int], object] = {}
    try:
        with main_cursor() as cursor:
            cursor.execute("""
                SELECT id_unidad, tipo, MAX(fecha_registro)
                FROM t_notificaciones_usuario
                WHERE id_unidad IS NOT NULL
                GROUP BY id_unidad, tipo
                """)
            for id_unidad, tipo, fecha in cursor.fetchall():
                fechas[(id_unidad, tipo)] = fecha
    except Exception as exc:
        # Sin hidratación el worker sigue funcionando (solo pierde la
        # protección anti-duplicado de este arranque) — nunca tumbar el
        # ciclo por esto.
        logger.warning("Hidratación de cooldown falló: %s", repr(exc))
    return fechas


def _naive(dt):
    """
    Convierte un datetime con tzinfo a naive (sin tzinfo) en UTC-6.
    1. Si dt es None, devuelve None.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


def _ya_notificado_este_episodio(unidad: dict, telem: dict | None, tipo: int) -> bool:
    """
    True si ya se notificó este episodio de estado crítico (cooldown).
    """
    global _max_fecha_notif
    if _max_fecha_notif is None:
        _max_fecha_notif = _cargar_max_fechas_notificacion()

    fecha_notif = _naive(_max_fecha_notif.get((unidad.get("id_unidad"), tipo)))
    if fecha_notif is None:
        return False

    ultima_llegada = _naive(telem.get("fecha_hora_sistema")) if telem else None
    # Sin dato de referencia, la existencia de una notificación previa
    # basta como evidencia del episodio ya avisado.
    if ultima_llegada is None:
        return True
    return fecha_notif > ultima_llegada


def _en_ventana_de_rearme(unidad: dict, tipo: int) -> bool:
    """True si la última alerta de esta unidad+tipo es demasiado reciente."""
    if _max_fecha_notif is None:
        return False
    fecha_notif = _naive(_max_fecha_notif.get((unidad.get("id_unidad"), tipo)))
    if fecha_notif is None:
        return False
    transcurrido = (_ahora_naive_utc6() - fecha_notif).total_seconds()
    return transcurrido < REALERT_MIN_SEC


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
            # ¿Ya notificamos este episodio? Si es así, no alertamos de nuevo
            if _ya_notificado_este_episodio(unidad, telem, tipo):
                _alertado.add(clave)
                return
            # ¿La última notificación fue demasiado reciente? Si es así, no alertamos
            if _en_ventana_de_rearme(unidad, tipo):
                _alertado.add(clave)
                return
            _alertado.add(clave)
            _publicar_evento(
                unidad["id_empresa"],
                _construir_payload(unidad, tipo, telem),
            )
            # Actualizar la fecha de notificación para el cooldown y la persistencia
            if _max_fecha_notif is not None:
                _max_fecha_notif[(unidad.get("id_unidad"), tipo)] = _ahora_naive_utc6()
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