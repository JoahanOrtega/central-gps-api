import logging
import psycopg2
from psycopg2 import pool as pg_pool
from config import Config

logger = logging.getLogger(__name__)

# ── Tamaños del pool ───────────────────────────────────────────────────────────
# minconn: conexiones abiertas al arrancar — listas para usar de inmediato.
# maxconn: tope máximo de conexiones simultáneas.
#
# Ajustar según la carga esperada y los límites de PostgreSQL.
# Regla general: maxconn ≤ max_connections de PostgreSQL / número de workers.
# Con gunicorn -w 4 y max_connections=100 → 100 / 4 = 25 por worker.
_POOL_MIN = 2
_POOL_MAX = 20

# ── Pool de la base de datos principal ────────────────────────────────────────
# ThreadedConnectionPool es thread-safe — necesario con gunicorn en modo sync.
# Si se usan workers async (gevent/eventlet), migrar a psycogreen o asyncpg.
try:
    _main_pool = pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN,
        maxconn=_POOL_MAX,
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT,
        # Timeout de conexión: si PostgreSQL no responde en 10s, fallar rápido
        connect_timeout=10,
    )
    logger.info(
        "Pool BD principal iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN,
        _POOL_MAX,
        Config.DB_NAME,
    )
except Exception as exc:
    logger.critical("No se pudo crear el pool de BD principal: %s", repr(exc))
    raise

# ── Pool de la base de datos de telemetría ─────────────────────────────────────
# Pool separado porque la BD de telemetría puede estar en un servidor distinto
# y tiene un patrón de acceso de alta frecuencia (polling cada 15s).
try:
    _telemetry_pool = pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN,
        maxconn=_POOL_MAX,
        host=Config.TELEMETRY_DB_HOST,
        dbname=Config.TELEMETRY_DB_NAME,
        user=Config.TELEMETRY_DB_USER,
        password=Config.TELEMETRY_DB_PASSWORD,
        port=Config.TELEMETRY_DB_PORT,
        connect_timeout=10,
    )
    logger.info(
        "Pool BD telemetría iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN,
        _POOL_MAX,
        Config.TELEMETRY_DB_NAME,
    )
except Exception as exc:
    logger.critical("No se pudo crear el pool de BD telemetría: %s", repr(exc))
    raise


def get_db_connection():
    """
    Obtiene una conexión del pool de BD principal.

    IMPORTANTE: siempre usar dentro de un bloque try/finally para garantizar
    que la conexión se devuelve al pool con release_db_connection().

    Uso recomendado — gestor de contexto:
        with managed_db_connection() as (conn, cursor):
            cursor.execute(...)

    Uso manual:
        conn = get_db_connection()
        try:
            ...
        finally:
            release_db_connection(conn)
    """
    return _main_pool.getconn()


def release_db_connection(conn) -> None:
    """
    Devuelve una conexión al pool de BD principal.
    Llamar siempre en el bloque finally del código que llamó get_db_connection().
    """
    if conn:
        _main_pool.putconn(conn)


def get_db_telemetry_connection():
    """
    Obtiene una conexión del pool de BD de telemetría.
    Mismas reglas que get_db_connection().
    """
    return _telemetry_pool.getconn()


def release_db_telemetry_connection(conn) -> None:
    """
    Devuelve una conexión al pool de BD de telemetría.
    """
    if conn:
        _telemetry_pool.putconn(conn)
