import jwt
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from config import Config


def generate_access_token(user: dict) -> str:
    """
    Genera un access token JWT de corta duración (15 min por defecto).

    Este token viaja en el header Authorization de cada petición HTTP.
    Al expirar, el frontend lo renueva automáticamente usando el refresh token
    sin que el usuario tenga que volver a hacer login.

    El campo 'type': 'access' permite distinguirlo del refresh token
    en caso de que alguien intente usar uno en lugar del otro.

    Args:
        user: Diccionario con los datos del usuario autenticado.

    Returns:
        JWT firmado como string.
    """
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "nombre": user.get("nombre"),
        "rol": user.get("rol"),
        "perfil": user.get("perfil"),  # Legacy — compatibilidad PHP
        "id_empresa": user.get("id_empresa"),
        "nombre_empresa": user.get("nombre_empresa"),
        "es_admin_empresa": user.get("es_admin_empresa", False),
        "permisos": user.get("permisos"),
        "exp": datetime.now(timezone.utc)
        + timedelta(minutes=Config.JWT_EXPIRATION_MINUTES),
        "iat": datetime.now(timezone.utc),
        "type": "access",  # Distinguir del refresh token
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def generate_refresh_token() -> tuple[str, str]:
    """
    Genera un refresh token opaco y su hash SHA-256.

    El refresh token es un string aleatorio de 64 bytes (128 chars hex) —
    no es un JWT. Esto lo hace imposible de falsificar sin acceso a la BD.

    Retorna una tupla (token_crudo, token_hash):
      - token_crudo: se envía al cliente en la cookie HttpOnly.
                     Nunca se guarda en BD.
      - token_hash:  SHA-256 del token crudo. Solo esto se guarda en BD.
                     Si la BD se filtra, los tokens siguen siendo inutilizables.

    Al renovar, el cliente envía el token crudo en la cookie, el backend
    lo hashea y compara contra la BD.

    Returns:
        Tupla (token_crudo: str, token_hash: str).
    """
    token_crudo = secrets.token_hex(64)  # 128 caracteres hex — 512 bits
    token_hash = hashlib.sha256(token_crudo.encode()).hexdigest()
    return token_crudo, token_hash


def decode_access_token(token: str) -> dict:
    """
    Decodifica y valida un access token JWT.

    Lanza excepción si:
      - El token tiene firma inválida
      - El token ha expirado
      - El campo 'type' no es 'access' (previene usar refresh como access)

    Args:
        token: JWT en formato string.

    Returns:
        Payload decodificado como diccionario.

    Raises:
        jwt.InvalidTokenError: Si el token es inválido, expirado o del tipo incorrecto.
    """
    payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])

    # Verificar que sea un access token — no aceptar otros tipos
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("El token no es un access token válido")

    return payload


# ── Aliases de compatibilidad ─────────────────────────────────────────────────
# Mantener hasta que todos los módulos que llaman generate_jwt/decode_jwt
# sean migrados a los nombres explícitos.
def generate_jwt(user: dict) -> str:
    """Alias de compatibilidad para generate_access_token."""
    return generate_access_token(user)


def decode_jwt(token: str) -> dict:
    """Alias de compatibilidad para decode_access_token."""
    return decode_access_token(token)
