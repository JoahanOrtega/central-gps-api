import logging
import os
import threading
import time

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

# El pool de telemetría se crea al primer uso, porque es remoto y escaso.
_telemetry_pool = None
logger.info(
    "Pool BD telemetría diferido al primer uso (min=%s, max=%s, bd=%s)",
    _POOL_MIN_TELEMETRY,
    _POOL_MAX_TELEMETRY,
    Config.TELEMETRY_DB_NAME,
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


# Registro de la última vez que una conexión fue usada con éxito. Se usa para
# evitar pagar un SELECT 1 extra si la conexión se usó hace poco.
_ULTIMO_USO_OK: dict[int, float] = {}
_VERIFICACION_TTL_S = int(os.getenv("DB_ALIVE_CHECK_TTL_S", "60"))


def _marcar_uso_ok(conn) -> None:
    """Registra que la conexión acaba de usarse con éxito."""
    _ULTIMO_USO_OK[id(conn)] = time.monotonic()


def _olvidar_conexion(conn) -> None:
    """Elimina el registro de una conexión que sale del pool (destruida)."""
    _ULTIMO_USO_OK.pop(id(conn), None)


def _requiere_verificacion(conn) -> bool:
    ultimo = _ULTIMO_USO_OK.get(id(conn))
    return ultimo is None or (time.monotonic() - ultimo) > _VERIFICACION_TTL_S


def _get_conn_with_retry(pool):
    """
    Obtiene una conexión viva del pool indicado, reintentando si es necesario.
    """
    if pool is None:
        raise ConnectionError("El pool solicitado no está disponible.")

    ultimo_error = None

    for intento in range(1, _GET_CONN_MAX_RETRIES + 1):
        conn = pool.getconn()

        # Si la conexión se usó hace poco, asumimos que sigue viva y la devolvemos
        if not _requiere_verificacion(conn):
            return conn

        if _is_connection_alive(conn):
            _marcar_uso_ok(conn)
            return conn

        # Conexión muerta: la cerramos y la sacamos del pool (no la
        # reutilizamos). El pool abrirá una nueva la próxima vez que se pida.
        _olvidar_conexion(conn)
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
        # La conexión regresa tras usarse: renueva el TTL de verificación
        # sin pagar un SELECT 1 extra.
        if conn.closed == 0:
            _marcar_uso_ok(conn)
        else:
            _olvidar_conexion(conn)
        _main_pool.putconn(conn)


# Telemetry pool (BD remota, escasa)
_telemetry_pool_lock = threading.Lock()
_telemetry_retry_at = 0.0  # monotonic — próximo instante permitido para reintentar
_TELEMETRY_RETRY_COOLDOWN_S = int(os.getenv("TELEMETRY_POOL_RETRY_COOLDOWN_S", "30"))


def _ensure_telemetry_pool():
    """
    Asegura que el pool de telemetría esté disponible, reintentando si es necesario.
    """
    global _telemetry_pool, _telemetry_retry_at

    # Chequeo rápido sin lock: si ya existe, devolvemos. La mayoría de las
    # requests no necesitan bloquearse.
    if _telemetry_pool is not None:
        return _telemetry_pool

    with _telemetry_pool_lock:
        # Rechequeo tras adquirir el lock: otro thread pudo haberlo creado mientras
        if _telemetry_pool is not None:
            return _telemetry_pool

        ahora = time.monotonic()
        if ahora < _telemetry_retry_at:
            raise ConnectionError(
                "La BD de telemetría no está disponible. "
                "Próximo reintento de conexión en "
                f"{int(_telemetry_retry_at - ahora)}s."
            )

        try:
            _telemetry_pool = _make_telemetry_pool()
            logger.info(
                "Pool BD telemetría recreado en caliente (min=%s, max=%s)",
                _POOL_MIN_TELEMETRY,
                _POOL_MAX_TELEMETRY,
            )
            return _telemetry_pool
        except Exception as exc:
            _telemetry_retry_at = ahora + _TELEMETRY_RETRY_COOLDOWN_S
            logger.warning(
                "Reintento de pool de telemetría falló: %s — siguiente "
                "intento en %ss.",
                repr(exc),
                _TELEMETRY_RETRY_COOLDOWN_S,
            )
            raise ConnectionError(
                f"La BD de telemetría no está disponible: {exc!r}"
            ) from exc


def get_db_telemetry_connection():
    """
    Obtiene una conexión VIVA del pool de telemetría.
    """
    pool = _ensure_telemetry_pool()
    return _get_conn_with_retry(pool)


def release_db_telemetry_connection(conn) -> None:
    """Devuelve una conexión al pool de telemetría. Llamar en el finally."""
    if conn and _telemetry_pool is not None:
        if conn.closed == 0:
            _marcar_uso_ok(conn)
        else:
            _olvidar_conexion(conn)
        _telemetry_pool.putconn(conn)