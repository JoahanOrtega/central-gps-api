"""
¿Por qué Redis y no memoria?
──────────────────────────────
gunicorn levanta N workers (procesos separados). Con storage en memoria,
cada worker tiene su propio contador: un cliente puede hacer N × límite
requests antes de ser bloqueado. Además los contadores se borran en cada
restart/redeploy.

Con Redis el contador es global y persistente:
  - El límite es real, no multiplicado por workers.
  - Un restart de gunicorn no reinicia los contadores.

key_func = get_real_ip:
  Lee CF-Connecting-IP (Cloudflare) → X-Forwarded-For[0] (nginx directo)
  → remote_addr (dev local). Garantiza que el límite sea por usuario real,
  no por la IP interna de nginx (que sería el mismo bucket para todo el mundo).

El storage_uri se asigna en create_app() desde la variable de entorno
LIMITER_STORAGE_URI para que el módulo pueda importarse sin contexto de app.

Uso en blueprints:
    from utils.limiter import limiter

    @mi_bp.route("/ruta")
    @limiter.limit("10 per minute; 50 per hour")
    def mi_endpoint(): ...
"""

from flask_limiter import Limiter
from utils.real_ip import get_real_ip

limiter = Limiter(
    # Usa la IP real del cliente (Cloudflare-aware) como clave del límite.
    # Sin esto, todos los usuarios de producción comparten un mismo bucket.
    key_func=get_real_ip,
    default_limits=["1000 per hour"],
)