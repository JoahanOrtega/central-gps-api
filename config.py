import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "key-prueba")
    JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "8"))

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_NAME = os.getenv("DB_NAME", "centralgps")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "123")
    DB_PORT = os.getenv("DB_PORT", "5432")

    TELEMETRY_DB_HOST = os.getenv("TELEMETRY_DB_HOST", "127.0.0.1")
    TELEMETRY_DB_NAME = os.getenv("TELEMETRY_DB_NAME", "centralgps")
    TELEMETRY_DB_USER = os.getenv("TELEMETRY_DB_USER", "postgres")
    TELEMETRY_DB_PASSWORD = os.getenv("TELEMETRY_DB_PASSWORD", "123")
    TELEMETRY_DB_PORT = os.getenv("TELEMETRY_DB_PORT", "5432")
