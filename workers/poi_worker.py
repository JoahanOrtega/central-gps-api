"""
Flujo por ciclo:
  1. Leer de BD principal: alertas activas (t_alertas_poi + t_pois).
  2. Leer de BD telemetria: ultimo GPS de cada unidad activa (t_data).
  3. Para cada par (unidad, POI): calcular si esta dentro del perimetro.
  4. Comparar con estado previo en r_poi_unidades (BD principal).
  5. Si cambio de estado: insertar en t_eventos (BD telemetria).
  6. Actualizar r_poi_unidades (BD principal).
  7. Publicar evento en Redis -> SSE -> frontend.
"""

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
from utils.geofence import punto_en_geocerca

logger = logging.getLogger(__name__)

# ── Configuracion ─────────────────────────────────────────────────────────────

POLL_INTERVAL: int = int(os.getenv("WORKER_POLL_INTERVAL", "15"))
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_CHANNEL_BASE = "eventos_poi"

# ── Redis (lazy init) ────────────────────────────────────────────────────────
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
    return _redis_client


# ── Queries SQL ───────────────────────────────────────────────────────────────

# Lee alertas activas con geometria del POI (BD principal)
_SQL_ALERTAS_ACTIVAS = """
    SELECT
        a.id_alerta_poi,
        a.id_empresa,
        a.id_poi,
        a.in_out,
        a.permanencia,
        a.tipo_permanencia,
        a.minutos_permanencia,
        a.vel_max,
        a.vel_max_permitida,
        a.alcance,
        a.id_grupo_unidades,
        p.nombre         AS poi_nombre,
        p.tipo_poi,
        p.lat            AS poi_lat,
        p.lng            AS poi_lng,
        p.radio          AS poi_radio,
        p.polygon_path   AS poi_polygon_path
    FROM t_alertas_poi a
    JOIN t_pois p ON p.id_poi = a.id_poi AND p.status = 1
    WHERE a.status = 1
    ORDER BY a.id_empresa
"""

# Lee ultimo GPS de cada unidad activa de una empresa (BD telemetria).
# Incluye id_data para el FK en t_eventos — necesario para trazabilidad.
# Solo unidades con dato reciente (< 30 min) para no procesar desconectadas.
_SQL_ULTIMOS_GPS = """
    SELECT DISTINCT ON (u.id_unidad)
        u.id_unidad,
        u.numero,
        u.imei,
        d.latitud,
        d.longitud,
        d.velocidad,
        d.fecha_hora_gps,
        d.id_data
    FROM t_unidades u
    JOIN t_data d ON d.imei = u.imei
    WHERE
        u.id_empresa = %(id_empresa)s
        AND u.status = 1
        AND d.fecha_hora_gps >= NOW() - INTERVAL '30 minutes'
    ORDER BY u.id_unidad, d.fecha_hora_gps DESC
"""

# Lee estado actual de una unidad en un POI (BD principal)
_SQL_ESTADO_ACTUAL = """
    SELECT
        id_poi_unidad,
        in_actual,
        fecha_hora_in,
        fecha_hora_out,
        fecha_hora_gps,
        alerta_permanencia,
        fecha_hora_ini_vel_max,
        vel_max_alcanzada
    FROM r_poi_unidades
    WHERE id_unidad = %(id_unidad)s AND id_poi = %(id_poi)s
"""

# Upsert del estado en r_poi_unidades (BD principal).
# ON CONFLICT garantiza atomicidad — no hay race conditions.
_SQL_UPSERT_ESTADO = """
    INSERT INTO r_poi_unidades (
        id_poi, id_unidad, id_empresa,
        in_actual, fecha_hora_in, fecha_hora_out,
        fecha_hora_gps, alerta_permanencia,
        fecha_hora_ini_vel_max, vel_max_alcanzada,
        fecha_registro
    ) VALUES (
        %(id_poi)s, %(id_unidad)s, %(id_empresa)s,
        %(in_actual)s, %(fecha_hora_in)s, %(fecha_hora_out)s,
        %(fecha_hora_gps)s, %(alerta_permanencia)s,
        %(fecha_hora_ini_vel_max)s, %(vel_max_alcanzada)s,
        NOW()
    )
    ON CONFLICT (id_unidad, id_poi) DO UPDATE SET
        in_actual              = EXCLUDED.in_actual,
        fecha_hora_in          = EXCLUDED.fecha_hora_in,
        fecha_hora_out         = EXCLUDED.fecha_hora_out,
        fecha_hora_gps         = EXCLUDED.fecha_hora_gps,
        alerta_permanencia     = EXCLUDED.alerta_permanencia,
        fecha_hora_ini_vel_max = EXCLUDED.fecha_hora_ini_vel_max,
        vel_max_alcanzada      = EXCLUDED.vel_max_alcanzada
"""

# Inserta en t_eventos del servidor de telemetria (hypertable TimescaleDB).
# Refleja la estructura real de la tabla:
#   id_data       -> FK al ping GPS que genero el evento (trazabilidad)
#   id_empresa    -> para filtrar por empresa sin JOIN
#   id_unidad     -> unidad que entro/salio del POI
#   fecha         -> solo la fecha (optimiza queries de tipo "eventos del dia")
#   evento        -> tipo de evento: 10=entro, 11=salio, 12=perm.max, etc.
#   id_elemento   -> el POI que disparo el evento
#   fecha_hora_gmt -> timestamp exacto del evento segun el GPS
#   fecha_registro -> cuando el worker lo detecto e inserto
#   payload       -> JSON con datos extra (detalles de permanencia, velocidad)
_SQL_INSERT_EVENTO = """
    INSERT INTO public.t_eventos (
        id_data,
        id_empresa,
        id_unidad,
        fecha,
        evento,
        id_elemento,
        fecha_hora_gmt,
        fecha_registro,
        payload
    ) VALUES (
        %(id_data)s,
        %(id_empresa)s,
        %(id_unidad)s,
        %(fecha)s,
        %(evento)s,
        %(id_elemento)s,
        %(fecha_hora_gmt)s,
        NOW(),
        %(payload)s
    )
    RETURNING id_evento
"""


# ── Logica principal del ciclo ────────────────────────────────────────────────


def _ejecutar_ciclo() -> None:
    """
    Un ciclo completo de deteccion de geocercas.
    Se ejecuta cada POLL_INTERVAL segundos por APScheduler.
    Los errores se capturan para que el scheduler no detenga la ejecucion.
    """
    try:
        _ciclo_interno()
    except Exception as exc:
        logger.error(
            "Error catastrofico en ciclo POI worker: %s",
            repr(exc),
            exc_info=True,
        )


def _ciclo_interno() -> None:
    """
    Implementacion del ciclo sin manejo de errores de nivel top.

    Usa DOS conexiones separadas:
      conn_main  -> r_poi_unidades, t_alertas_poi (BD principal)
      conn_telem -> t_data (GPS), t_eventos (insercion) (BD telemetria)

    Esto es correcto porque las tablas viven en servidores distintos
    y los pools no deben mezclarse.
    """
    conn_main = conn_telem = None

    try:
        conn_main = get_db_connection()
        conn_telem = get_db_telemetry_connection()

        cur_main = conn_main.cursor()
        cur_telem = conn_telem.cursor()

        # ── 1. Leer alertas activas (BD principal) ────────────────────────
        cur_main.execute(_SQL_ALERTAS_ACTIVAS)
        col_alertas = [d[0] for d in cur_main.description]
        alertas = [dict(zip(col_alertas, row)) for row in cur_main.fetchall()]

        if not alertas:
            logger.debug("Sin alertas activas — ciclo terminado.")
            return

        # ── 2. Agrupar por empresa ────────────────────────────────────────
        alertas_por_empresa: dict[int, list[dict]] = {}
        for alerta in alertas:
            emp = alerta["id_empresa"]
            alertas_por_empresa.setdefault(emp, []).append(alerta)

        total_eventos = 0

        # ── 3. Procesar empresa por empresa ──────────────────────────────
        for id_empresa, alertas_empresa in alertas_por_empresa.items():

            try:
                cur_telem.execute(_SQL_ULTIMOS_GPS, {"id_empresa": id_empresa})
                col_gps = [d[0] for d in cur_telem.description]
                unidades_gps = [dict(zip(col_gps, row)) for row in cur_telem.fetchall()]
            except Exception as exc:
                logger.error("Error leyendo GPS empresa=%s: %s", id_empresa, repr(exc))
                continue

            if not unidades_gps:
                continue

            # ── 4. Para cada unidad x alerta -> detectar evento ──────────
            eventos_empresa: list[dict] = []

            for unidad in unidades_gps:
                for alerta in alertas_empresa:

                    # TODO: filtrar por id_grupo_unidades cuando alcance=1
                    eventos = _procesar_par_unidad_poi(
                        unidad=unidad,
                        alerta=alerta,
                        cur_main=cur_main,
                        conn_main=conn_main,
                        cur_telem=cur_telem,
                        conn_telem=conn_telem,
                    )
                    eventos_empresa.extend(eventos)
                    total_eventos += len(eventos)

            # ── 5. Publicar en Redis ──────────────────────────────────────
            if eventos_empresa:
                _publicar_eventos_redis(id_empresa, eventos_empresa)

        if total_eventos > 0:
            logger.info("Ciclo POI: %d eventos detectados.", total_eventos)

    finally:
        # Siempre devolver conexiones al pool aunque haya errores
        if conn_main:
            release_db_connection(conn_main)
        if conn_telem:
            release_db_telemetry_connection(conn_telem)


def _procesar_par_unidad_poi(
    unidad: dict,
    alerta: dict,
    cur_main,
    conn_main,
    cur_telem,
    conn_telem,
) -> list[dict]:
    """
    Evalua una unidad contra un POI y genera los eventos correspondientes.

    Args:
        unidad:     Dict con datos GPS actuales de la unidad (de t_data).
        alerta:     Dict con configuracion de alerta del POI (de t_alertas_poi).
        cur_main:   Cursor BD principal (leer/escribir r_poi_unidades).
        conn_main:  Conexion BD principal (para commit del estado).
        cur_telem:  Cursor BD telemetria (insertar en t_eventos).
        conn_telem: Conexion BD telemetria (para commit del evento).

    Returns:
        Lista de eventos generados. Puede estar vacia si no hubo cambio.
    """
    id_unidad = unidad["id_unidad"]
    id_poi = alerta["id_poi"]
    id_empresa = alerta["id_empresa"]
    eventos: list[dict] = []

    # ── Calcular si la unidad esta dentro del POI ─────────────────────────
    new_in = punto_en_geocerca(
        lat_punto=float(unidad["latitud"]),
        lng_punto=float(unidad["longitud"]),
        tipo_poi=alerta["tipo_poi"],
        lat_centro=float(alerta["poi_lat"]) if alerta["poi_lat"] else None,
        lng_centro=float(alerta["poi_lng"]) if alerta["poi_lng"] else None,
        radio_m=alerta["poi_radio"],
        polygon_path=alerta["poi_polygon_path"],
    )

    # ── Leer estado previo en r_poi_unidades ─────────────────────────────
    cur_main.execute(_SQL_ESTADO_ACTUAL, {"id_unidad": id_unidad, "id_poi": id_poi})
    row_estado = cur_main.fetchone()
    col_estado = [d[0] for d in cur_main.description]
    estado = dict(zip(col_estado, row_estado)) if row_estado else None

    old_in = estado["in_actual"] if estado else None
    fecha_hora_gps = unidad["fecha_hora_gps"]

    # Evitar reprocesar el mismo dato GPS
    if (
        estado
        and estado["fecha_hora_gps"]
        and fecha_hora_gps <= estado["fecha_hora_gps"]
    ):
        return []

    # ── Preparar nuevo estado ────────────────────────────────────────────
    nuevo_estado: dict = {
        "id_poi": id_poi,
        "id_unidad": id_unidad,
        "id_empresa": id_empresa,
        "in_actual": 1 if new_in else 0,
        "fecha_hora_gps": fecha_hora_gps,
        "fecha_hora_in": estado["fecha_hora_in"] if estado else None,
        "fecha_hora_out": estado["fecha_hora_out"] if estado else None,
        "alerta_permanencia": estado["alerta_permanencia"] if estado else 0,
        "fecha_hora_ini_vel_max": estado["fecha_hora_ini_vel_max"] if estado else None,
        "vel_max_alcanzada": estado["vel_max_alcanzada"] if estado else None,
    }

    cambio_estado = (old_in is None) or (old_in != (1 if new_in else 0))

    # ── Detectar entrada / salida ─────────────────────────────────────────
    if cambio_estado and alerta["in_out"] == 1:
        tipo_evento = 10 if new_in else 11

        if new_in:
            nuevo_estado["fecha_hora_in"] = fecha_hora_gps
            nuevo_estado["alerta_permanencia"] = 0
            nuevo_estado["fecha_hora_ini_vel_max"] = None
            nuevo_estado["vel_max_alcanzada"] = None
        else:
            nuevo_estado["fecha_hora_out"] = fecha_hora_gps

        evento = _construir_evento(tipo_evento, unidad, alerta, detalles=None)
        eventos.append(evento)
        _insertar_evento_bd(cur_telem, conn_telem, evento)

    # ── Detectar exceso de permanencia ────────────────────────────────────
    if (
        new_in
        and alerta["permanencia"] == 1
        and nuevo_estado["alerta_permanencia"] == 0
        and nuevo_estado["fecha_hora_in"] is not None
    ):
        minutos_dentro = _minutos_entre(nuevo_estado["fecha_hora_in"], fecha_hora_gps)
        minutos_permitidos = alerta["minutos_permanencia"] or 0

        if alerta["tipo_permanencia"] == 1 and minutos_dentro >= minutos_permitidos:
            detalles = {
                "fecha_hora_in": str(nuevo_estado["fecha_hora_in"]),
                "minutos_dentro": round(minutos_dentro, 1),
                "minutos_permitidos": minutos_permitidos,
            }
            evento = _construir_evento(12, unidad, alerta, detalles)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)
            nuevo_estado["alerta_permanencia"] = 1

        elif (
            not new_in
            and alerta["tipo_permanencia"] == 2
            and nuevo_estado["fecha_hora_in"] is not None
            and nuevo_estado["fecha_hora_out"] is not None
            and minutos_dentro < minutos_permitidos
        ):
            detalles = {
                "fecha_hora_in": str(nuevo_estado["fecha_hora_in"]),
                "fecha_hora_out": str(nuevo_estado["fecha_hora_out"]),
                "minutos_dentro": round(minutos_dentro, 1),
                "minutos_requeridos": minutos_permitidos,
            }
            evento = _construir_evento(13, unidad, alerta, detalles)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)
            nuevo_estado["alerta_permanencia"] = 1

    # ── Detectar exceso de velocidad dentro del POI ───────────────────────
    if new_in and alerta["vel_max"] == 1 and alerta["vel_max_permitida"]:
        vel_actual = float(unidad["velocidad"] or 0)
        vel_permitida = float(alerta["vel_max_permitida"])
        ini_vel = nuevo_estado["fecha_hora_ini_vel_max"]

        if vel_actual >= vel_permitida and ini_vel is None:
            nuevo_estado["fecha_hora_ini_vel_max"] = fecha_hora_gps
            nuevo_estado["vel_max_alcanzada"] = vel_actual
            evento = _construir_evento(14, unidad, alerta, detalles=None)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)

        elif vel_actual >= vel_permitida and ini_vel is not None:
            if vel_actual > float(nuevo_estado["vel_max_alcanzada"] or 0):
                nuevo_estado["vel_max_alcanzada"] = vel_actual

        elif vel_actual < vel_permitida and ini_vel is not None:
            detalles = {
                "vel_max_permitida": vel_permitida,
                "vel_max_alcanzada": float(nuevo_estado["vel_max_alcanzada"] or 0),
                "duracion_segundos": int(
                    (fecha_hora_gps - ini_vel).total_seconds()
                    if isinstance(ini_vel, datetime)
                    else 0
                ),
            }
            nuevo_estado["fecha_hora_ini_vel_max"] = None
            nuevo_estado["vel_max_alcanzada"] = None
            evento = _construir_evento(15, unidad, alerta, detalles)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)

    # ── Guardar estado actualizado en r_poi_unidades (BD principal) ───────
    try:
        cur_main.execute(_SQL_UPSERT_ESTADO, nuevo_estado)
        conn_main.commit()
    except Exception as exc:
        conn_main.rollback()
        logger.error(
            "Error guardando estado id_unidad=%s id_poi=%s: %s",
            id_unidad,
            id_poi,
            repr(exc),
        )

    return eventos


# ── Helpers ───────────────────────────────────────────────────────────────────


def _construir_evento(
    tipo_evento: int,
    unidad: dict,
    alerta: dict,
    detalles: dict | None,
) -> dict:
    """
    Construye el dict canonico de un evento para BD y Redis.

    Mapeo al esquema real de t_eventos:
      evento       = tipo_evento  (10=entro, 11=salio, 12=perm.max, etc.)
      id_elemento  = id_poi       (el POI que disparo el evento)
      id_data      = id_data      (FK al ping GPS — puede ser None si no disponible)
      fecha        = date del GPS (para queries por dia sin funciones de tiempo)
      fecha_hora_gmt = timestamp exacto del evento
      payload      = JSON con detalles extra (permanencia, velocidad)
    """
    fecha_gps = unidad["fecha_hora_gps"]
    return {
        # Campos de t_eventos
        "id_data": unidad.get("id_data"),
        "id_empresa": alerta["id_empresa"],
        "id_unidad": unidad["id_unidad"],
        "fecha": fecha_gps.date() if isinstance(fecha_gps, datetime) else None,
        "evento": tipo_evento,
        "id_elemento": alerta["id_poi"],
        "fecha_hora_gmt": fecha_gps,
        "payload": json.dumps(detalles, default=str) if detalles else None,
        # Campos extras solo para Redis (prefijo _ = no van a BD)
        "_numero_unidad": unidad["numero"],
        "_nombre_poi": alerta["poi_nombre"],
        "_descripcion": _descripcion_evento(tipo_evento),
    }


def _insertar_evento_bd(cur_telem, conn_telem, evento: dict) -> None:
    """
    Persiste el evento en t_eventos del servidor de telemetria (hypertable).

    Usa el cursor de telemetria — NO el cursor principal.
    TimescaleDB particiona automaticamente por fecha_hora_gmt.

    Si falla, hace rollback y loggea — no propaga para no detener el ciclo.
    """
    try:
        # Filtrar campos privados antes de insertar
        payload_bd = {k: v for k, v in evento.items() if not k.startswith("_")}
        cur_telem.execute(_SQL_INSERT_EVENTO, payload_bd)
        conn_telem.commit()
    except Exception as exc:
        conn_telem.rollback()
        logger.error(
            "Error insertando en t_eventos tipo=%s id_unidad=%s id_elemento=%s: %s",
            evento.get("evento"),
            evento.get("id_unidad"),
            evento.get("id_elemento"),
            repr(exc),
        )


def _publicar_eventos_redis(id_empresa: int, eventos: list[dict]) -> None:
    """
    Publica cada evento en el canal Redis de la empresa.
    Canal: "eventos_poi:{id_empresa}"

    Si Redis no esta disponible, los eventos ya estan en BD —
    solo se pierde la notificacion en tiempo real (degradacion graciosa).
    """
    try:
        r = _get_redis()
        canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
        for evento in eventos:
            # Construir payload para Redis con nombres legibles
            payload_redis = {
                "tipo_evento": evento["evento"],
                "id_empresa": evento["id_empresa"],
                "id_unidad": evento["id_unidad"],
                "numero_unidad": evento["_numero_unidad"],
                "id_poi": evento["id_elemento"],
                "nombre_poi": evento["_nombre_poi"],
                "descripcion": evento["_descripcion"],
                "fecha_hora_evento": (
                    evento["fecha_hora_gmt"].isoformat()
                    if isinstance(evento["fecha_hora_gmt"], datetime)
                    else str(evento["fecha_hora_gmt"])
                ),
            }
            r.publish(canal, json.dumps(payload_redis, default=str))
            logger.debug(
                "Evento Redis canal=%s tipo=%s unidad=%s poi=%s",
                canal,
                evento["evento"],
                evento["_numero_unidad"],
                evento["_nombre_poi"],
            )
    except redis.RedisError as exc:
        logger.warning(
            "Redis no disponible — evento NO enviado en tiempo real: %s", repr(exc)
        )


def _minutos_entre(inicio, fin) -> float:
    """Calcula minutos entre dos timestamps. Retorna 0 si son None."""
    if not inicio or not fin:
        return 0.0
    if isinstance(inicio, str):
        inicio = datetime.fromisoformat(inicio)
    if isinstance(fin, str):
        fin = datetime.fromisoformat(fin)
    return (fin - inicio).total_seconds() / 60.0


def _descripcion_evento(tipo_evento: int) -> str:
    """Retorna la descripcion legible del tipo de evento para el frontend."""
    return {
        10: "Entro al POI",
        11: "Salio del POI",
        12: "Permanencia maxima excedida",
        13: "Permanencia minima no cumplida",
        14: "Exceso de velocidad inicio",
        15: "Exceso de velocidad fin",
    }.get(tipo_evento, "Evento desconocido")


# ── Scheduler ────────────────────────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def iniciar_worker() -> None:
    """
    Crea e inicia el BackgroundScheduler con el job de deteccion.
    Llamar desde create_app() DESPUES de registrar blueprints.
    """
    global _scheduler

    if os.getenv("WORKER_ENABLED", "true").lower() == "false":
        logger.info("POI Worker deshabilitado (WORKER_ENABLED=false).")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("POI Worker ya esta corriendo — ignorando llamada duplicada.")
        return

    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
        },
    )

    _scheduler.add_job(
        func=_ejecutar_ciclo,
        trigger="interval",
        seconds=POLL_INTERVAL,
        id="poi_geocerca_worker",
        name="POI Geocerca Worker",
        next_run_time=datetime.now(timezone.utc),
    )

    _scheduler.start()
    logger.info(
        "POI Worker iniciado — ciclo cada %ds, canal Redis: %s:{id_empresa}",
        POLL_INTERVAL,
        REDIS_CHANNEL_BASE,
    )


def detener_worker() -> None:
    """Detiene el scheduler de forma graciosa. Llamar en SIGTERM o atexit."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("POI Worker detenido.")
