"""
utils/real_ip.py — IP real del cliente detrás de Cloudflare Tunnel + nginx.

¿Por qué existe este módulo?
─────────────────────────────
El tráfico de producción recorre la cadena:
    Usuario → Cloudflare Edge → Cloudflare Tunnel → nginx → Flask

Sin ProxyFix, Flask ve como "IP del cliente" la IP interna del
contenedor de nginx — completamente inútil para auditoría y rate limiting.

Estrategia (la misma que usan Shopify, Vercel y Cloudflare Workers):
    1. Leer CF-Connecting-IP: Cloudflare lo inyecta con la IP real del
       visitante y lo sobreescribe en cada request, por lo que un cliente
       malicioso NO puede falsificarlo. Es la fuente más confiable.
    2. Fallback X-Forwarded-For[0]: si el request llega sin pasar por
       Cloudflare (entorno local, health-check directo desde el servidor),
       nginx ya setea X-Forwarded-For con la IP de origen.
    3. Último recurso remote_addr: siempre presente, pero es la IP de nginx
       en producción. Solo util en dev directo (python app.py).

ProxyFix de Werkzeug se configura con x_for=1, x_proto=1, x_host=1 para
que request.remote_addr refleje el primer proxy de confianza. La función
get_real_ip() va un paso más allá leyendo CF-Connecting-IP.

Uso:
    from utils.real_ip import get_real_ip
    ip = get_real_ip()          # en cualquier endpoint Flask

    # Para el rate limiter (key_func):
    from utils.real_ip import get_real_ip
    limiter = Limiter(key_func=get_real_ip, ...)
"""

from flask import request


def get_real_ip() -> str:
    """
    Devuelve la IP real del cliente en cualquier entorno:

        Producción (Cloudflare → nginx → Flask):
            CF-Connecting-IP tiene la IP real del visitante.

        Staging / acceso directo sin Cloudflare:
            X-Forwarded-For[0] contiene la IP del cliente original.

        Desarrollo local (python app.py sin proxies):
            remote_addr es directamente la IP del cliente.
    """
    # Prioridad 1: Cloudflare la inyecta y la sobreescribe — no falsificable
    # cuando el tráfico pasa por el tunnel de Cloudflare.
    cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
    if cf_ip:
        return cf_ip

    # Prioridad 2: primer elemento de X-Forwarded-For (más cercano al cliente).
    # nginx lo setea como $remote_addr cuando no viene de Cloudflare.
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()

    # Prioridad 3: fallback directo — solo ocurre en dev local sin proxies.
    return request.remote_addr or "unknown"