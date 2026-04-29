import os
import logging
import sys
from flask import Flask, jsonify
from flask_cors import CORS
from flask_limiter.util import get_remote_address
from utils.limiter import limiter

# ─── Configuración de logging ─────────────────────────────────────────────────
# Configuramos ANTES de importar los blueprints porque db/connection.py se
# ejecuta al importar y loggea "Pool BD iniciado" — sin esta config esos
# mensajes se pierden en stderr sin formato.
#
# stdout (no stderr) para que Docker los capture con `docker compose logs`.
# Nivel INFO en dev para ver el arranque de pools, requests, etc.
# Formato: timestamp + nivel + módulo + mensaje.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,  # sobrescribe cualquier config previa (ej: la de gunicorn)
)

# Silenciar el ruido de bibliotecas verbosas que no aportan en dev.
logging.getLogger("werkzeug").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from routes import auth_bp, users_bp, units_bp
from routes.poi_routes import poi_bp
from routes.telemetry_routes import telemetry_bp
from routes.monitor_routes import monitor_bp
from routes.catalogs_routes import catalogs_bp
from routes.company_routes import company_bp
from routes.catalog_user_routes import catalog_users_bp
from routes.erp_routes import erp_bp

logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    """
    Lee los orígenes CORS permitidos desde la variable de entorno CORS_ORIGINS.

    Formato esperado en .env:
        CORS_ORIGINS=https://app.tudominio.com,https://tudominio.com

    Si la variable no está definida, usa los orígenes de desarrollo local
    como fallback seguro — nunca un wildcard (*).

    IMPORTANTE: Con supports_credentials=True, el navegador rechaza '*' como
    origen. Siempre debe ser una lista de orígenes explícitos.
    """
    raw = os.getenv("CORS_ORIGINS", "").strip()

    if raw:
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Fallback solo para desarrollo local — nunca llegar aquí en producción
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app() -> Flask:
    """
    Factory de la aplicación Flask.

    Centralizar la creación en una función permite:
      - Reutilizar la app en tests sin efectos secundarios de módulo
      - Configurar entornos distintos (dev, test, prod) de forma limpia
    """
    app = Flask(__name__)

    # ── CORS ─────────────────────────────────────────────────────────────────
    # supports_credentials=True es REQUERIDO para que el navegador envíe y
    # reciba cookies HttpOnly en peticiones cross-origin (frontend en :5173,
    # backend en :5000).
    #
    # Con supports_credentials=True el navegador rechaza '*' como origen —
    # debe usarse una lista de orígenes explícitos. Nunca usar '*'.
    #
    # En producción definir CORS_ORIGINS en .env con los dominios reales.
    CORS(
        app,
        resources={r"/*": {"origins": _get_cors_origins()}},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        supports_credentials=True,  # Necesario para cookies HttpOnly cross-origin
    )

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    # El limiter se define en utils/limiter.py como singleton importable.
    # Aquí solo se inicializa con la app y se configura el storage.
    limiter.storage_uri = os.getenv("LIMITER_STORAGE_URI", "memory://")
    limiter.enabled = os.getenv("FLASK_TESTING", "false").lower() != "true"
    limiter.init_app(app)
    app.extensions["limiter"] = limiter

    # ── Blueprints ────────────────────────────────────────────────────────────
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(users_bp, url_prefix="/users")
    app.register_blueprint(catalogs_bp)
    app.register_blueprint(units_bp)
    app.register_blueprint(poi_bp)
    app.register_blueprint(telemetry_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(company_bp)
    app.register_blueprint(catalog_users_bp)
    app.register_blueprint(erp_bp)

    # ── Manejador global de errores de rate limit ─────────────────────────────
    @app.errorhandler(429)
    def handle_rate_limit(exc):
        logger.warning("Rate limit excedido desde IP: %s", get_remote_address())
        return (
            jsonify(
                {"error": "Demasiados intentos. Espera un momento e intenta de nuevo."}
            ),
            429,
        )

    # ── Health check ──────────────────────────────────────────────────────────
    @app.route("/", methods=["GET"])
    def health_check():
        return {"message": "API CentralGPS funcionando correctamente"}, 200

    return app


if __name__ == "__main__":
    app = create_app()
    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(
        debug=debug_mode,
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
    )
