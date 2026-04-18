import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    """
    Lee una variable de entorno y lanza un error fatal si no está definida.

    Úsala para secretos y credenciales que NUNCA deben tener un valor por
    defecto: si faltan, la app debe negarse a arrancar en lugar de hacerlo
    con credenciales débiles o predecibles.

    Args:
        key: Nombre de la variable de entorno requerida.

    Returns:
        El valor de la variable si existe y no está vacío.

    Raises:
        SystemExit: Si la variable no está definida o está vacía.
    """
    value = os.getenv(key, "").strip()

    if not value:
        sys.exit(
            f"\n[CONFIG ERROR] La variable de entorno '{key}' no está configurada.\n"
            f"  → Revisa tu archivo .env y asegúrate de definir '{key}' con un valor real.\n"
            f"  → Consulta .env.example para ver el formato esperado.\n"
        )

    return value


class Config:
    # ── Seguridad JWT — Access Token ──────────────────────────────────────────
    # SECRET_KEY firma los access tokens JWT (corta duración — 15 min).
    # Si esta clave es débil, un atacante puede fabricar tokens válidos
    # para cualquier usuario, incluyendo sudo_erp. Requerida al arrancar.
    SECRET_KEY: str = _require("SECRET_KEY")

    # Duración del access token en minutos. Default: 15 min.
    # Corto por diseño — se renueva automáticamente con el refresh token.
    # No usar horas largas aquí; para sesiones largas usar el refresh token.
    JWT_EXPIRATION_MINUTES: int = int(os.getenv("JWT_EXPIRATION_MINUTES", "15"))

    # ── Seguridad JWT — Refresh Token ─────────────────────────────────────────
    # REFRESH_SECRET_KEY debe ser DIFERENTE a SECRET_KEY.
    # El refresh token es opaco (no JWT) — esta clave no se usa para firmarlo,
    # pero sí para validar el contexto de la sesión en el endpoint /auth/refresh.
    # Genera una clave segura:
    #   python -c "import secrets; print(secrets.token_hex(64))"
    REFRESH_SECRET_KEY: str = _require("REFRESH_SECRET_KEY")

    # Duración del refresh token en días. Default: 30 días.
    # Determina cada cuánto el usuario debe hacer login nuevamente.
    REFRESH_TOKEN_EXPIRATION_DAYS: int = int(
        os.getenv("REFRESH_TOKEN_EXPIRATION_DAYS", "30")
    )

    # ── Frontend ──────────────────────────────────────────────────────────────
    # URL base del frontend — usada para configurar la cookie del refresh token.
    # En desarrollo: http://localhost:5173
    # En producción: https://app.tudominio.com
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    # ── Base de datos principal ────────────────────────────────────────────────
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_NAME: str = os.getenv("DB_NAME", "centralgps")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = _require("DB_PASSWORD")
    DB_PORT: str = os.getenv("DB_PORT", "5432")

    # ── Base de datos de telemetría ────────────────────────────────────────────
    TELEMETRY_DB_HOST: str = os.getenv("TELEMETRY_DB_HOST", "127.0.0.1")
    TELEMETRY_DB_NAME: str = os.getenv("TELEMETRY_DB_NAME", "centralgps")
    TELEMETRY_DB_USER: str = os.getenv("TELEMETRY_DB_USER", "postgres")
    TELEMETRY_DB_PASSWORD: str = _require("TELEMETRY_DB_PASSWORD")
    TELEMETRY_DB_PORT: str = os.getenv("TELEMETRY_DB_PORT", "5432")
