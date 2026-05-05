"""
Responsabilidad:
  Exponer el endpoint GET /events/stream que mantiene una conexión HTTP
  abierta y envía eventos de geocerca al frontend a medida que ocurren,
  usando el protocolo Server-Sent Events (SSE).

Por qué SSE y no WebSocket:
  - SSE es unidireccional (servidor → cliente). Para notificaciones de
    geocerca, el frontend solo necesita RECIBIR — nunca enviar datos
    por este canal.
  - SSE funciona sobre HTTP/1.1 estándar sin upgrade de protocolo.
  - El navegador reconecta automáticamente si la conexión se cae
    (con el campo `retry` del protocolo SSE).
  - No requiere librerías adicionales en el cliente — EventSource nativo.

Protocolo SSE:
  Cada mensaje tiene el formato:
    event: poi_event\n
    data: {"tipo_evento": 10, "nombre_poi": "...", ...}\n
    id: {timestamp}\n
    \n
  El campo `event` permite al frontend filtrar por tipo con
  addEventListener("poi_event", handler).

Flujo por conexión:
  1. El frontend abre GET /events/stream con el JWT en el header.
  2. El endpoint valida el JWT y extrae id_empresa.
  3. Suscribe a Redis canal "eventos_poi:{id_empresa}".
  4. Envía un heartbeat cada 25s para mantener la conexión viva
     (los proxies/load balancers suelen cerrar conexiones idle > 30s).
  5. Cuando Redis publica un evento, lo retransmite al cliente como SSE.
  6. Si el cliente desconecta, el generador termina y el thread se libera.

Consideraciones de concurrencia:
  - Cada cliente conectado tiene su propio thread de generador.
  - Redis pub/sub crea un nuevo cliente por conexión SSE — no se comparte
    el cliente Redis del worker para evitar conflictos de estado.
  - En producción con múltiples workers de Gunicorn, cada worker tiene
    sus propias conexiones SSE — Redis pub/sub garantiza que TODOS
    reciben los eventos.

Limitaciones conocidas:
  - Flask dev server (werkzeug) no soporta streaming con múltiples threads
    bien — usar `FLASK_DEBUG=false` o Gunicorn en producción.
  - Gunicorn necesita `--worker-class gevent` o `--worker-class gthread`
    para SSE. Workers síncronos bloquean el proceso.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Generator

import redis
from flask import Blueprint, Response, request, stream_with_context

from utils.jwt_handler import decode_jwt
from workers.poi_worker import REDIS_CHANNEL_BASE, REDIS_URL

logger = logging.getLogger(__name__)

events_bp = Blueprint("events", __name__, url_prefix="/events")

# Segundos entre heartbeats — mantiene la conexión viva a través de proxies.
# La mayoría de proxies tiene un timeout de idle de 30-60s.
_HEARTBEAT_INTERVAL = 25

# Timeout de bloqueo en Redis listen — cuánto esperar un mensaje antes de
# enviar el heartbeat. Debe ser < _HEARTBEAT_INTERVAL para que el heartbeat
# llegue a tiempo.
_REDIS_LISTEN_TIMEOUT = 20


# ── Endpoint SSE ──────────────────────────────────────────────────────────────


@events_bp.route("/stream")
def stream_eventos():
    """
    GET /events/stream

    Abre una conexión SSE y retransmite eventos de geocerca de la empresa
    del usuario autenticado.

    Autenticación:
      JWT en el header Authorization: Bearer <token>
      (No se usa cookie porque EventSource del navegador no permite
       headers custom — se pasa el token como query param ?token=<jwt>
       o en el header desde el cliente con fetch + ReadableStream si
       el navegador lo soporta. Ver nota en el frontend hook.)

    Query params:
      token      (str, requerido): JWT del usuario.
                                   EventSource nativo no soporta headers custom,
                                   así que el token va en la URL como ?token=...
      id_empresa (int, opcional):  Requerido solo para sudo_erp, cuyo JWT no
                                   tiene id_empresa fijo. El frontend lo pasa
                                   desde companyStore.currentCompany.id_empresa.

    Respuesta:
      Content-Type: text/event-stream
      Cache-Control: no-cache
      X-Accel-Buffering: no  (desactiva el buffer de nginx)

    Errores:
      401 → token inválido, expirado o faltante (respuesta JSON, no SSE)
      403 → usuario sin empresa asignada y sin ?id_empresa en la URL
    """
    # ── Validar JWT desde query param (EventSource no admite headers) ─────────
    token = request.args.get("token", "").strip()
    if not token:
        return {"error": "Token requerido"}, 401

    try:
        payload = decode_jwt(token)
    except Exception:
        return {"error": "Token inválido o expirado"}, 401

    # ── Resolver id_empresa ───────────────────────────────────────────────────
    # Prioridad:
    #   1. id_empresa del JWT (usuarios normales y admin_empresa)
    #   2. ?id_empresa en la URL (sudo_erp — su JWT no tiene empresa fija)
    id_empresa: int | None = payload.get("id_empresa")

    if not id_empresa:
        # sudo_erp: leer el parámetro de la URL
        id_empresa_param = request.args.get("id_empresa", "").strip()
        if id_empresa_param and id_empresa_param.isdigit():
            id_empresa = int(id_empresa_param)

    if not id_empresa:
        return {
            "error": "Empresa no especificada. "
            "Para sudo_erp incluir ?id_empresa=<id> en la URL."
        }, 403

    # ── Retornar la respuesta de streaming ────────────────────────────────────
    return Response(
        stream_with_context(_generar_eventos_sse(id_empresa)),
        mimetype="text/event-stream",
        headers={
            # Desactiva cualquier buffer intermedio (nginx, proxies, etc.)
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            # Permite que el navegador reconecte automáticamente cada 3s
            # si la conexión se interrumpe inesperadamente.
            "Connection": "keep-alive",
        },
    )


# ── Generador SSE ─────────────────────────────────────────────────────────────


def _generar_eventos_sse(id_empresa: int) -> Generator[str, None, None]:
    """
    Generador Python que produce mensajes SSE para la empresa dada.

    Flujo:
      1. Conectar a Redis y suscribirse al canal de la empresa.
      2. Enviar mensaje de conexión exitosa.
      3. Loop: esperar mensaje de Redis o enviar heartbeat.
      4. Al desconectar el cliente, cerrar el cliente Redis.

    Args:
        id_empresa: ID de la empresa del usuario autenticado.

    Yields:
        Strings con formato SSE: "event: ...\ndata: ...\nid: ...\n\n"
    """
    canal = f"{REDIS_CHANNEL_BASE}:{id_empresa}"
    r_client = None
    pubsub = None

    try:
        # ── Conectar a Redis y suscribirse ─────────────────────────────────────
        # Nuevo cliente Redis POR CONEXIÓN — no compartir con el worker
        # para evitar conflictos en el estado del pub/sub.
        r_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_timeout=_REDIS_LISTEN_TIMEOUT + 5,
        )
        pubsub = r_client.pubsub()
        pubsub.subscribe(canal)

        logger.info(
            "Cliente SSE conectado — empresa=%s canal=%s",
            id_empresa,
            canal,
        )

        # ── Mensaje de conexión exitosa ────────────────────────────────────────
        # Confirma al frontend que el stream está activo.
        # El hook usePoiEvents() lo usa para actualizar el estado "conectado".
        yield _formatear_sse(
            evento="connected",
            data={
                "message": f"Conectado — empresa {id_empresa}",
                "empresa": id_empresa,
            },
        )

        ultimo_heartbeat = time.monotonic()

        # ── Loop principal ─────────────────────────────────────────────────────
        while True:

            # Leer mensajes pendientes de Redis (no bloqueante con timeout)
            mensaje = pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_REDIS_LISTEN_TIMEOUT,
            )

            if mensaje and mensaje.get("type") == "message":
                # Mensaje real de geocerca — retransmitir al cliente
                try:
                    data = json.loads(mensaje["data"])
                    yield _formatear_sse(
                        evento="poi_event",
                        data=data,
                        event_id=str(int(time.time() * 1000)),
                    )
                    logger.debug(
                        "SSE enviado empresa=%s tipo=%s poi=%s",
                        id_empresa,
                        data.get("tipo_evento"),
                        data.get("nombre_poi"),
                    )
                except (json.JSONDecodeError, KeyError) as exc:
                    logger.warning(
                        "Mensaje Redis malformado empresa=%s: %s",
                        id_empresa,
                        repr(exc),
                    )

            # Heartbeat: si pasaron > _HEARTBEAT_INTERVAL sin mensajes,
            # enviar un comentario SSE para mantener la conexión viva.
            ahora = time.monotonic()
            if ahora - ultimo_heartbeat >= _HEARTBEAT_INTERVAL:
                yield ": heartbeat\n\n"
                ultimo_heartbeat = ahora

    except GeneratorExit:
        # El cliente se desconectó — limpieza normal
        logger.info("Cliente SSE desconectado — empresa=%s", id_empresa)

    except redis.RedisError as exc:
        # Redis caído — notificar al cliente para que reintente
        logger.error("Error Redis en SSE empresa=%s: %s", id_empresa, repr(exc))
        yield _formatear_sse(
            evento="error",
            data={
                "message": "Conexión perdida con el servidor de eventos. Reintentando..."
            },
        )

    except Exception as exc:
        logger.error(
            "Error inesperado en SSE empresa=%s: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )

    finally:
        # Siempre limpiar la suscripción Redis al desconectar
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


# ── Helpers SSE ───────────────────────────────────────────────────────────────


def _formatear_sse(
    evento: str,
    data: dict,
    event_id: str | None = None,
) -> str:
    """
    Formatea un dict como mensaje SSE según la especificación W3C.

    Formato de salida:
        event: {evento}\n
        data: {json}\n
        id: {event_id}\n   (opcional)
        \n

    Args:
        evento:   Nombre del evento SSE (ej: "poi_event", "connected", "error").
        data:     Dict que se serializa como JSON en el campo data.
        event_id: ID único del evento (timestamp en ms). Permite al cliente
                  solicitar eventos perdidos con Last-Event-ID al reconectar.

    Returns:
        String con el mensaje SSE correctamente formateado.
    """
    lineas = [
        f"event: {evento}",
        f"data: {json.dumps(data, default=str)}",
    ]
    if event_id:
        lineas.append(f"id: {event_id}")
    # Línea vacía final — requerida por la especificación SSE para delimitar mensajes
    lineas.append("")
    lineas.append("")
    return "\n".join(lineas)
