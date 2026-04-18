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

# ── Parámetros TCP keepalive ───────────────────────────────────────────────────
# Cuando la app está en stand-by, PostgreSQL puede cerrar las conexiones
# inactivas por timeout. Con keepalive, el SO envía paquetes periódicos para
# mantener la conexión viva y detectar fallos antes de que el pool la use.
#
#   keepalives_idle    → segundos de inactividad antes del primer keepalive
#   keepalives_interval → segundos entre reintentos si no hay respuesta
#   keepalives_count   → intentos antes de declarar la conexión muerta
_KEEPALIVE_KWARGS = {
    "keepalives": 1,
    "keepalives_idle": 60,       # primer ping tras 60s inactiva
    "keepalives_interval": 10,   # reintento cada 10s
    "keepalives_count": 5,       # 5 intentos antes de cerrar
}


def _make_main_pool():
    return pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN,
        maxconn=_POOL_MAX,
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT,
        connect_timeout=10,
        **_KEEPALIVE_KWARGS,
    )


def _make_telemetry_pool():
    return pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN,
        maxconn=_POOL_MAX,
        host=Config.TELEMETRY_DB_HOST,
        dbname=Config.TELEMETRY_DB_NAME,
        user=Config.TELEMETRY_DB_USER,
        password=Config.TELEMETRY_DB_PASSWORD,
        port=Config.TELEMETRY_DB_PORT,
        connect_timeout=10,
        **_KEEPALIVE_KWARGS,
    )


# ── Pool de la base de datos principal ────────────────────────────────────────
try:
    _main_pool = _make_main_pool()
    logger.info(
        "Pool BD principal iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN, _POOL_MAX, Config.DB_NAME,
    )
except Exception as exc:
    logger.critical("No se pudo crear el pool de BD principal: %s", repr(exc))
    raise

# ── Pool de la base de datos de telemetría ─────────────────────────────────────
try:
    _telemetry_pool = _make_telemetry_pool()
    logger.info(
        "Pool BD telemetría iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN, _POOL_MAX, Config.TELEMETRY_DB_NAME,
    )
except Exception as exc:
    logger.critical("No se pudo crear el pool de BD telemetría: %s", repr(exc))
    raise


def _is_connection_alive(conn) -> bool:
    """
    Verifica si una conexión sigue activa enviando una query ligera.
    Retorna False si la conexión está cerrada o en estado de error.
    """
    try:
        conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


def _get_conn_with_retry(pool, make_pool_fn, pool_attr: str):
    """
    Obtiene una conexión del pool validando que esté viva.
    Si está muerta (OperationalError por timeout de servidor):
      1. La descarta del pool
      2. Recrea el pool completo
      3. Retorna una conexión nueva

    pool_attr: nombre del atributo global (_main_pool o _telemetry_pool)
    """
    global _main_pool, _telemetry_pool

    conn = pool.getconn()

    # Verificar si la conexión sigue viva
    if not _is_connection_alive(conn):
        logger.warning(
            "Conexión muerta detectada en el pool — recreando pool..."
        )
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass

        # Recrear el pool completo
        new_pool = make_pool_fn()
        if pool_attr == "main":
            _main_pool = new_pool
        else:
            _telemetry_pool = new_pool

        conn = new_pool.getconn()

    return conn


def get_db_connection():
    """
    Obtiene una conexión del pool de BD principal.
    Valida automáticamente que la conexión esté viva — si no lo está,
    recrea el pool y retorna una conexión nueva.

    IMPORTANTE: siempre devolver con release_db_connection() en un finally.
    """
    return _get_conn_with_retry(_main_pool, _make_main_pool, "main")


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
    return _get_conn_with_retry(_telemetry_pool, _make_telemetry_pool, "telemetry")


def release_db_telemetry_connection(conn) -> None:
    """
    Devuelve una conexión al pool de BD de telemetría.
    """
    if conn:
        _telemetry_pool.putconn(conn)