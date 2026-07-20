"""
Health check con verificación real de dependencias.

¿Por qué no basta el "/" que ya existía?
─────────────────────────────────────────
El endpoint "/" responde 200 siempre que el proceso Flask esté vivo, aunque
la BD principal esté inalcanzable o Redis caído. Los orquestadores
(Docker/Podman healthcheck) leían ese 200 y daban el contenedor por sano
cuando en realidad todas las peticiones reales fallaban.

Semántica de estados (patrón readiness estándar):
  - "ok"       → BD principal + Redis vivos. HTTP 200.
  - "degraded" → BD y Redis vivos, pero telemetría (VM remota GCP) caída.
                 HTTP 200 igualmente: la app sigue siendo útil (catálogos,
                 usuarios, permisos) y reiniciar el contenedor NO arregla
                 una VM remota — sería un restart-loop inútil.
  - "error"    → BD principal o Redis caídos. HTTP 503: aquí sí el
                 orquestador debe actuar (el contenedor no puede operar).

Cada componente reporta su latencia en ms para diagnóstico rápido:
un "ok" con 4000ms en telemetría ya avisa de la degradación antes
de que se convierta en timeout.
"""

import logging
import time

import redis
from flask import Blueprint, jsonify

from config import Config
from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection,
)
from utils.limiter import limiter

logger = logging.getLogger(__name__)

health_bp = Blueprint("health", __name__, url_prefix="/health")


def _check_db_principal() -> dict:
    """
    Verifica la BD principal con un SELECT 1 real a través del pool.

    Usamos el pool (no una conexión nueva) a propósito: si el pool está
    agotado o sus conexiones muertas, ESO es lo que sufren los requests
    reales — el health check debe medir la misma ruta que usa la app.
    """
    inicio = time.monotonic()
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "latencia_ms": _ms_desde(inicio)}
    except Exception as exc:
        logger.error("Health: BD principal caída: %s", repr(exc))
        return {"status": "error", "latencia_ms": _ms_desde(inicio)}
    finally:
        if conn is not None:
            release_db_connection(conn)


def _check_redis() -> dict:
    """
    Verifica Redis con un PING.

    Cliente propio con timeouts de 2s (no el del worker) para que un Redis
    colgado no bloquee el health check más allá de 2s. Redis es crítico:
    sin él, el rate limiter y el canal de eventos WS/SSE no funcionan.
    """
    inicio = time.monotonic()
    try:
        cliente = redis.from_url(
            Config.REDIS_URL,
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        cliente.ping()
        return {"status": "ok", "latencia_ms": _ms_desde(inicio)}
    except Exception as exc:
        logger.error("Health: Redis caído: %s", repr(exc))
        return {"status": "error", "latencia_ms": _ms_desde(inicio)}
    finally:
        # from_url crea un pool interno; lo cerramos para no acumular
        # conexiones huérfanas en cada ping del orquestador.
        try:
            cliente.close()
        except Exception:
            pass


def _check_telemetria() -> dict:
    """
    Verifica la BD de telemetría (TimescaleDB en la VM remota).

    Su caída degrada pero NO tumba el servicio — por eso su fallo
    nunca produce 503. El pool de telemetría es lazy: si nunca se ha
    usado, este check paga la creación inicial (hasta ~5s de
    connect_timeout la primera vez), lo cual es aceptable y además
    "calienta" el pool para el primer request real.
    """
    inicio = time.monotonic()
    conn = None
    try:
        conn = get_db_telemetry_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "ok", "latencia_ms": _ms_desde(inicio)}
    except Exception as exc:
        # warning y no error: es una degradación esperada (VM remota,
        # NAT de GCP), no una falla del contenedor.
        logger.warning("Health: telemetría inalcanzable: %s", repr(exc))
        return {"status": "error", "latencia_ms": _ms_desde(inicio)}
    finally:
        if conn is not None:
            release_db_telemetry_connection(conn)


def _ms_desde(inicio: float) -> int:
    """Milisegundos transcurridos desde `inicio` (time.monotonic)."""
    return int((time.monotonic() - inicio) * 1000)


@health_bp.route("", methods=["GET"])
@limiter.exempt
def health():
    """
    Readiness probe: verifica todas las dependencias.

    Exento del rate limiter: los orquestadores hacen ping cada pocos
    segundos y consumirían el bucket global de la IP interna.

    Respuesta:
        200 {"status": "ok"|"degraded", "componentes": {...}}
        503 {"status": "error", "componentes": {...}}
    """
    componentes = {
        "bd_principal": _check_db_principal(),
        "redis": _check_redis(),
        "telemetria": _check_telemetria(),
    }

    # BD principal y Redis son críticos; telemetría solo degrada.
    critico_caido = (
        componentes["bd_principal"]["status"] != "ok"
        or componentes["redis"]["status"] != "ok"
    )

    if critico_caido:
        estado, codigo = "error", 503
    elif componentes["telemetria"]["status"] != "ok":
        estado, codigo = "degraded", 200
    else:
        estado, codigo = "ok", 200

    return jsonify({"status": estado, "componentes": componentes}), codigo


@health_bp.route("/live", methods=["GET"])
@limiter.exempt
def liveness():
    """
    Liveness probe: solo confirma que el proceso responde.

    Separado del readiness a propósito: si se usara /health como liveness
    y la BD tardara en volver, el orquestador mataría el contenedor en
    bucle sin darle oportunidad de reconectar. /health/live nunca falla
    mientras Flask viva.
    """
    return jsonify({"status": "ok"}), 200