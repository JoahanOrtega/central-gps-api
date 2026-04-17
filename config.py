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
        # Mensaje claro para el operador — indica qué falta y cómo resolverlo
        sys.exit(
            f"\n[CONFIG ERROR] La variable de entorno '{key}' no está configurada.\n"
            f"  → Revisa tu archivo .env y asegúrate de definir '{key}' con un valor real.\n"
            f"  → Consulta .env.example para ver el formato esperado.\n"
        )

    return value


class Config:
    # ── Seguridad JWT ──────────────────────────────────────────────────────
    # SECRET_KEY firma todos los tokens JWT del sistema.
    # Si esta clave es débil o predecible, un atacante puede fabricar tokens
    # válidos para cualquier usuario, incluyendo sudo_erp.
    # Requerida: la app no arranca sin ella.
    SECRET_KEY: str = _require("SECRET_KEY")

    # Duración de los tokens JWT en horas. Default: 8 horas.
    # Ajustar según política de seguridad del negocio.
    JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "8"))

    # ── Base de datos principal ────────────────────────────────────────────
    # Host y puerto tienen defaults de desarrollo — no son secretos.
    # Usuario y contraseña son requeridos: no deben tener defaults débiles.
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_NAME: str = os.getenv("DB_NAME", "centralgps")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = _require("DB_PASSWORD")
    DB_PORT: str = os.getenv("DB_PORT", "5432")

    # ── Base de datos de telemetría ────────────────────────────────────────
    # Misma política: host/puerto/nombre con defaults, contraseña requerida.
    TELEMETRY_DB_HOST: str = os.getenv("TELEMETRY_DB_HOST", "127.0.0.1")
    TELEMETRY_DB_NAME: str = os.getenv("TELEMETRY_DB_NAME", "centralgps")
    TELEMETRY_DB_USER: str = os.getenv("TELEMETRY_DB_USER", "postgres")
    TELEMETRY_DB_PASSWORD: str = _require("TELEMETRY_DB_PASSWORD")
    TELEMETRY_DB_PORT: str = os.getenv("TELEMETRY_DB_PORT", "5432")

    import os


from dotenv import load_dotenv

load_dotenv()
