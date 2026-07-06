import logging
import os

import psycopg2
from psycopg2 import pool as pg_pool

from config import Config

logger = logging.getLogger(__name__)


# Pool de BD principal — recursos locales, generoso.
_POOL_MIN_MAIN = int(os.getenv("MAIN_POOL_MIN", "2"))
_POOL_MAX_MAIN = int(os.getenv("MAIN_POOL_MAX", "20"))

# Pool de BD de telemetría — recursos remotos, escaso.
_POOL_MIN_TELEMETRY = int(os.getenv("TELEMETRY_POOL_MIN", "4"))
_POOL_MAX_TELEMETRY = int(os.getenv("TELEMETRY_POOL_MAX", "12"))

# Timeout de las queries en milisegundos. Se pasa al servidor remoto al abrir la conexión.
_STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "25000"))

_COMMON_KWARGS = {
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 60,
    "keepalives_interval": 10,
    "keepalives_count": 5,
    # -c pasa opciones de sesión al servidor al abrir la conexión.
    "options": f"-c statement_timeout={_STATEMENT_TIMEOUT_MS}",
}

# Cuántas veces intentamos conseguir una conexión VIVA del pool antes de
# rendirnos. Cubre el caso de varias conexiones muertas seguidas (server
# remoto que reinició, red intermitente) sin recrear el pool entero.
_GET_CONN_MAX_RETRIES = 3


def _make_main_pool():
    return pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN_MAIN,
        maxconn=_POOL_MAX_MAIN,
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT,
        **_COMMON_KWARGS,
    )


def _make_telemetry_pool():
    return pg_pool.ThreadedConnectionPool(
        minconn=_POOL_MIN_TELEMETRY,
        maxconn=_POOL_MAX_TELEMETRY,
        host=Config.TELEMETRY_DB_HOST,
        dbname=Config.TELEMETRY_DB_NAME,
        user=Config.TELEMETRY_DB_USER,
        password=Config.TELEMETRY_DB_PASSWORD,
        port=Config.TELEMETRY_DB_PORT,
        **_COMMON_KWARGS,
    )


# Inicialización de los pools
try:
    _main_pool = _make_main_pool()
    logger.info(
        "Pool BD principal iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN_MAIN,
        _POOL_MAX_MAIN,
        Config.DB_NAME,
    )
except Exception as exc:
    logger.critical("No se pudo crear el pool de BD principal: %s", repr(exc))
    raise

try:
    _telemetry_pool = _make_telemetry_pool()
    logger.info(
        "Pool BD telemetría iniciado (min=%s, max=%s, bd=%s)",
        _POOL_MIN_TELEMETRY,
        _POOL_MAX_TELEMETRY,
        Config.TELEMETRY_DB_NAME,
    )
except Exception as exc:
    _telemetry_pool = None
    logger.warning(
        "Pool BD telemetría NO disponible: %s — la API arranca sin telemetría. "
        "Los endpoints de mapa/posiciones devolverán error hasta que el "
        "servidor remoto sea accesible.",
        repr(exc),
    )


def _is_connection_alive(conn) -> bool:
    """
    Verifica que una conexión siga viva con una query mínima.

    Usa un cursor propio y lo cierra siempre (context manager), para no dejar
    cursores colgando durante la validación. Devuelve False si la conexión
    está cerrada o en estado de error.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def _get_conn_with_retry(pool):
    """
    Obtiene una conexión viva del pool indicado, reintentando si es necesario.
    """
    if pool is None:
        raise ConnectionError("El pool solicitado no está disponible.")

    ultimo_error = None

    for intento in range(1, _GET_CONN_MAX_RETRIES + 1):
        conn = pool.getconn()

        if _is_connection_alive(conn):
            return conn

        # Conexión muerta: la cerramos y la sacamos del pool (no la
        # reutilizamos). El pool abrirá una nueva la próxima vez que se pida.
        logger.warning(
            "Conexión muerta descartada del pool (intento %s/%s).",
            intento,
            _GET_CONN_MAX_RETRIES,
        )
        try:
            pool.putconn(conn, close=True)
        except Exception as exc:
            ultimo_error = exc

    # Si tras varios intentos no conseguimos una conexión viva, algo mayor
    # falla (server caído). Fallar con un error claro es mejor que devolver
    # una conexión rota que reventará más adelante de forma confusa.
    raise ConnectionError(
        "No se pudo obtener una conexión viva tras "
        f"{_GET_CONN_MAX_RETRIES} intentos. Último error: {ultimo_error!r}"
    )


# BD principal
def get_db_connection():
    """
    Obtiene una conexión VIVA del pool de BD principal.

    IMPORTANTE: devolver SIEMPRE con release_db_connection() en un finally,
    incluso si hubo excepción — si no, la conexión se fuga del pool.
    """
    return _get_conn_with_retry(_main_pool)


def release_db_connection(conn) -> None:
    """Devuelve una conexión al pool de BD principal. Llamar en el finally."""
    if conn:
        _main_pool.putconn(conn)


# BD telemetría
def get_db_telemetry_connection():
    """
    Obtiene una conexión VIVA del pool de telemetría.

    Si la telemetría no está disponible (pool None por fallo al iniciar),
    lanza un error descriptivo en vez de tumbar la API.
    """
    if _telemetry_pool is None:
        raise ConnectionError(
            "La BD de telemetría no está disponible. "
            "El servidor remoto no respondió al iniciar la API."
        )
    return _get_conn_with_retry(_telemetry_pool)


def release_db_telemetry_connection(conn) -> None:
    """Devuelve una conexión al pool de telemetría. Llamar en el finally."""
    if conn and _telemetry_pool is not None:
        _telemetry_pool.putconn(conn)
