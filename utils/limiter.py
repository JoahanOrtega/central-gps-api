from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Instancia global del rate limiter ────────────────────────
#
# Se crea aquí como singleton y se inicializa con la app en
# create_app() via limiter.init_app(app).
#
# Los blueprints lo importan directamente para aplicar límites
# específicos por endpoint con @limiter.limit("N per period").
#
# Ejemplo de uso en un blueprint:
#   from utils.limiter import limiter
#
#   @auth_bp.route("/login", methods=["POST"])
#   @limiter.limit("10 per minute; 50 per hour")
#   def login(): ...

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per hour"],
)
