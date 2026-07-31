# Responsabilidad ÚNICA: dado un chatid de grupo y un mensaje, entregarlo a
# Evolution API y reportar el resultado.
#
# Usa `requests` (NO urllib) A PROPÓSITO: el proyecto corre bajo gunicorn+gevent
# con monkey.patch_all(), que parcha los sockets de `requests` para que cedan el
# control del event loop. urllib NO es parcheado por gevent, y sus llamadas
# bloqueantes CONGELAN el scheduler tras el primer ciclo (los jobs dejan de
# correr hasta reiniciar). Por eso aquí la dependencia `requests` está
# justificada — no es opcional en este entorno.
#
# Evolution API es un gateway NO OFICIAL (protocolo de WhatsApp Web vía Baileys).
#
# Prueba manual (con la API corriendo y la instancia conectada):
#   python -m services.whatsapp_service "12036XXXXXXXXX@g.us" "mensaje de prueba"

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# ── Configuración (sobrescribible por variables de entorno) ───────────────────

# URL base de Evolution API (sin diagonal final). En dev con docker-compose
# los contenedores se ven entre sí por nombre de servicio.
EVOLUTION_URL: str = os.getenv("EVOLUTION_API_URL", "http://evolution:8080").rstrip("/")

# API key global de Evolution (la misma que AUTHENTICATION_API_KEY del
# contenedor). SIN default: si falta, el servicio se niega a operar.
EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "")

# Nombre de la instancia (un número de WhatsApp vinculado = una instancia).
# A futuro, para multi-empresa, esto se resuelve por empresa; hoy una sola.
EVOLUTION_INSTANCE: str = os.getenv("EVOLUTION_INSTANCE", "centralgps")

# Timeout (conexión, lectura) en segundos. El envío es asíncrono del lado de
# Evolution, así que una respuesta lenta ya es señal de problema.
_TIMEOUT = (5, 20)


def enviar_a_grupo(chatid: str, mensaje: str) -> tuple[bool, str | None]:
    """
    Envía un mensaje de texto a un grupo de WhatsApp vía Evolution API.

    Args:
        chatid: Identificador del grupo (formato '1203...@g.us'), tal como
            está almacenado en t_grupos_whatsapp.chatid.
        mensaje: Texto a enviar. Soporta el formato de WhatsApp
            (*negritas*, _cursivas_) que ya usan los mensajes del legacy.

    Returns:
        (True, None) si Evolution aceptó el mensaje.
        (False, detalle) si falló — el detalle es apto para log/diagnóstico,
        nunca para mostrarse al usuario final.
    """
    if not EVOLUTION_API_KEY:
        return False, "EVOLUTION_API_KEY no configurada en el entorno"

    try:
        resp = requests.post(
            f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}",
            json={"number": chatid, "text": mensaje},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        # Red caída, contenedor apagado, timeout: el worker reintentará.
        return False, f"Error de conexión con Evolution: {repr(exc)}"

    # Evolution responde 200/201 cuando encola el mensaje correctamente.
    if resp.status_code in (200, 201):
        return True, None

    # 4xx típicos: instancia desconectada (reescanear QR), chatid inválido,
    # o apikey mala. Se registra el cuerpo truncado para diagnóstico.
    return False, f"HTTP {resp.status_code}: {resp.text[:300]}"


def instancia_conectada() -> bool:
    """
    Verifica si la instancia tiene sesión activa de WhatsApp.

    Útil para no quemar ciclos del worker cuando el número está
    desvinculado (estado 'close'): sin sesión, TODO envío fallará.

    Returns:
        True si el estado de conexión es 'open'; False en cualquier
        otro caso, incluidos errores de red (pesimismo deliberado).
    """
    try:
        resp = requests.get(
            f"{EVOLUTION_URL}/instance/connectionState/{EVOLUTION_INSTANCE}",
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            # Respuesta esperada: {"instance": {"instanceName": ..., "state": "open"}}
            return resp.json().get("instance", {}).get("state") == "open"
    except (requests.RequestException, ValueError) as exc:
        logger.warning("No se pudo consultar el estado de la instancia: %s", repr(exc))
    return False


def listar_grupos() -> list[dict]:
    """
    Trae todos los grupos donde el número vinculado es miembro (Evolution
    fetchAllGroups). Cada grupo trae su chatid y nombre.

    Returns:
        Lista de dicts [{ "chatid": "...@g.us", "nombre": "..." }].
        Lista vacía si falla o no hay grupos.
    """
    try:
        resp = requests.get(
            f"{EVOLUTION_URL}/group/fetchAllGroups/{EVOLUTION_INSTANCE}",
            params={"getParticipants": "false"},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.warning("fetchAllGroups devolvió HTTP %s", resp.status_code)
            return []
        # Evolution devuelve una lista de grupos con 'id' (chatid) y 'subject'.
        return [
            {"chatid": g.get("id"), "nombre": g.get("subject", "")}
            for g in resp.json()
            if g.get("id")
        ]
    except (requests.RequestException, ValueError) as exc:
        logger.warning("No se pudieron listar grupos de Evolution: %s", repr(exc))
        return []


def crear_grupo(nombre: str, participantes: list[str]) -> tuple[str | None, str | None]:
    """
    Crea un grupo nuevo en WhatsApp vía Evolution.

    Args:
        nombre: nombre del grupo.
        participantes: lista de números (formato 521...), que deben ser
            contactos que acepten — WhatsApp bloquea agregar desconocidos.

    Returns:
        (chatid, None) si se creó — chatid es el nuevo '...@g.us'.
        (None, detalle) si falló (p.ej. participantes no agregables).
    """
    try:
        resp = requests.post(
            f"{EVOLUTION_URL}/group/create/{EVOLUTION_INSTANCE}",
            json={"subject": nombre, "participants": participantes},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            # La respuesta trae el id del grupo creado.
            data = resp.json()
            chatid = data.get("id") or data.get("groupJid")
            return chatid, None
        return None, f"HTTP {resp.status_code}: {resp.text[:300]}"
    except (requests.RequestException, ValueError) as exc:
        return None, f"Error de conexión con Evolution: {repr(exc)}"


def participantes_grupo(chatid: str) -> list[dict]:
    """
    Lista los participantes de un grupo.

    Args:
        chatid: identificador del grupo (...@g.us).

    Returns:
        Lista [{ "numero": "521...", "es_admin": bool }]. Vacía si falla.
    """
    try:
        resp = requests.get(
            f"{EVOLUTION_URL}/group/participants/{EVOLUTION_INSTANCE}",
            params={"groupJid": chatid},
            headers={"apikey": EVOLUTION_API_KEY},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        return [
            {
                "numero": p.get("id", "").split("@")[0],
                "es_admin": p.get("admin") in ("admin", "superadmin"),
            }
            for p in resp.json().get("participants", [])
        ]
    except (requests.RequestException, ValueError) as exc:
        logger.warning("No se pudieron listar participantes: %s", repr(exc))
        return []


# ── Prueba manual desde línea de comandos ─────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) != 3:
        sys.exit("Uso: python -m services.whatsapp_service <chatid@g.us> <mensaje>")

    ok, detalle = enviar_a_grupo(sys.argv[1], sys.argv[2])
    print("✓ Enviado" if ok else f"✗ Falló: {detalle}")
