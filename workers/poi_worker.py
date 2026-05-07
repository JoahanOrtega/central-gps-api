"""
Flujo por ciclo:
  1. Leer de BD principal: alertas activas (t_alertas_poi + t_pois).
  2. Leer de BD principal: unidades activas por empresa (t_unidades).
  3. Leer de BD telemetria: ultimo GPS de los IMEIs encontrados (t_data).
  4. Combinar unidades + GPS en Python.
  5. Para cada par (unidad, POI): calcular si esta dentro del perimetro.
  6. Comparar con estado previo en r_poi_unidades (BD principal).
  7. Si cambio de estado: insertar en t_eventos (BD telemetria).
  8. Actualizar r_poi_unidades (BD principal).
  9. Publicar evento en Redis -> SSE -> frontend.
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

# Lee unidades activas de una empresa (BD principal).
# Se usa para obtener los IMEIs y luego buscar GPS en telemetria.
_SQL_UNIDADES_EMPRESA = """
    SELECT id_unidad, numero, imei
    FROM t_unidades
    WHERE id_empresa = %(id_empresa)s
      AND status = 1
      AND imei IS NOT NULL
      AND imei != ''
"""

# Lee el ultimo GPS de una lista de IMEIs (BD telemetria — t_data).
# DISTINCT ON garantiza un solo registro por IMEI: el mas reciente.
# Solo considera pings de los ultimos 30 minutos para no procesar
# unidades desconectadas.
_SQL_GPS_POR_IMEIS = """
    SELECT DISTINCT ON (imei)
        imei,
        latitud,
        longitud,
        velocidad,
        fecha_hora_gps,
        id_data
    FROM t_data
    WHERE imei = ANY(%(imeis)s)
      AND fecha_hora_gps >= NOW() - INTERVAL '30 minutes'
    ORDER BY imei, fecha_hora_gps DESC
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
    """Ciclo completo de deteccion. Captura errores para que el scheduler continue."""
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
    Implementacion del ciclo de deteccion.

    Usa DOS conexiones separadas:
      conn_main  -> BD principal (t_unidades, t_alertas_poi, r_poi_unidades)
      conn_telem -> BD telemetria (t_data GPS, t_eventos INSERT)
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

        # ── 2. Agrupar alertas por empresa ────────────────────────────────
        alertas_por_empresa: dict[int, list[dict]] = {}
        for alerta in alertas:
            emp = alerta["id_empresa"]
            alertas_por_empresa.setdefault(emp, []).append(alerta)

        total_eventos = 0

        # ── 3. Procesar empresa por empresa ──────────────────────────────
        for id_empresa, alertas_empresa in alertas_por_empresa.items():

            # ── 3a. Leer unidades de BD principal ─────────────────────────
            # t_unidades vive en BD principal, NO en telemetria.
            try:
                cur_main.execute(_SQL_UNIDADES_EMPRESA, {"id_empresa": id_empresa})
                col_u = [d[0] for d in cur_main.description]
                unidades = [dict(zip(col_u, row)) for row in cur_main.fetchall()]
            except Exception as exc:
                conn_main.rollback()
                logger.error(
                    "Error leyendo unidades empresa=%s (BD principal): %s",
                    id_empresa,
                    repr(exc),
                )
                continue

            if not unidades:
                continue

            # ── 3b. Buscar GPS reciente en BD telemetria por IMEI ─────────
            # Solo enviamos los IMEIs al servidor remoto — no hacemos JOIN
            # con t_unidades porque esa tabla no existe en telemetria.
            imeis = [u["imei"] for u in unidades if u.get("imei")]
            if not imeis:
                continue

            try:
                cur_telem.execute(_SQL_GPS_POR_IMEIS, {"imeis": imeis})
                col_g = [d[0] for d in cur_telem.description]
                gps_por_imei: dict[str, dict] = {
                    row[col_g.index("imei")]: dict(zip(col_g, row))
                    for row in cur_telem.fetchall()
                }
            except Exception as exc:
                # Rollback obligatorio — deja la conexion limpia para el
                # proximo ciclo. Sin esto, "InFailedSqlTransaction" contamina
                # todas las queries siguientes de la misma conexion.
                try:
                    conn_telem.rollback()
                except Exception:
                    pass
                logger.error(
                    "Error leyendo GPS empresa=%s (BD telemetria): %s",
                    id_empresa,
                    repr(exc),
                )
                continue

            # ── 3c. Combinar unidades con su GPS en Python ─────────────────
            unidades_gps = []
            for u in unidades:
                gps = gps_por_imei.get(u.get("imei", ""))
                if gps:
                    unidades_gps.append({**u, **gps})

            if not unidades_gps:
                logger.debug(
                    "Empresa=%s: sin GPS reciente para %d unidades",
                    id_empresa,
                    len(unidades),
                )
                continue

            # ── 3d. Para cada unidad x alerta -> detectar evento ──────────
            eventos_empresa: list[dict] = []

            for unidad in unidades_gps:
                for alerta in alertas_empresa:

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

            # ── 3e. Publicar en Redis ─────────────────────────────────────
            if eventos_empresa:
                _publicar_eventos_redis(id_empresa, eventos_empresa)

        if total_eventos > 0:
            logger.info("Ciclo POI: %d eventos detectados.", total_eventos)

    finally:
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
    """Construye el dict canonico de un evento para BD y Redis."""
    fecha_gps = unidad["fecha_hora_gps"]
    return {
        "id_data": unidad.get("id_data"),
        "id_empresa": alerta["id_empresa"],
        "id_unidad": unidad["id_unidad"],
        "fecha": fecha_gps.date() if isinstance(fecha_gps, datetime) else None,
        "evento": tipo_evento,
        "id_elemento": alerta["id_poi"],
        "fecha_hora_gmt": fecha_gps,
        "payload": json.dumps(detalles, default=str) if detalles else None,
        # Campos privados solo para Redis (prefijo _ = no van a BD)
        "_numero_unidad": unidad["numero"],
        "_nombre_poi": alerta["poi_nombre"],
        "_descripcion": _descripcion_evento(tipo_evento),
    }


def _insertar_evento_bd(cur_telem, conn_telem, evento: dict) -> None:
    """Persiste el evento en t_eventos del servidor de telemetria."""
    try:
        payload_bd = {k: v for k, v in evento.items() if not k.startswith("_")}
        cur_telem.execute(_SQL_INSERT_EVENTO, payload_bd)
        conn_telem.commit()
    except Exception as exc:
        try:
            conn_telem.rollback()
        except Exception:
            pass
        logger.error(
            "Error insertando en t_eventos tipo=%s id_unidad=%s: %s",
            evento.get("evento"),
            evento.get("id_unidad"),
            repr(exc),
        )


def _publicar_eventos_redis(id_empresa: int, eventos: list[dict]) -> None:
    """Publica cada evento en el canal Redis de la empresa."""
    try:
        r = _get_redis()
        canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
        for evento in eventos:
            # Serializar fecha con offset -06:00 via to_app_iso.
            # Sin esto .isoformat() emite "2026-05-06T16:33:49" (sin TZ)
            # y el frontend no sabe si es UTC o local — muestra 6 horas
            # adelantadas en el panel de notificaciones.
            fecha_gmt = evento["fecha_hora_gmt"]
            if isinstance(fecha_gmt, datetime):
                from services.telemetry_service import to_app_iso

                fecha_str = to_app_iso(fecha_gmt) or str(fecha_gmt)
            else:
                fecha_str = str(fecha_gmt)

            payload_redis = {
                "tipo_evento": evento["evento"],
                "id_empresa": evento["id_empresa"],
                "id_unidad": evento["id_unidad"],
                "numero_unidad": evento["_numero_unidad"],
                "id_poi": evento["id_elemento"],
                "nombre_poi": evento["_nombre_poi"],
                "descripcion": evento["_descripcion"],
                "fecha_hora_evento": fecha_str,
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
    """Retorna la descripcion legible del tipo de evento."""
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
    """Crea e inicia el scheduler. Llamar desde create_app()."""
    global _scheduler

    if os.getenv("WORKER_ENABLED", "true").lower() == "false":
        logger.info("POI Worker deshabilitado (WORKER_ENABLED=false).")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("POI Worker ya esta corriendo.")
        return

    _scheduler = BackgroundScheduler(
        timezone="UTC",
        job_defaults={"coalesce": True, "max_instances": 1},
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
    """Detiene el scheduler. Llamar en SIGTERM o atexit."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("POI Worker detenido.")
