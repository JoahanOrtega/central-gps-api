import jwt
from datetime import datetime, timedelta, timezone
from config import Config


def generate_jwt(user: dict) -> str:
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "exp": datetime.now(timezone.utc)
        + timedelta(hours=Config.JWT_EXPIRATION_HOURS),
        "iat": datetime.now(timezone.utc),
    }

    return jwt.encode(payload, Config.SECRET_KEY, algorithm="HS256")


def decode_jwt(token: str):
    return jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
