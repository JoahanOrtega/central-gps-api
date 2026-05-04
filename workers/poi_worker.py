"""
workers/poi_worker.py — Worker de detección de geocercas en tiempo real
────────────────────────────────────────────────────────────────────────────────

Responsabilidad:
  Ejecutarse en background cada POLL_INTERVAL segundos, detectar cuando
  una unidad entra o sale de un POI, y publicar el evento en Redis para
  que el endpoint SSE lo retransmita al frontend en tiempo real.

Flujo por ciclo:
  1. Leer de BD principal todas las alertas activas agrupadas por empresa.
  2. Para cada empresa, leer el último dato GPS de sus unidades activas
     desde la BD de telemetría.
  3. Para cada par (unidad, POI), calcular si la unidad está dentro.
  4. Comparar con el estado previo en r_poi_unidades:
       - Si old_in != new_in → evento de entrada (10) o salida (11).
       - Si in_actual=1 y alerta de permanencia activa → verificar tiempo.
       - Si in_actual=1 y alerta de velocidad activa → verificar velocidad.
  5. Insertar eventos detectados en t_eventos_poi.
  6. Actualizar r_poi_unidades con el nuevo estado.
  7. Publicar cada evento en Redis canal "eventos_poi:{id_empresa}".

Arquitectura de concurrencia:
  - El worker corre en un thread daemon separado del servidor Flask.
  - APScheduler (BackgroundScheduler) gestiona el ciclo sin bloquear
    las peticiones HTTP de Flask.
  - El worker usa el mismo pool de BD que Flask — las conexiones se
    devuelven correctamente en cada ciclo incluso si hay errores.
  - Redis pub/sub desacopla al worker del endpoint SSE: si no hay
    clientes conectados, los mensajes se descartan sin acumular.

Manejo de errores:
  - Un error en el ciclo completo NO detiene el worker — se loggea y
    el scheduler vuelve a ejecutar en el siguiente intervalo.
  - Un error en UNA empresa NO afecta el procesamiento de las demás.
  - Si Redis no está disponible, el evento se guarda en BD pero no
    llega en tiempo real al frontend (degradación graciosa).

Configuración:
  WORKER_POLL_INTERVAL  → segundos entre ciclos. Default: 15.
  WORKER_ENABLED        → "false" para deshabilitar en tests. Default: "true".
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

# ── Configuración ─────────────────────────────────────────────────────────────

# Segundos entre cada ciclo de detección.
# 15s es el balance entre latencia de notificación y carga en BD.
# El GPS de los AVL suele enviar cada 30s cuando está en movimiento,
# así que 15s garantiza detectar el evento en el primer o segundo ciclo.
POLL_INTERVAL: int = int(os.getenv("WORKER_POLL_INTERVAL", "15"))

# URL de Redis para pub/sub.
REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Canal base de Redis. El canal real es "{REDIS_CHANNEL_BASE}:{id_empresa}".
# Permite que el endpoint SSE suscriba solo a los eventos de su empresa.
REDIS_CHANNEL_BASE = "eventos_poi"

# ── Instancia Redis (lazy — se crea al primer uso) ────────────────────────────
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """
    Retorna la instancia Redis, creándola si no existe.
    Lazy init para no fallar en el arranque si Redis no está levantado.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,  # str en vez de bytes — más cómodo para JSON
            socket_timeout=2,  # no bloquear el worker si Redis tarda
            socket_connect_timeout=2,
        )
    return _redis_client


# ── Queries SQL ───────────────────────────────────────────────────────────────

# Lee todas las alertas activas con la geometría de su POI.
# Incluye solo POIs con status=1 (activos — no eliminados).
# La query es ligera porque t_alertas_poi y t_pois tienen pocos registros
# comparado con t_data.
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
        -- Geometría del POI
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

# Lee el último dato GPS de cada unidad activa de una empresa.
# Solo incluye unidades con dato reciente (< 30 minutos) para no
# procesar unidades desconectadas que no generarían eventos reales.
# La BD de telemetría puede ser un servidor distinto al principal.
_SQL_ULTIMOS_GPS = """
    SELECT
        u.id_unidad,
        u.numero,
        u.imei,
        d.latitud,
        d.longitud,
        d.velocidad,
        d.fecha_hora_gps
    FROM t_unidades u
    JOIN t_data d ON d.imei = u.imei
    WHERE
        u.id_empresa = %(id_empresa)s
        AND u.status = 1
        AND d.fecha_hora_gps >= NOW() - INTERVAL '30 minutes'
"""

# Lee el estado actual de una unidad en un POI específico.
# Se usa para comparar old_in vs new_in y evitar duplicar eventos.
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

# Upsert del estado en r_poi_unidades.
# ON CONFLICT garantiza atomicidad — no hay race conditions si dos
# workers corren en paralelo (aunque no debería pasar).
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

# Inserta un evento en t_eventos_poi (tabla particionada).
_SQL_INSERT_EVENTO = """
    INSERT INTO t_eventos_poi (
        id_empresa, id_unidad, id_poi,
        tipo_evento, latitud, longitud, velocidad,
        detalles, fecha_hora_evento, fecha_registro
    ) VALUES (
        %(id_empresa)s, %(id_unidad)s, %(id_poi)s,
        %(tipo_evento)s, %(latitud)s, %(longitud)s, %(velocidad)s,
        %(detalles)s, %(fecha_hora_evento)s, NOW()
    )
    RETURNING id_evento
"""


# ── Lógica principal del ciclo ────────────────────────────────────────────────


def _ejecutar_ciclo() -> None:
    """
    Un ciclo completo de detección de geocercas.

    Se ejecuta cada POLL_INTERVAL segundos por APScheduler.
    Los errores se capturan aquí para que el scheduler no detenga
    la ejecución futura.
    """
    try:
        _ciclo_interno()
    except Exception as exc:
        # Error catastrófico (BD caída, OOM, etc.) — loggear pero no
        # propagar para que APScheduler reintente en el siguiente intervalo.
        logger.error(
            "Error catastrófico en ciclo POI worker: %s", repr(exc), exc_info=True
        )


def _ciclo_interno() -> None:
    """
    Implementación del ciclo de detección sin manejo de errores de nivel top.

    Estructura:
      1. Leer alertas activas (BD principal).
      2. Agrupar por empresa.
      3. Para cada empresa, leer GPS de sus unidades (BD telemetría).
      4. Para cada (unidad, alerta) → detectar evento.
      5. Persistir y publicar.
    """
    conn_main = conn_telem = None

    try:
        conn_main = get_db_connection()
        conn_telem = get_db_telemetry_connection()

        cur_main = conn_main.cursor()
        cur_telem = conn_telem.cursor()

        # ── 1. Leer todas las alertas activas ────────────────────────────────
        cur_main.execute(_SQL_ALERTAS_ACTIVAS)
        alertas_rows = cur_main.fetchall()
        col_alertas = [d[0] for d in cur_main.description]
        alertas = [dict(zip(col_alertas, row)) for row in alertas_rows]

        if not alertas:
            logger.debug("Sin alertas activas — ciclo terminado.")
            return

        # ── 2. Agrupar alertas por empresa ────────────────────────────────────
        alertas_por_empresa: dict[int, list[dict]] = {}
        for alerta in alertas:
            emp = alerta["id_empresa"]
            alertas_por_empresa.setdefault(emp, []).append(alerta)

        total_eventos = 0

        # ── 3. Procesar empresa por empresa ───────────────────────────────────
        for id_empresa, alertas_empresa in alertas_por_empresa.items():

            try:
                cur_telem.execute(
                    _SQL_ULTIMOS_GPS,
                    {"id_empresa": id_empresa},
                )
                gps_rows = cur_telem.fetchall()
                col_gps = [d[0] for d in cur_telem.description]
                unidades_gps = [dict(zip(col_gps, row)) for row in gps_rows]

            except Exception as exc:
                logger.error("Error leyendo GPS empresa=%s: %s", id_empresa, repr(exc))
                continue

            if not unidades_gps:
                continue

            # ── 4. Para cada unidad × alerta → detectar ──────────────────────
            eventos_empresa: list[dict] = []

            for unidad in unidades_gps:
                for alerta in alertas_empresa:

                    # Filtro de alcance: si es por grupo, verificar membresía.
                    # Por ahora procesamos todas — el filtro granular por grupo
                    # se implementa en la Tarea 2C extendida.
                    # TODO: filtrar por id_grupo_unidades cuando alcance=1

                    eventos = _procesar_par_unidad_poi(
                        unidad=unidad,
                        alerta=alerta,
                        cur_main=cur_main,
                        conn_main=conn_main,
                    )
                    eventos_empresa.extend(eventos)
                    total_eventos += len(eventos)

            # ── 5. Publicar en Redis los eventos de esta empresa ──────────────
            if eventos_empresa:
                _publicar_eventos_redis(id_empresa, eventos_empresa)

        if total_eventos > 0:
            logger.info("Ciclo POI: %d eventos detectados.", total_eventos)

    finally:
        # Siempre devolver las conexiones al pool aunque haya errores
        if conn_main:
            release_db_connection(conn_main)
        if conn_telem:
            release_db_telemetry_connection(conn_telem)


def _procesar_par_unidad_poi(
    unidad: dict,
    alerta: dict,
    cur_main,
    conn_main,
) -> list[dict]:
    """
    Evalúa una unidad contra un POI y genera los eventos correspondientes.

    Retorna una lista de dicts de eventos (puede ser vacía si no hay cambio,
    o contener 1-2 eventos si hay entrada + permanencia simultáneamente).

    Args:
        unidad:   Dict con los datos GPS actuales de la unidad.
        alerta:   Dict con la configuración de alerta del POI.
        cur_main: Cursor de la BD principal (para leer y escribir estado).
        conn_main: Conexión principal (para commit).

    Returns:
        Lista de eventos generados en este ciclo para este par (unidad, POI).
        Cada evento es un dict listo para enviar por Redis y persistir en BD.
    """
    id_unidad = unidad["id_unidad"]
    id_poi = alerta["id_poi"]
    id_empresa = alerta["id_empresa"]
    eventos: list[dict] = []

    # ── Calcular si la unidad está dentro del POI ─────────────────────────────
    new_in = punto_en_geocerca(
        lat_punto=float(unidad["latitud"]),
        lng_punto=float(unidad["longitud"]),
        tipo_poi=alerta["tipo_poi"],
        lat_centro=float(alerta["poi_lat"]) if alerta["poi_lat"] else None,
        lng_centro=float(alerta["poi_lng"]) if alerta["poi_lng"] else None,
        radio_m=alerta["poi_radio"],
        polygon_path=alerta["poi_polygon_path"],
    )

    # ── Leer estado previo ────────────────────────────────────────────────────
    cur_main.execute(_SQL_ESTADO_ACTUAL, {"id_unidad": id_unidad, "id_poi": id_poi})
    row_estado = cur_main.fetchone()
    col_estado = [d[0] for d in cur_main.description]
    estado = dict(zip(col_estado, row_estado)) if row_estado else None

    old_in = estado["in_actual"] if estado else None
    fecha_hora_gps = unidad["fecha_hora_gps"]

    # Evitar reprocesar el mismo dato GPS que ya procesamos en el ciclo anterior
    if (
        estado
        and estado["fecha_hora_gps"]
        and fecha_hora_gps <= estado["fecha_hora_gps"]
    ):
        return []

    # ── Preparar el nuevo estado para upsert ─────────────────────────────────
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

    # ── Detectar cambio de estado (entrada / salida) ──────────────────────────
    cambio_estado = (old_in is None) or (old_in != (1 if new_in else 0))

    if cambio_estado and alerta["in_out"] == 1:
        # Determinar tipo de evento
        tipo_evento = 10 if new_in else 11

        if new_in:
            # La unidad ENTRÓ: registrar hora de entrada, resetear permanencia
            nuevo_estado["fecha_hora_in"] = fecha_hora_gps
            nuevo_estado["alerta_permanencia"] = 0
            nuevo_estado["fecha_hora_ini_vel_max"] = None
            nuevo_estado["vel_max_alcanzada"] = None
        else:
            # La unidad SALIÓ: registrar hora de salida
            nuevo_estado["fecha_hora_out"] = fecha_hora_gps

        evento = _construir_evento(
            tipo_evento=tipo_evento,
            unidad=unidad,
            alerta=alerta,
            detalles=None,
        )
        eventos.append(evento)
        _insertar_evento_bd(cur_main, conn_main, evento)

    # ── Detectar exceso de permanencia (solo si la unidad está dentro) ────────
    if (
        new_in
        and alerta["permanencia"] == 1
        and nuevo_estado["alerta_permanencia"] == 0
        and nuevo_estado["fecha_hora_in"] is not None
    ):
        minutos_dentro = _minutos_entre(
            nuevo_estado["fecha_hora_in"],
            fecha_hora_gps,
        )
        minutos_permitidos = alerta["minutos_permanencia"] or 0

        # tipo_permanencia=1: máximo excedido (unidad lleva MÁS de X minutos)
        if alerta["tipo_permanencia"] == 1 and minutos_dentro >= minutos_permitidos:
            detalles = {
                "fecha_hora_in": str(nuevo_estado["fecha_hora_in"]),
                "minutos_dentro": round(minutos_dentro, 1),
                "minutos_permitidos": minutos_permitidos,
            }
            evento = _construir_evento(12, unidad, alerta, detalles)
            eventos.append(evento)
            _insertar_evento_bd(cur_main, conn_main, evento)
            nuevo_estado["alerta_permanencia"] = 1  # no volver a disparar

        # tipo_permanencia=2: mínimo no cumplido (unidad salió antes de X min)
        # Este se evalúa en la salida, no durante la permanencia.
        # Se maneja en el bloque de cambio_estado cuando new_in=False.
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
            _insertar_evento_bd(cur_main, conn_main, evento)
            nuevo_estado["alerta_permanencia"] = 1

    # ── Detectar exceso de velocidad dentro del POI ───────────────────────────
    if new_in and alerta["vel_max"] == 1 and alerta["vel_max_permitida"]:
        velocidad_actual = float(unidad["velocidad"] or 0)
        vel_max_permitida = float(alerta["vel_max_permitida"])
        ini_vel = nuevo_estado["fecha_hora_ini_vel_max"]

        if velocidad_actual >= vel_max_permitida and ini_vel is None:
            # Inicio de exceso
            nuevo_estado["fecha_hora_ini_vel_max"] = fecha_hora_gps
            nuevo_estado["vel_max_alcanzada"] = velocidad_actual
            evento = _construir_evento(14, unidad, alerta, detalles=None)
            eventos.append(evento)
            _insertar_evento_bd(cur_main, conn_main, evento)

        elif velocidad_actual >= vel_max_permitida and ini_vel is not None:
            # Sigue en exceso — actualizar vel máxima si es mayor
            if velocidad_actual > (float(nuevo_estado["vel_max_alcanzada"] or 0)):
                nuevo_estado["vel_max_alcanzada"] = velocidad_actual

        elif velocidad_actual < vel_max_permitida and ini_vel is not None:
            # Fin del exceso de velocidad
            detalles = {
                "vel_max_permitida": vel_max_permitida,
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
            _insertar_evento_bd(cur_main, conn_main, evento)

    # ── Guardar estado actualizado en r_poi_unidades ──────────────────────────
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
    Construye el dict de un evento listo para BD y Redis.

    El dict es la representación canónica del evento — se usa tanto para
    INSERT en t_eventos_poi como para publicar en Redis (JSON serializado).
    """
    return {
        "tipo_evento": tipo_evento,
        "id_empresa": alerta["id_empresa"],
        "id_unidad": unidad["id_unidad"],
        "numero_unidad": unidad["numero"],
        "id_poi": alerta["id_poi"],
        "nombre_poi": alerta["poi_nombre"],
        "latitud": str(unidad["latitud"]),
        "longitud": str(unidad["longitud"]),
        "velocidad": str(unidad["velocidad"]) if unidad["velocidad"] else None,
        "detalles": json.dumps(detalles) if detalles else None,
        "fecha_hora_evento": unidad["fecha_hora_gps"],
        # Campos extra solo para Redis (no van a BD)
        "_descripcion": _descripcion_evento(tipo_evento),
    }


def _insertar_evento_bd(cur_main, conn_main, evento: dict) -> None:
    """
    Persiste el evento en t_eventos_poi.
    Si falla, hace rollback y loggea — no propaga para no detener el ciclo.
    """
    try:
        cur_main.execute(
            _SQL_INSERT_EVENTO,
            {
                "id_empresa": evento["id_empresa"],
                "id_unidad": evento["id_unidad"],
                "id_poi": evento["id_poi"],
                "tipo_evento": evento["tipo_evento"],
                "latitud": evento["latitud"],
                "longitud": evento["longitud"],
                "velocidad": evento["velocidad"],
                "detalles": evento["detalles"],
                "fecha_hora_evento": evento["fecha_hora_evento"],
            },
        )
        conn_main.commit()
    except Exception as exc:
        conn_main.rollback()
        logger.error(
            "Error insertando evento tipo=%s id_unidad=%s id_poi=%s: %s",
            evento["tipo_evento"],
            evento["id_unidad"],
            evento["id_poi"],
            repr(exc),
        )


def _publicar_eventos_redis(id_empresa: int, eventos: list[dict]) -> None:
    """
    Publica cada evento en el canal Redis de la empresa.

    Canal: "eventos_poi:{id_empresa}"
    Mensaje: JSON del evento (sin campos privados que empiecen con "_").

    Si Redis no está disponible, loggea un warning y continúa —
    los eventos ya están en BD, solo se pierde la notificación en tiempo real.
    """
    try:
        r = _get_redis()
        canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
        for evento in eventos:
            # Filtrar campos privados (_descripcion, etc.) antes de serializar
            payload = {k: v for k, v in evento.items() if not k.startswith("_")}
            # Serializar fecha como string ISO
            if isinstance(payload.get("fecha_hora_evento"), datetime):
                payload["fecha_hora_evento"] = payload["fecha_hora_evento"].isoformat()
            r.publish(canal, json.dumps(payload, default=str))
            logger.debug(
                "Evento publicado en Redis canal=%s tipo=%s unidad=%s poi=%s",
                canal,
                evento["tipo_evento"],
                evento["numero_unidad"],
                evento["nombre_poi"],
            )
    except redis.RedisError as exc:
        logger.warning(
            "Redis no disponible — evento NO enviado en tiempo real: %s",
            repr(exc),
        )


def _minutos_entre(inicio, fin) -> float:
    """Calcula los minutos entre dos timestamps. Retorna 0 si son None."""
    if not inicio or not fin:
        return 0.0
    if isinstance(inicio, str):
        inicio = datetime.fromisoformat(inicio)
    if isinstance(fin, str):
        fin = datetime.fromisoformat(fin)
    return (fin - inicio).total_seconds() / 60.0


def _descripcion_evento(tipo_evento: int) -> str:
    """Retorna la descripción legible de un tipo de evento para el frontend."""
    return {
        10: "Entró al POI",
        11: "Salió del POI",
        12: "Permanencia máxima excedida",
        13: "Permanencia mínima no cumplida",
        14: "Exceso de velocidad inicio",
        15: "Exceso de velocidad fin",
    }.get(tipo_evento, "Evento desconocido")


# ── Inicialización del scheduler ──────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def iniciar_worker() -> None:
    """
    Crea e inicia el BackgroundScheduler con el job de detección.

    Llamar desde el factory de Flask (create_app) DESPUÉS de registrar
    los blueprints. El scheduler corre en un thread daemon — se detiene
    automáticamente cuando el proceso principal termina.

    Si WORKER_ENABLED=false (útil en tests), no hace nada.
    """
    global _scheduler

    if os.getenv("WORKER_ENABLED", "true").lower() == "false":
        logger.info("POI Worker deshabilitado (WORKER_ENABLED=false).")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("POI Worker ya está corriendo — ignorando llamada duplicada.")
        return

    _scheduler = BackgroundScheduler(
        # Usar UTC internamente para evitar problemas con cambios de horario.
        # La conversión a America/Mexico_City se hace solo en el frontend.
        timezone="UTC",
        job_defaults={
            # Si un job tarda más que el intervalo, no lo encola — lo salta.
            # Evita que ciclos lentos se acumulen si la BD está lenta.
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
        # Ejecutar inmediatamente al arrancar (no esperar el primer intervalo)
        next_run_time=datetime.now(timezone.utc),
    )

    _scheduler.start()
    logger.info(
        "POI Worker iniciado — ciclo cada %ds, canal Redis: %s:{{id_empresa}}",
        POLL_INTERVAL,
        REDIS_CHANNEL_BASE,
    )


def detener_worker() -> None:
    """
    Detiene el scheduler de forma graciosa.
    Llamar desde el teardown de la app o en señales SIGTERM.
    """
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("POI Worker detenido.")
