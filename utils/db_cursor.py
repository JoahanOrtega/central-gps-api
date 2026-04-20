"""
db_cursor.py — Context managers para cursores de BD.

────────────────────────────────────────────────────────────────────────────────
¿Por qué existe este módulo?
────────────────────────────────────────────────────────────────────────────────
El patrón tradicional para usar el pool de conexiones era:

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(...)
        return cursor.fetchall()
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)

Ese patrón se repetía 11 veces entre telemetry_service y monitor_service.
Cada vez que alguien copiaba-pegaba, existía el riesgo de:
  - Olvidar cerrar el cursor (conexión parcialmente bloqueada).
  - Olvidar liberar la conexión (fuga del pool → eventualmente se agota).
  - Mezclar los pools (soltar una conexión de telemetría al pool principal).

Este módulo centraliza el patrón con dos context managers:
  - `main_cursor()`       → pool de BD principal (t_unidades, t_empresas, etc.)
  - `telemetry_cursor()`  → pool de BD de telemetría (t_data)

Uso:

    from utils.db_cursor import telemetry_cursor

    with telemetry_cursor() as cursor:
        cursor.execute(QUERY, params)
        return cursor.fetchall()

La conexión se libera automáticamente al salir del bloque `with`, incluso
si la función lanza una excepción. Esto elimina toda clase de fugas
de pool por descuidos.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from db.connection import (
    get_db_connection,
    get_db_telemetry_connection,
    release_db_connection,
    release_db_telemetry_connection,
)


@contextmanager
def main_cursor() -> Iterator[Any]:
    """
    Context manager para el pool de BD principal.

    Garantiza liberación de la conexión aunque el bloque lance excepción.
    El cursor retornado es un cursor estándar de psycopg2.

    Ejemplo:
        with main_cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()
    """
    connection = get_db_connection()
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        release_db_connection(connection)


@contextmanager
def telemetry_cursor() -> Iterator[Any]:
    """
    Context manager para el pool de BD de telemetría (t_data).

    Usar exclusivamente para queries sobre la BD de telemetría. Mezclar
    este pool con el principal llevaría a que conexiones del pool
    incorrecto se devuelvan al equivocado, corrompiendo ambos pools.
    """
    connection = get_db_telemetry_connection()
    cursor = connection.cursor()
    try:
        yield cursor
    finally:
        cursor.close()
        release_db_telemetry_connection(connection)
