"""
Protocolo (mensajes JSON):
  Cliente → servidor:
    {"type": "auth", "token": "<jwt>", "id_empresa": <int|null>}
    {"type": "ping"}
  Servidor → cliente:
    {"type": "connected", "empresa": <int>}
    {"type": "poi_event", ...}          (evento de geocerca)
    {"type": "unit_state_event", ...}   (alerta de estado de unidad)
    {"type": "pong"}
    {"type": "error", "message": "..."}
"""

import json
import logging
import time

import redis
from flask import Blueprint, request
from flask_sock import Sock

from utils.jwt_handler import decode_jwt
from workers.poi_worker import REDIS_CHANNEL_BASE, REDIS_URL

logger = logging.getLogger(__name__)

# Instancia de Sock — se inicializa con la app en create_app() vía init_app.
sock = Sock()

# Mismos intervalos que el SSE, para comportamiento consistente.
_HEARTBEAT_INTERVAL = 25
_REDIS_LISTEN_TIMEOUT = 20

# Tiempo máximo para recibir el mensaje de auth tras abrir la conexión.
_AUTH_TIMEOUT = 10


def init_ws(app):
    """Registra el WebSocket en la app. Llamar desde create_app()."""
    sock.init_app(app)


def _resolver_id_empresa(payload: dict, auth_msg: dict) -> int | None:
    """
    Resuelve el id_empresa igual que el SSE:
      1. id_empresa del JWT (usuarios normales y admin_empresa)
      2. id_empresa del mensaje auth (sudo_erp, cuyo JWT no tiene empresa fija)
    """
    id_empresa = payload.get("id_empresa")
    if id_empresa:
        return int(id_empresa)

    id_empresa_msg = auth_msg.get("id_empresa")
    if id_empresa_msg and str(id_empresa_msg).isdigit():
        return int(id_empresa_msg)

    return None


@sock.route("/events/ws")
def events_ws(ws):
    """
    WebSocket de eventos de la empresa.

    Flujo:
      1. Esperar el mensaje de auth con el JWT.
      2. Validar el token y resolver id_empresa.
      3. Suscribirse al canal Redis de la empresa.
      4. Loop: retransmitir mensajes de Redis al cliente; responder ping/pong;
         enviar heartbeat para mantener la conexión viva.
      5. Al desconectar, limpiar la suscripción Redis.
    """
    # ── 1. Autenticación: primer mensaje debe ser {type:"auth", token:...} ────
    try:
        raw = ws.receive(timeout=_AUTH_TIMEOUT)
    except Exception:
        raw = None

    if not raw:
        _safe_send(ws, {"type": "error", "message": "Auth requerida"})
        return

    try:
        auth_msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _safe_send(ws, {"type": "error", "message": "Mensaje de auth inválido"})
        return

    if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
        _safe_send(ws, {"type": "error", "message": "Se esperaba mensaje de auth"})
        return

    try:
        payload = decode_jwt(auth_msg["token"])
    except Exception:
        _safe_send(ws, {"type": "error", "message": "Token inválido o expirado"})
        return

    id_empresa = _resolver_id_empresa(payload, auth_msg)
    if not id_empresa:
        _safe_send(
            ws,
            {
                "type": "error",
                "message": "Empresa no especificada. sudo_erp debe enviar id_empresa.",
            },
        )
        return

    # ── 2. Suscripción a Redis y loop ─────────────────────────────────────────
    canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
    r_client = None
    pubsub = None

    try:
        r_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=_REDIS_LISTEN_TIMEOUT + 5,
        )
        pubsub = r_client.pubsub()
        pubsub.subscribe(canal)

        logger.info("Cliente WS conectado — empresa=%s canal=%s", id_empresa, canal)

        _safe_send(ws, {"type": "connected", "empresa": id_empresa})

        ultimo_heartbeat = time.monotonic()

        while True:
            # Leer mensajes pendientes de Redis (no bloqueante con timeout).
            mensaje = pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_REDIS_LISTEN_TIMEOUT,
            )

            if mensaje and mensaje.get("type") == "message":
                try:
                    data = json.loads(mensaje["data"])
                    # Mismo discriminador que el SSE: sse_event → type.
                    tipo = data.pop("sse_event", "poi_event")
                    data["type"] = tipo
                    _safe_send(ws, data)
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning(
                        "Mensaje Redis malformado empresa=%s: %s",
                        id_empresa,
                        repr(exc),
                    )

            # Heartbeat para mantener viva la conexión a través de proxies.
            ahora = time.monotonic()
            if ahora - ultimo_heartbeat >= _HEARTBEAT_INTERVAL:
                if not _safe_send(ws, {"type": "heartbeat"}):
                    break  # cliente desconectado
                ultimo_heartbeat = ahora

    except redis.RedisError as exc:
        logger.error("Error Redis en WS empresa=%s: %s", id_empresa, repr(exc))
        _safe_send(
            ws,
            {
                "type": "error",
                "message": "Conexión perdida con el servidor de eventos.",
            },
        )
    except Exception as exc:
        # Una desconexión del cliente típicamente cae aquí (ConnectionClosed).
        logger.info("WS finalizado empresa=%s: %s", id_empresa, repr(exc))
    finally:
        if pubsub:
            try:
                pubsub.unsubscribe(canal)
                pubsub.close()
            except Exception:
                pass
        if r_client:
            try:
                r_client.close()
            except Exception:
                pass


def _safe_send(ws, data: dict) -> bool:
    """
    Envía un dict como JSON por el WebSocket. Devuelve False si el cliente ya
    se desconectó (para cortar el loop sin lanzar excepción ruidosa).
    """
    try:
        ws.send(json.dumps(data))
        return True
    except Exception:
        return False
