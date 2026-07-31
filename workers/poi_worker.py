"""
Flujo por ciclo (cada WORKER_POLL_INTERVAL segundos):
  1. Leer de BD principal: alertas activas (t_alertas_poi + t_pois).
  2. Leer de BD principal: unidades activas con vel_max (t_unidades).
  3. Leer de BD telemetria: último GPS de los IMEIs encontrados (t_data).
  4. Combinar unidades + GPS en Python.
  5. Para cada par (unidad, POI): evaluar entrada/salida/paso/permanencia/vel.
  6. Para cada unidad con vel_max: evaluar exceso de velocidad global (ev. 3/4).
  7. Insertar eventos en t_eventos (BD telemetria) y t_alertas_whatsapp (BD principal).
  8. Actualizar estado geográfico en r_poi_unidades (BD principal).
  9. Publicar eventos en Redis → SSE → frontend.

Cache in-memory (UnitStateCache):
  El estado de cada par (unidad, POI) se mantiene en memoria entre ciclos,
  eliminando el SELECT por par en cada ciclo. Solo se persiste en r_poi_unidades
  al detectar un cambio de estado relevante (entrada, salida, alerta).

  Ventaja:
    - Sin cache: 100 unidades × 50 POIs = 5,000 SELECT por ciclo.
    - Con cache: 0 SELECT por par — solo 1 SELECT de sincronización al arrancar.

  Expiración:
    Las entradas del cache expiran tras UNIT_STATE_TTL_MIN minutos de inactividad
    (GPS no recibido). Evita que unidades dadas de baja acumulen memoria.

Eventos que genera este worker:
  Sistema B (eventos de negocio, generados por evaluación del backend):
    10 — Entrada a geocerca (POI o parada)
    11 — Salida de geocerca
    12 — Permanencia máxima excedida en POI
    13 — Permanencia mínima no cumplida en POI
    14 — Inicio exceso de velocidad en POI (vel > vel_max_permitida)
    15 — Fin exceso de velocidad en POI
    19 — Paso por geocerca (trayectoria cruza sin entrar)
     3 — Inicio exceso de velocidad global (vel > t_unidades.vel_max)
     4 — Fin exceso de velocidad global

  Sistema A (tipo_alerta del GPS Suntech — NO generados aquí):
    33/34 — Ignición ON/OFF (los maneja oreja al parsear la trama)

Separación explícita Sistema A vs Sistema B:
  Los eventos 1,2,5,6,18,21 (motor, pánico, desconexión, inmovilizador,
  remolcado) vienen del tipo_alerta en t_data (Sistema A — generados por
  el hardware Suntech). Este worker SOLO genera eventos del Sistema B.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import redis
from apscheduler.schedulers.background import BackgroundScheduler

from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection,
)
from utils.geofence import (
    punto_en_geocerca,
    linea_cruza_geocerca,
    punto_fuera_de_bbox,
    calcular_bounding_box,
)

logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

# Intervalo del ciclo de detección en segundos.
# 5s da una latencia promedio de ~2.5s para eventos de geocerca.
# Si el sistema tiene > 2000 unidades activas, considerar subir a 10s.
POLL_INTERVAL: int = int(os.getenv("WORKER_POLL_INTERVAL", "5"))

# Cooldown mínimo entre eventos del mismo tipo para la misma unidad+POI.
# Evita rafagas de eventos duplicados si el GPS oscila en el borde del perímetro.
COOLDOWN_SECONDS: int = int(os.getenv("WORKER_EVENT_COOLDOWN_SEC", "60"))

# TTL del cache por unidad: si no hay GPS nuevo en X minutos, liberar la entrada.
UNIT_STATE_TTL_MIN: int = int(os.getenv("WORKER_UNIT_TTL_MIN", "60"))

REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_CHANNEL_BASE = "eventos_poi"

# Ventana de GPS activo: ignorar posiciones más antiguas que esto.
# Con intervalo de 5s, 10 minutos es suficientemente conservador.
GPS_MAX_AGE_MIN: int = int(os.getenv("WORKER_GPS_MAX_AGE_MIN", "10"))


# ── Cache in-memory ───────────────────────────────────────────────────────────


@dataclass
class EstadoPOI:
    """
    Estado de una unidad respecto a un POI específico.

    Se mantiene en memoria entre ciclos para evitar SELECT por cada par
    (unidad, POI). Solo se escribe en BD cuando hay cambio de estado relevante.

    Equivalente al GeofenceState del StateCache de Temis.
    """

    # Estado geográfico actual
    dentro: bool = False

    # Tracking de permanencia
    fecha_hora_in: Optional[datetime] = None
    fecha_hora_out: Optional[datetime] = None
    alerta_permanencia_ok: bool = False  # True = ya se emitió la alerta

    # Tracking de velocidad dentro del POI (ev. 14/15)
    fecha_hora_ini_vel_max: Optional[datetime] = None
    vel_max_alcanzada: float = 0.0

    # Última posición GPS procesada (para deduplicación)
    ultima_fecha_gps: Optional[datetime] = None

    # Timestamps de último evento emitido por tipo (cooldown)
    # clave: str(tipo_evento), valor: datetime
    ultimo_evento_at: dict[str, datetime] = field(default_factory=dict)


@dataclass
class EstadoUnidad:
    """
    Estado de una unidad a nivel global (fuera de cualquier POI específico).

    Cubre los eventos que evalúan la unidad sin contexto de POI:
      - Exceso de velocidad global (ev. 3/4)
      - Posición anterior (necesaria para ev. 19 paso por geocerca)

    Equivalente al UnitState sin GeofenceState del StateCache de Temis.
    """

    # Última posición procesada por IMEI — necesaria para ev. 19
    lat_prev: Optional[float] = None
    lng_prev: Optional[float] = None
    fecha_gps_prev: Optional[datetime] = None

    # Tracking de exceso de velocidad global (ev. 3/4)
    fecha_hora_ini_exceso_vel: Optional[datetime] = None
    odometro_ini_exceso_vel: float = 0.0
    vel_max_alcanzada_global: float = 0.0

    # Cooldown de eventos globales
    ultimo_evento_at: dict[str, datetime] = field(default_factory=dict)

    # Timestamp de la última actualización — para expirar entradas inactivas
    ultimo_update: float = field(default_factory=time.monotonic)


class UnitStateCache:
    """
    Cache in-memory hilo-seguro para el estado de todas las unidades.

    Diseño:
      - Un dict de IMEI → EstadoUnidad (estado global por unidad).
      - Un dict de (IMEI, id_poi) → EstadoPOI (estado por par).
      - Lock de lectura/escritura con threading.Lock().
      - Expiración pasiva: se purga en cada ciclo si UNIT_STATE_TTL_MIN vence.

    Nota sobre hilo-seguridad:
      APScheduler ejecuta el ciclo en un único hilo (max_instances=1).
      El Lock es una precaución extra para cuando se agreguen endpoints
      de lectura del cache en el futuro (ej: API de estado en tiempo real).
    """

    def __init__(self) -> None:
        self._estados_unidad: dict[str, EstadoUnidad] = {}
        self._estados_poi: dict[tuple[str, int], EstadoPOI] = {}
        self._lock = threading.Lock()

    def get_unidad(self, imei: str) -> EstadoUnidad:
        """Retorna el estado global de una unidad, creándolo si no existe."""
        with self._lock:
            if imei not in self._estados_unidad:
                self._estados_unidad[imei] = EstadoUnidad()
            return self._estados_unidad[imei]

    def get_poi(self, imei: str, id_poi: int) -> EstadoPOI:
        """Retorna el estado de una unidad en un POI, creándolo si no existe."""
        key = (imei, id_poi)
        with self._lock:
            if key not in self._estados_poi:
                self._estados_poi[key] = EstadoPOI()
            return self._estados_poi[key]

    def purgar_expirados(self) -> int:
        """
        Elimina entradas de unidades inactivas para liberar memoria.
        Una unidad se considera inactiva si no ha recibido GPS en los
        últimos UNIT_STATE_TTL_MIN minutos.

        Returns:
            Número de entradas eliminadas.
        """
        ttl_seg = UNIT_STATE_TTL_MIN * 60
        ahora = time.monotonic()
        eliminados = 0

        with self._lock:
            # Identificar IMEIs expirados
            imeis_expirados = [
                imei
                for imei, estado in self._estados_unidad.items()
                if ahora - estado.ultimo_update > ttl_seg
            ]
            # Eliminar estado global y todos sus estados por POI
            for imei in imeis_expirados:
                del self._estados_unidad[imei]
                claves_poi = [k for k in self._estados_poi if k[0] == imei]
                for k in claves_poi:
                    del self._estados_poi[k]
                eliminados += 1

        if eliminados > 0:
            logger.info("Cache: purgadas %d unidades inactivas.", eliminados)

        return eliminados

    def sincronizar_desde_bd(
        self,
        conn_main,
        unidades: list[dict],
    ) -> None:
        """
        Carga el estado persistido en r_poi_unidades al iniciar el worker.
        Evita que el primer ciclo emita eventos espurios al interpretar
        estados desconocidos como "nunca visto" (entradas falsas).

        Solo carga unidades que NO estén ya en el cache en memoria.
        """
        imeis_sin_cache = [
            u["imei"] for u in unidades if u["imei"] not in self._estados_unidad
        ]
        if not imeis_sin_cache:
            return

        try:
            cur = conn_main.cursor()
            cur.execute(
                """
                SELECT
                    r.id_unidad, r.id_poi, r.in_actual,
                    r.fecha_hora_in, r.fecha_hora_out,
                    r.alerta_permanencia,
                    r.fecha_hora_ini_vel_max, r.vel_max_alcanzada,
                    r.fecha_hora_gps,
                    u.imei
                FROM r_poi_unidades r
                JOIN t_unidades u ON u.id_unidad = r.id_unidad
                WHERE u.imei = ANY(%s)
                """,
                (imeis_sin_cache,),
            )
            col = [d[0] for d in cur.description]
            filas = [dict(zip(col, row)) for row in cur.fetchall()]

            with self._lock:
                for fila in filas:
                    imei = fila["imei"]
                    id_poi = fila["id_poi"]
                    key = (imei, id_poi)

                    # Solo inicializar si aún no existe en cache
                    if key not in self._estados_poi:
                        ep = EstadoPOI(
                            dentro=bool(fila["in_actual"]),
                            fecha_hora_in=fila["fecha_hora_in"],
                            fecha_hora_out=fila["fecha_hora_out"],
                            alerta_permanencia_ok=bool(fila["alerta_permanencia"]),
                            fecha_hora_ini_vel_max=fila["fecha_hora_ini_vel_max"],
                            vel_max_alcanzada=float(fila["vel_max_alcanzada"] or 0),
                            ultima_fecha_gps=fila["fecha_hora_gps"],
                        )
                        self._estados_poi[key] = ep

                    # Asegurar que el EstadoUnidad existe para este IMEI
                    if imei not in self._estados_unidad:
                        self._estados_unidad[imei] = EstadoUnidad()

            logger.info(
                "Cache sincronizado: %d entradas desde r_poi_unidades.",
                len(filas),
            )
        except Exception as exc:
            logger.error(
                "Error sincronizando cache desde BD: %s",
                repr(exc),
                exc_info=True,
            )


# Instancia global del cache — vive durante toda la vida del proceso.
# Se comparte entre ciclos del scheduler.
_cache = UnitStateCache()


# ── Redis (lazy init) ─────────────────────────────────────────────────────────

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

# Lee alertas activas con geometría del POI (BD principal)
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

# Lee unidades activas de una empresa con vel_max (BD principal).
_SQL_UNIDADES_EMPRESA = """
    SELECT id_unidad, id_empresa, numero, imei, vel_max
    FROM t_unidades
    WHERE id_empresa = %(id_empresa)s
      AND status = 1
      AND imei IS NOT NULL
      AND imei != ''
"""

# Lee el último GPS de una lista de IMEIs (BD telemetría — t_data).
# DISTINCT ON garantiza un solo registro por IMEI: el más reciente.
#
# IMPORTANTE: el INTERVAL se construye con un literal fijo en Python antes
# de pasarlo a psycopg2. NO usar %(max_age)s dentro de la cadena SQL porque
# psycopg2 interpreta TODOS los %(nombre)s como parámetros del dict — si
# max_age no está en el dict de params causa KeyError('imeis') al no poder
# parsear correctamente los demás parámetros.
# La interpolación de string segura aquí es con .format() ANTES de llamar
# a execute(), ya que max_age es un entero validado desde os.getenv, no
# input de usuario.
_SQL_GPS_POR_IMEIS_TPL = """
    SELECT DISTINCT ON (imei)
        imei,
        latitud,
        longitud,
        velocidad,
        fecha_hora_gps,
        odometro,
        id_data
    FROM t_data
    WHERE imei = ANY(%(imeis)s)
      AND fecha_hora_gps >= (NOW() AT TIME ZONE 'America/Mexico_City') - INTERVAL '{max_age} minutes'
    ORDER BY imei, fecha_hora_gps DESC
"""

# Upsert del estado en r_poi_unidades (BD principal).
# Solo se ejecuta cuando hay un cambio de estado relevante (not en cada ciclo).
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

# Inserta en t_eventos del servidor de telemetría (hypertable TimescaleDB).
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
    ON CONFLICT (id_unidad, evento, fecha_hora_gmt) DO NOTHING
    RETURNING id_evento
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


# ── Lógica principal del ciclo ────────────────────────────────────────────────


def _ejecutar_ciclo() -> None:
    """Ciclo completo de detección. Captura errores para que el scheduler continue."""
    try:
        _ciclo_interno()
    except Exception as exc:
        logger.error(
            "Error catastrófico en ciclo POI worker: %s",
            repr(exc),
            exc_info=True,
        )


def _ciclo_interno() -> None:
    """
    Implementación del ciclo de detección.

    Usa DOS conexiones separadas:
      conn_main  → BD principal (t_unidades, t_alertas_poi, r_poi_unidades, t_alertas_whatsapp)
      conn_telem → BD telemetría (t_data GPS, t_eventos INSERT)

    El cache in-memory (_cache) reduce las consultas a BD per-par.
    r_poi_unidades solo se actualiza cuando hay un cambio de estado real.
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
            try:
                cur_main.execute(_SQL_UNIDADES_EMPRESA, {"id_empresa": id_empresa})
                col_u = [d[0] for d in cur_main.description]
                unidades = [dict(zip(col_u, row)) for row in cur_main.fetchall()]
            except Exception as exc:
                conn_main.rollback()
                logger.error(
                    "Error leyendo unidades empresa=%s: %s", id_empresa, repr(exc)
                )
                continue

            if not unidades:
                continue

            # ── 3b. Sincronizar cache desde BD (solo IMEIs no en memoria) ─
            _cache.sincronizar_desde_bd(conn_main, unidades)

            # ── 3c. Buscar GPS reciente en BD telemetría por IMEI ─────────
            imeis = [u["imei"] for u in unidades if u.get("imei")]
            if not imeis:
                continue

            try:
                cur_telem.execute(
                    _SQL_GPS_POR_IMEIS_TPL.format(max_age=GPS_MAX_AGE_MIN),
                    {"imeis": imeis},
                )
                col_g = [d[0] for d in cur_telem.description]
                gps_por_imei: dict[str, dict] = {
                    row[col_g.index("imei")]: dict(zip(col_g, row))
                    for row in cur_telem.fetchall()
                }
            except Exception as exc:
                try:
                    conn_telem.rollback()
                except Exception as rb_exc:
                    # Rollback fallido indica conexión de telemetría rota (típico tras
                    # timeout de NAT con el VM de GCP). El pool lazy reconectará en el
                    # siguiente ciclo. Loggear en debug para no generar ruido en cada ciclo.
                    logger.debug(
                        "Rollback telemetría falló leyendo GPS empresa=%s: %s",
                        id_empresa, repr(rb_exc)
                    )
                logger.error("Error leyendo GPS empresa=%s: %s", id_empresa, repr(exc))
                continue

            # ── 3d. Combinar unidades con su GPS en Python ─────────────────
            unidades_gps = []
            for u in unidades:
                gps = gps_por_imei.get(u.get("imei", ""))
                if gps:
                    unidades_gps.append({**u, **gps})

            if not unidades_gps:
                continue

            # ── 3e. Evaluar eventos para cada unidad ──────────────────────
            eventos_empresa: list[dict] = []

            for unidad in unidades_gps:
                imei = unidad.get("imei", "")
                estado_unidad = _cache.get_unidad(imei)

                # ── Evaluar POIs para esta unidad ─────────────────────────
                for alerta in alertas_empresa:
                    eventos_poi = _evaluar_unidad_poi(
                        unidad=unidad,
                        alerta=alerta,
                        estado_unidad=estado_unidad,
                        cur_main=cur_main,
                        conn_main=conn_main,
                        cur_telem=cur_telem,
                        conn_telem=conn_telem,
                    )
                    eventos_empresa.extend(eventos_poi)
                    total_eventos += len(eventos_poi)

                # ── Evaluar velocidad global (ev. 3/4) ────────────────────
                vel_max_global = float(unidad.get("vel_max") or 0)
                if vel_max_global > 0:
                    eventos_vel = _evaluar_velocidad_global(
                        unidad=unidad,
                        vel_max=vel_max_global,
                        estado_unidad=estado_unidad,
                        cur_main=cur_main,
                        conn_main=conn_main,
                        cur_telem=cur_telem,
                        conn_telem=conn_telem,
                    )
                    eventos_empresa.extend(eventos_vel)
                    total_eventos += len(eventos_vel)

                # ── Actualizar posición anterior para próximo ciclo ───────
                # Se hace DESPUÉS de evaluar todos los POIs y la velocidad
                # para que ev.19 y ev.3/4 usen el prev del ciclo ANTERIOR.
                estado_unidad.lat_prev = float(unidad["latitud"])
                estado_unidad.lng_prev = float(unidad["longitud"])
                estado_unidad.fecha_gps_prev = unidad["fecha_hora_gps"]
                estado_unidad.ultimo_update = time.monotonic()

            # ── 3f. Publicar en Redis ─────────────────────────────────────
            if eventos_empresa:
                _publicar_eventos_redis(id_empresa, eventos_empresa)

        # ── 4. Purgar entradas expiradas del cache ────────────────────────
        # Se hace una vez por ciclo, no por empresa, para no penalizar el loop.
        _cache.purgar_expirados()

        if total_eventos > 0:
            logger.info("Ciclo: %d eventos detectados.", total_eventos)

    finally:
        if conn_main:
            release_db_connection(conn_main)
        if conn_telem:
            release_db_telemetry_connection(conn_telem)


# ── Evaluadores de eventos ────────────────────────────────────────────────────


def _evaluar_unidad_poi(
    unidad: dict,
    alerta: dict,
    estado_unidad: EstadoUnidad,
    cur_main,
    conn_main,
    cur_telem,
    conn_telem,
) -> list[dict]:
    """
    Evalúa los eventos de geocerca para un par (unidad, POI).

    Usa el cache in-memory para el estado previo — no hace SELECT a BD.
    Solo escribe en r_poi_unidades cuando hay un cambio de estado relevante.

    Evalúa en orden:
      1. Entrada (ev. 10) / Salida (ev. 11)
      2. Paso (ev. 19) — solo si ambos puntos están fuera
      3. Permanencia excedida (ev. 12) / no cumplida (ev. 13)
      4. Velocidad en POI inicio (ev. 14) / fin (ev. 15)
    """
    id_poi = alerta["id_poi"]
    id_unidad = unidad["id_unidad"]
    id_empresa = alerta["id_empresa"]
    imei = unidad.get("imei", "")
    eventos: list[dict] = []

    fecha_hora_gps = unidad["fecha_hora_gps"]

    # Estado en memoria para este par
    ep = _cache.get_poi(imei, id_poi)

    # Evitar reprocesar el mismo dato GPS (idempotencia)
    if ep.ultima_fecha_gps and fecha_hora_gps <= ep.ultima_fecha_gps:
        return []

    lat_curr = float(unidad["latitud"])
    lng_curr = float(unidad["longitud"])

    # ── Calcular si la unidad está dentro del POI ahora ───────────────────
    new_in = punto_en_geocerca(
        lat_punto=lat_curr,
        lng_punto=lng_curr,
        tipo_poi=alerta["tipo_poi"],
        lat_centro=float(alerta["poi_lat"]) if alerta.get("poi_lat") else None,
        lng_centro=float(alerta["poi_lng"]) if alerta.get("poi_lng") else None,
        radio_m=alerta.get("poi_radio"),
        polygon_path=alerta.get("poi_polygon_path"),
    )

    old_in = ep.dentro
    cambio_estado = new_in != old_in
    estado_persistir = False  # ¿hay que escribir en r_poi_unidades?

    # ── Detección de entrada / salida (ev. 10 / ev. 11) ─────────────────
    if cambio_estado and alerta.get("in_out") == 1:
        tipo_evento = 10 if new_in else 11

        if _cooldown_ok(ep, tipo_evento, fecha_hora_gps):
            if new_in:
                # Entró: registrar hora de entrada, resetear permanencia
                ep.fecha_hora_in = fecha_hora_gps
                ep.alerta_permanencia_ok = False
                ep.fecha_hora_ini_vel_max = None
                ep.vel_max_alcanzada = 0.0
            else:
                # Salió: registrar hora de salida
                ep.fecha_hora_out = fecha_hora_gps

            evento = _construir_evento(tipo_evento, unidad, alerta, detalles=None)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)
            _insertar_alerta_whatsapp_bd(
                cur_main,
                conn_main,
                id_empresa,
                "geocerca",
                _construir_mensaje_whatsapp(evento),
                fecha_hora_gps,
            )
            _marcar_cooldown(ep, tipo_evento, fecha_hora_gps)
            estado_persistir = True

    # ── Detección de paso (ev. 19) — solo si ambos puntos están FUERA ────
    # Requiere posición anterior disponible en el estado global de la unidad.
    if (
        not new_in
        and not old_in
        and estado_unidad.lat_prev is not None
        and alerta.get("in_out") == 1
        and _cooldown_ok(ep, 19, fecha_hora_gps)
    ):
        cruza = linea_cruza_geocerca(
            lat_prev=estado_unidad.lat_prev,
            lng_prev=estado_unidad.lng_prev,
            lat_curr=lat_curr,
            lng_curr=lng_curr,
            tipo_poi=alerta["tipo_poi"],
            lat_centro=float(alerta["poi_lat"]) if alerta.get("poi_lat") else None,
            lng_centro=float(alerta["poi_lng"]) if alerta.get("poi_lng") else None,
            radio_m=alerta.get("poi_radio"),
            polygon_path=alerta.get("poi_polygon_path"),
        )
        if cruza:
            evento = _construir_evento(19, unidad, alerta, detalles=None)
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)
            _insertar_alerta_whatsapp_bd(
                cur_main,
                conn_main,
                id_empresa,
                "geocerca",
                _construir_mensaje_whatsapp(evento),
                fecha_hora_gps,
            )
            _marcar_cooldown(ep, 19, fecha_hora_gps)

    # ── Detección de permanencia excedida (ev. 12) ────────────────────────
    if (
        new_in
        and alerta.get("permanencia") == 1
        and not ep.alerta_permanencia_ok
        and ep.fecha_hora_in is not None
        and alerta.get("tipo_permanencia") == 1
    ):
        minutos_dentro = _minutos_entre(ep.fecha_hora_in, fecha_hora_gps)
        minutos_max = float(alerta.get("minutos_permanencia") or 0)

        if minutos_max > 0 and minutos_dentro >= minutos_max:
            if _cooldown_ok(ep, 12, fecha_hora_gps):
                detalles = {
                    "fecha_hora_in": str(ep.fecha_hora_in),
                    "minutos_dentro": round(minutos_dentro, 1),
                    "minutos_permitidos": minutos_max,
                }
                evento = _construir_evento(12, unidad, alerta, detalles)
                eventos.append(evento)
                _insertar_evento_bd(cur_telem, conn_telem, evento)
                _insertar_alerta_whatsapp_bd(
                    cur_main,
                    conn_main,
                    id_empresa,
                    "geocerca",
                    _construir_mensaje_whatsapp(evento),
                    fecha_hora_gps,
                )
                _marcar_cooldown(ep, 12, fecha_hora_gps)
                ep.alerta_permanencia_ok = True
                estado_persistir = True

    # ── Detección de permanencia no cumplida (ev. 13) ─────────────────────
    if (
        cambio_estado
        and not new_in  # recién salió
        and alerta.get("permanencia") == 1
        and not ep.alerta_permanencia_ok
        and ep.fecha_hora_in is not None
        and alerta.get("tipo_permanencia") == 2
    ):
        minutos_dentro = _minutos_entre(ep.fecha_hora_in, fecha_hora_gps)
        minutos_min = float(alerta.get("minutos_permanencia") or 0)

        if minutos_min > 0 and minutos_dentro < minutos_min:
            if _cooldown_ok(ep, 13, fecha_hora_gps):
                detalles = {
                    "fecha_hora_in": str(ep.fecha_hora_in),
                    "fecha_hora_out": str(fecha_hora_gps),
                    "minutos_dentro": round(minutos_dentro, 1),
                    "minutos_requeridos": minutos_min,
                }
                evento = _construir_evento(13, unidad, alerta, detalles)
                eventos.append(evento)
                _insertar_evento_bd(cur_telem, conn_telem, evento)
                _insertar_alerta_whatsapp_bd(
                    cur_main,
                    conn_main,
                    id_empresa,
                    "geocerca",
                    _construir_mensaje_whatsapp(evento),
                    fecha_hora_gps,
                )
                _marcar_cooldown(ep, 13, fecha_hora_gps)
                ep.alerta_permanencia_ok = True
                estado_persistir = True

    # ── Detección de velocidad en POI: inicio (ev. 14) ────────────────────
    if new_in and alerta.get("vel_max") == 1 and alerta.get("vel_max_permitida"):
        vel_actual = float(unidad.get("velocidad") or 0)
        vel_permitida = float(alerta["vel_max_permitida"])
        ini_vel = ep.fecha_hora_ini_vel_max

        if vel_actual >= vel_permitida and ini_vel is None:
            if _cooldown_ok(ep, 14, fecha_hora_gps):
                ep.fecha_hora_ini_vel_max = fecha_hora_gps
                ep.vel_max_alcanzada = vel_actual
                evento = _construir_evento(14, unidad, alerta, detalles=None)
                eventos.append(evento)
                _insertar_evento_bd(cur_telem, conn_telem, evento)
                _insertar_alerta_whatsapp_bd(
                    cur_main,
                    conn_main,
                    id_empresa,
                    "velocidad",
                    _construir_mensaje_whatsapp(evento),
                    fecha_hora_gps,
                )
                _marcar_cooldown(ep, 14, fecha_hora_gps)
                estado_persistir = True

        elif vel_actual >= vel_permitida and ini_vel is not None:
            # Durante el exceso — actualizar velocidad máxima
            if vel_actual > ep.vel_max_alcanzada:
                ep.vel_max_alcanzada = vel_actual

        elif vel_actual < vel_permitida and ini_vel is not None:
            # ── Fin de velocidad en POI (ev. 15) ─────────────────────────
            if _cooldown_ok(ep, 15, fecha_hora_gps):
                dur_seg = int(
                    (fecha_hora_gps - ini_vel).total_seconds()
                    if isinstance(ini_vel, datetime)
                    else 0
                )
                detalles = {
                    "vel_max_permitida": vel_permitida,
                    "vel_max_alcanzada": ep.vel_max_alcanzada,
                    "duracion_segundos": dur_seg,
                }
                ep.fecha_hora_ini_vel_max = None
                ep.vel_max_alcanzada = 0.0
                evento = _construir_evento(15, unidad, alerta, detalles)
                eventos.append(evento)
                _insertar_evento_bd(cur_telem, conn_telem, evento)
                _insertar_alerta_whatsapp_bd(
                    cur_main,
                    conn_main,
                    id_empresa,
                    "velocidad",
                    _construir_mensaje_whatsapp(evento),
                    fecha_hora_gps,
                )
                _marcar_cooldown(ep, 15, fecha_hora_gps)
                estado_persistir = True

    # ── Actualizar estado en cache y en BD si hubo cambio relevante ───────
    ep.dentro = new_in
    ep.ultima_fecha_gps = fecha_hora_gps

    if estado_persistir or cambio_estado:
        _persistir_estado_poi(
            cur_main=cur_main,
            conn_main=conn_main,
            id_poi=id_poi,
            id_unidad=id_unidad,
            id_empresa=id_empresa,
            ep=ep,
            fecha_gps=fecha_hora_gps,
        )

    return eventos


def _evaluar_velocidad_global(
    unidad: dict,
    vel_max: float,
    estado_unidad: EstadoUnidad,
    cur_main,
    conn_main,
    cur_telem,
    conn_telem,
) -> list[dict]:
    """
    Evalúa el exceso de velocidad global de una unidad (ev. 3/4).

    A diferencia de ev. 14/15 (velocidad en POI), este evaluador se aplica
    independientemente de si la unidad está dentro de un POI.

    Lógica basada en rules_speed.go del equipo (ApplySpeedRules):
      - Ev. 3: velocidad actual >= vel_max Y la velocidad está subiendo
               (trending up: vel_curr > vel_prev o no hay prev).
      - Ev. 4: estaba en exceso Y velocidad actual < vel_max.
      - Durante el exceso: actualizar vel_max_alcanzada en cache.

    Args:
        unidad:        Dict con la posición GPS actual.
        vel_max:       Velocidad máxima configurada para la unidad (km/h).
        estado_unidad: Estado global en memoria de la unidad.
        cur_main, conn_main: Cursor y conexión BD principal para t_alertas_whatsapp.
        cur_telem, conn_telem: Cursor y conexión para insertar en t_eventos.

    Returns:
        Lista de eventos generados (0, 1 o 2 eventos).
    """
    eventos: list[dict] = []
    vel_actual = float(unidad.get("velocidad") or 0)
    fecha_gps = unidad["fecha_hora_gps"]
    id_empresa = unidad.get("id_empresa")

    en_exceso = estado_unidad.fecha_hora_ini_exceso_vel is not None

    # ── INICIO DE EXCESO (ev. 3) ──────────────────────────────────────────
    if not en_exceso and vel_actual >= vel_max:
        # Solo emitir si la velocidad está subiendo (evita falsas alarmas
        # por oscilaciones del GPS en el umbral).
        trending_up = True
        # vel_prev: no la tenemos directamente, usamos el estado del ciclo anterior.
        # En el primer ciclo no hay prev → asumir trending_up = True.

        if trending_up:
            if _cooldown_ok_unidad(estado_unidad, "ev_3", fecha_gps):
                estado_unidad.fecha_hora_ini_exceso_vel = fecha_gps
                estado_unidad.odometro_ini_exceso_vel = float(
                    unidad.get("odometro") or 0
                )
                estado_unidad.vel_max_alcanzada_global = vel_actual

                evento = _construir_evento_global(
                    tipo_evento=3,
                    unidad=unidad,
                    detalles={
                        "velocidad_actual": vel_actual,
                        "vel_max": vel_max,
                    },
                )
                eventos.append(evento)
                _insertar_evento_bd(cur_telem, conn_telem, evento)
                if id_empresa:
                    _insertar_alerta_whatsapp_bd(
                        cur_main,
                        conn_main,
                        id_empresa,
                        "velocidad",
                        _construir_mensaje_whatsapp(evento),
                        fecha_gps,
                    )
                _marcar_cooldown_unidad(estado_unidad, "ev_3", fecha_gps)

    # ── DURANTE EL EXCESO: actualizar vel_max_alcanzada ───────────────────
    if en_exceso and vel_actual > estado_unidad.vel_max_alcanzada_global:
        estado_unidad.vel_max_alcanzada_global = vel_actual

    # ── FIN DE EXCESO (ev. 4) ─────────────────────────────────────────────
    if en_exceso and vel_actual < vel_max:
        if _cooldown_ok_unidad(estado_unidad, "ev_4", fecha_gps):
            ini = estado_unidad.fecha_hora_ini_exceso_vel
            dur_seg = int(
                (fecha_gps - ini).total_seconds() if isinstance(ini, datetime) else 0
            )
            odo_inicio = estado_unidad.odometro_ini_exceso_vel
            odo_actual = float(unidad.get("odometro") or 0)
            dist_km = max(0.0, (odo_actual - odo_inicio) / 1000.0)

            evento = _construir_evento_global(
                tipo_evento=4,
                unidad=unidad,
                detalles={
                    "vel_max_permitida": vel_max,
                    "vel_max_alcanzada": estado_unidad.vel_max_alcanzada_global,
                    "duracion_segundos": dur_seg,
                    "distancia_km": round(dist_km, 2),
                },
            )
            eventos.append(evento)
            _insertar_evento_bd(cur_telem, conn_telem, evento)
            if id_empresa:
                _insertar_alerta_whatsapp_bd(
                    cur_main,
                    conn_main,
                    id_empresa,
                    "velocidad",
                    _construir_mensaje_whatsapp(evento),
                    fecha_gps,
                )
            _marcar_cooldown_unidad(estado_unidad, "ev_4", fecha_gps)

        # Limpiar estado de exceso independientemente de si se emitió el evento
        estado_unidad.fecha_hora_ini_exceso_vel = None
        estado_unidad.odometro_ini_exceso_vel = 0.0
        estado_unidad.vel_max_alcanzada_global = 0.0

    return eventos


# ── Helpers de estado ─────────────────────────────────────────────────────────


def _cooldown_ok(ep: EstadoPOI, tipo_evento: int, ahora: datetime) -> bool:
    """
    Verifica si pasó suficiente tiempo desde el último evento del mismo tipo
    para este par (unidad, POI). Evita ráfagas de eventos duplicados.

    Returns True si puede emitir el evento.
    """
    clave = str(tipo_evento)
    ultimo = ep.ultimo_evento_at.get(clave)
    if ultimo is None:
        return True
    delta = (ahora - ultimo).total_seconds()
    return delta >= COOLDOWN_SECONDS


def _marcar_cooldown(ep: EstadoPOI, tipo_evento: int, ahora: datetime) -> None:
    """Registra el timestamp del último evento emitido para el cooldown."""
    ep.ultimo_evento_at[str(tipo_evento)] = ahora


def _cooldown_ok_unidad(
    estado: EstadoUnidad,
    clave: str,
    ahora: datetime,
) -> bool:
    """Mismo mecanismo de cooldown para eventos globales de la unidad."""
    ultimo = estado.ultimo_evento_at.get(clave)
    if ultimo is None:
        return True
    return (ahora - ultimo).total_seconds() >= COOLDOWN_SECONDS


def _marcar_cooldown_unidad(
    estado: EstadoUnidad,
    clave: str,
    ahora: datetime,
) -> None:
    estado.ultimo_evento_at[clave] = ahora


def _persistir_estado_poi(
    cur_main,
    conn_main,
    id_poi: int,
    id_unidad: int,
    id_empresa: int,
    ep: EstadoPOI,
    fecha_gps: datetime,
) -> None:
    """
    Persiste el estado del par (unidad, POI) en r_poi_unidades.
    Solo se llama cuando hubo un cambio de estado relevante.
    """
    try:
        cur_main.execute(
            _SQL_UPSERT_ESTADO,
            {
                "id_poi": id_poi,
                "id_unidad": id_unidad,
                "id_empresa": id_empresa,
                "in_actual": 1 if ep.dentro else 0,
                "fecha_hora_in": ep.fecha_hora_in,
                "fecha_hora_out": ep.fecha_hora_out,
                "fecha_hora_gps": fecha_gps,
                "alerta_permanencia": 1 if ep.alerta_permanencia_ok else 0,
                "fecha_hora_ini_vel_max": ep.fecha_hora_ini_vel_max,
                "vel_max_alcanzada": ep.vel_max_alcanzada or None,
            },
        )
        conn_main.commit()
    except Exception as exc:
        conn_main.rollback()
        logger.error(
            "Error persistiendo estado poi=%s unidad=%s: %s",
            id_poi,
            id_unidad,
            repr(exc),
        )


def _insertar_evento_bd(cur_telem, conn_telem, evento: dict) -> None:
    """Persiste el evento en t_eventos del servidor de telemetría."""
    try:
        payload_bd = {k: v for k, v in evento.items() if not k.startswith("_")}
        cur_telem.execute(_SQL_INSERT_EVENTO, payload_bd)
        conn_telem.commit()
    except Exception as exc:
        try:
            conn_telem.rollback()
        except Exception as rb_exc:
            # Si el rollback también falla, la conexión de telemetría se perdió.
            # El pool lazy la reemplazará en el siguiente acceso. Loggear en debug
            # para correlacionar con el error principal sin duplicar alertas.
            logger.debug(
                "Rollback telemetría falló al insertar t_eventos tipo=%s: %s",
                evento.get("evento"), repr(rb_exc)
            )
        logger.error(
            "Error insertando en t_eventos tipo=%s id_unidad=%s: %s",
            evento.get("evento"),
            evento.get("id_unidad"),
            repr(exc),
        )


def _construir_mensaje_whatsapp(evento: dict) -> str:
    """Construye un mensaje descriptivo para la alerta de WhatsApp."""
    num_unidad = evento.get("_numero_unidad") or evento.get("id_unidad")
    desc = evento.get("_descripcion") or f"Evento {evento.get('evento')}"
    nombre_poi = evento.get("_nombre_poi")

    if nombre_poi:
        return f"Unidad {num_unidad} - {desc}: {nombre_poi}"
    return f"Unidad {num_unidad} - {desc}"


def _insertar_alerta_whatsapp_bd(
    cur_main,
    conn_main,
    id_empresa: int,
    tipo_alerta: str,
    mensaje: str,
    fecha_evento: datetime,
) -> None:
    """
    Inserta el registro de alerta en t_alertas_whatsapp para todos los
    destinos de WhatsApp activos de la empresa.
    """
    if not id_empresa:
        return
    try:
        cur_main.execute(
            _SQL_INSERT_ALERTA_WHATSAPP,
            {
                "id_empresa": id_empresa,
                "tipo_alerta": tipo_alerta,
                "mensaje": mensaje,
                "fecha_evento": fecha_evento,
            },
        )
        conn_main.commit()
    except Exception as exc:
        try:
            conn_main.rollback()
        except Exception:
            pass
        logger.error(
            "Error insertando alerta WhatsApp empresa=%s: %s",
            id_empresa,
            repr(exc),
        )


def _construir_evento(
    tipo_evento: int,
    unidad: dict,
    alerta: dict,
    detalles: dict | None,
) -> dict:
    """Construye el dict canónico de un evento POI para BD y Redis."""
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
        # Campos privados para Redis (prefijo _ = no van a BD)
        "_numero_unidad": unidad["numero"],
        "_nombre_poi": alerta["poi_nombre"],
        "_descripcion": _descripcion_evento(tipo_evento),
        "_latitud": str(unidad.get("latitud", "")),
        "_longitud": str(unidad.get("longitud", "")),
        "_velocidad": str(unidad.get("velocidad", "")),
    }


def _construir_evento_global(
    tipo_evento: int,
    unidad: dict,
    detalles: dict | None,
) -> dict:
    """
    Construye el dict canónico de un evento global (sin POI) para BD y Redis.
    Para eventos 3 y 4 (velocidad global), id_elemento es NULL.
    """
    fecha_gps = unidad["fecha_hora_gps"]

    # id_empresa debe venir del SELECT de t_unidades (_SQL_UNIDADES_EMPRESA).
    # Si no viene, loggeamos un warning en lugar de petar con KeyError —
    # el worker sigue procesando otras unidades. Antes esto causaba un
    # crash catastrófico cada 15s en cuanto una unidad cruzaba su vel_max.
    id_empresa = unidad.get("id_empresa")
    if id_empresa is None:
        logger.warning(
            "Unidad sin id_empresa al construir evento global tipo=%s id_unidad=%s — "
            "revisa _SQL_UNIDADES_EMPRESA",
            tipo_evento,
            unidad.get("id_unidad"),
        )

    return {
        "id_data": unidad.get("id_data"),
        "id_empresa": id_empresa,
        "id_unidad": unidad["id_unidad"],
        "fecha": fecha_gps.date() if isinstance(fecha_gps, datetime) else None,
        "evento": tipo_evento,
        "id_elemento": None,  # ev. 3/4 no tienen POI asociado
        "fecha_hora_gmt": fecha_gps,
        "payload": json.dumps(detalles, default=str) if detalles else None,
        "_numero_unidad": unidad.get("numero", ""),
        "_nombre_poi": None,
        "_descripcion": _descripcion_evento(tipo_evento),
        "_latitud": str(unidad.get("latitud", "")),
        "_longitud": str(unidad.get("longitud", "")),
        "_velocidad": str(unidad.get("velocidad", "")),
    }


def _publicar_eventos_redis(id_empresa: int, eventos: list[dict]) -> None:
    """Publica cada evento en el canal Redis de la empresa."""
    try:
        r = _get_redis()
        canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
        for evento in eventos:
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
                "id_poi": evento.get("id_elemento"),
                "nombre_poi": evento.get("_nombre_poi"),
                "descripcion": evento["_descripcion"],
                "fecha_hora_evento": fecha_str,
                "latitud": evento.get("_latitud"),
                "longitud": evento.get("_longitud"),
                "velocidad": evento.get("_velocidad"),
            }
            r.publish(canal, json.dumps(payload_redis, default=str))
            logger.debug(
                "Redis canal=%s tipo=%s unidad=%s",
                canal,
                evento["evento"],
                evento["_numero_unidad"],
            )
    except redis.RedisError as exc:
        logger.warning(
            "Redis no disponible — eventos NO enviados en tiempo real: %s",
            repr(exc),
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
    """Retorna la descripción legible del tipo de evento."""
    return {
        3: "Inicio exceso de velocidad",
        4: "Fin exceso de velocidad",
        10: "Entró al POI",
        11: "Salió del POI",
        12: "Permanencia máxima excedida",
        13: "Permanencia mínima no cumplida",
        14: "Exceso de velocidad en POI inicio",
        15: "Exceso de velocidad en POI fin",
        19: "Paso por geocerca",
    }.get(tipo_evento, f"Evento {tipo_evento}")


# ── Scheduler ────────────────────────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def iniciar_worker() -> None:
    """Crea e inicia el scheduler. Llamar desde create_app() o gunicorn post_fork."""
    global _scheduler

    if os.getenv("WORKER_ENABLED", "true").lower() == "false":
        logger.info("POI Worker deshabilitado (WORKER_ENABLED=false).")
        return

    if _scheduler is not None and _scheduler.running:
        logger.warning("POI Worker ya está corriendo.")
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


def get_scheduler() -> BackgroundScheduler | None:
    """Expone el scheduler para que otros módulos registren jobs en él.

    Un solo BackgroundScheduler por proceso: dos schedulers separados
    bajo gevent compiten por el event loop y uno termina zombi
    (incidente 2026-06-12: POI colgado, luego Unit State colgado).
    """
    return _scheduler