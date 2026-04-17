import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS
from flask_limiter.util import get_remote_address
from utils.limiter import limiter
from routes import auth_bp, users_bp, units_bp
from routes.poi_routes import poi_bp
from routes.telemetry_routes import telemetry_bp
from routes.monitor_routes import monitor_bp
from routes.catalogs_routes import catalogs_bp
from routes.company_routes import company_bp
from routes.erp_routes import erp_bp

logger = logging.getLogger(__name__)


def _get_cors_origins() -> list[str]:
    """
    Lee los orígenes CORS permitidos desde la variable de entorno CORS_ORIGINS.

    Formato esperado en .env:
        CORS_ORIGINS=https://app.tudominio.com,https://tudominio.com

    Si la variable no está definida, usa los orígenes de desarrollo local
    como fallback seguro — nunca un wildcard (*).

    Returns:
        Lista de orígenes permitidos.
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
    # Los orígenes se leen desde CORS_ORIGINS en .env.
    # En producción definir solo los dominios reales del frontend.
    # Nunca usar "*" — permite peticiones desde cualquier origen externo.
    CORS(
        app,
        resources={r"/*": {"origins": _get_cors_origins()}},
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
        supports_credentials=False,
    )

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    # El limiter se define en utils/limiter.py como singleton importable.
    # Aquí solo se inicializa con la app y se configura el storage.
    #
    # Los blueprints usan @limiter.limit() directamente importando desde
    # utils.limiter — sin necesidad de current_app.extensions.
    limiter.storage_uri = os.getenv("LIMITER_STORAGE_URI", "memory://")
    limiter.enabled = os.getenv("FLASK_TESTING", "false").lower() != "true"
    limiter.init_app(app)
    app.extensions["limiter"] = limiter

    # ── Blueprints ────────────────────────────────────────────────────────────
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(users_bp)
    app.register_blueprint(catalogs_bp)
    app.register_blueprint(units_bp)
    app.register_blueprint(poi_bp)
    app.register_blueprint(telemetry_bp)
    app.register_blueprint(monitor_bp)
    app.register_blueprint(company_bp)
    app.register_blueprint(erp_bp)

    # ── Manejador global de errores de rate limit ─────────────────────────────
    # Flask-Limiter lanza 429 automáticamente, pero sin este handler
    # la respuesta no sería JSON consistente con el resto de la API.
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
    # Endpoint público para verificar que la API está en pie.
    # Útil para balanceadores de carga y monitoreo de infraestructura.
    @app.route("/", methods=["GET"])
    def health_check():
        return {"message": "API CentralGPS funcionando correctamente"}, 200

    return app


# ── Punto de entrada para desarrollo local ────────────────────────────────────
# En producción NO usar app.run() — usar gunicorn:
#   gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
#
# FLASK_DEBUG=true  → recarga automática y traceback detallado (solo dev)
# FLASK_DEBUG=false → modo silencioso, sin debugger (producción)
if __name__ == "__main__":
    app = create_app()

    debug_mode = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    app.run(
        debug=debug_mode,
        # Escuchar solo en localhost al correr directamente —
        # nunca exponer el servidor de desarrollo a la red
        host="127.0.0.1",
        port=int(os.getenv("PORT", "5000")),
    )
