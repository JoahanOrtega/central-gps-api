import psycopg2
from config import Config


def get_db_connection():
    return psycopg2.connect(
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT
    )

def get_db_telemetry_connection():
    return psycopg2.connect(
        host=Config.TELEMETRY_DB_HOST,
        dbname=Config.TELEMETRY_DB_NAME,
        user=Config.TELEMETRY_DB_USER,
        password=Config.TELEMETRY_DB_PASSWORD,
        port=Config.TELEMETRY_DB_PORT
    )