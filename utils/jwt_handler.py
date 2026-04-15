import jwt
from datetime import datetime, timedelta, timezone
from config import Config


def generate_jwt(user: dict) -> str:
    """
    Genera un JWT con la información del usuario autenticado.

    El payload incluye el rol normalizado (de t_roles) en lugar
    del campo legacy 'perfil', además de si es admin de empresa
    para que el frontend pueda redirigir correctamente.
    """
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "nombre": user.get("nombre"),
        # Nuevo: clave del rol ('sudo_erp', 'admin_empresa', 'usuario')
        "rol": user.get("rol"),
        # Legacy: se mantiene mientras se termina la migración del PHP
        "perfil": user.get("perfil"),
        "id_empresa": user.get("id_empresa"),
        "es_admin_empresa": user.get("es_admin_empresa", False),
        "exp": datetime.now(timezone.utc)
        + timedelta(hours=Config.JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def decode_jwt(token: str) -> dict:
    """
    Decodifica y valida un JWT.
    Lanza excepción si el token es inválido o expiró.
    """
    return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
