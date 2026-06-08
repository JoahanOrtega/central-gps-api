#!/usr/bin/env python3
"""
migrate.py — Sistema de control de migraciones para CentralGPS API
"""

import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Configuración
# Lee las mismas variables de entorno que usa la aplicación Flask,
# así no hay configuración duplicada.

DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "centralgps_project")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Carpeta donde viven los archivos .sql (relativa a este script)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Colores ANSI para la terminal (se desactivan si no es TTY)
_IS_TTY = sys.stdout.isatty()


def _color(code: str, text: str) -> str:
    """Aplica color ANSI solo si la salida es una terminal."""
    if not _IS_TTY:
        return text
    return f"\033[{code}m{text}\033[0m"


OK = _color("32", "✓")  # verde
SKIP = _color("33", "→")  # amarillo
ERROR = _color("31", "✗")  # rojo
WARN = _color("33", "!")  # amarillo
INFO = _color("36", "i")  # cyan
BOLD = lambda t: _color("1", t)


# Utilidades


def _checksum(content: str) -> str:
    """Calcula el SHA-256 del contenido de un archivo SQL."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _conectar() -> psycopg2.extensions.connection:
    """
    Abre una conexión a la BD principal usando las variables de entorno.
    Lanza una excepción clara si no puede conectarse.
    """
    try:
        return psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=10,
        )
    except psycopg2.OperationalError as e:
        print(f"\n{ERROR} No se pudo conectar a la BD: {e}")
        print(f"   Host: {DB_HOST}:{DB_PORT}  BD: {DB_NAME}  Usuario: {DB_USER}")
        print(
            f"   Verifica que el contenedor db esté corriendo y las variables de entorno sean correctas."
        )
        sys.exit(1)


def _obtener_aplicadas(cur) -> dict[str, str]:
    """
    Retorna un dict {filename: checksum} de las migraciones ya registradas
    en schema_migrations.
    """
    cur.execute(
        "SELECT filename, checksum FROM public.schema_migrations ORDER BY applied_at"
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _obtener_archivos() -> list[Path]:
    """
    Retorna la lista de archivos .sql en migrations/ ordenados numéricamente.
    Solo incluye archivos que empiezan con dígitos (NNN_nombre.sql).
    """
    if not MIGRATIONS_DIR.exists():
        print(f"{ERROR} No se encontró la carpeta migrations/ en {MIGRATIONS_DIR}")
        sys.exit(1)

    archivos = sorted(
        [f for f in MIGRATIONS_DIR.glob("*.sql") if f.name[0].isdigit()],
        key=lambda f: f.name,
    )
    return archivos


# Comandos


def cmd_status(cur) -> None:
    """
    Muestra el estado de todas las migraciones: aplicadas, pendientes
    y archivos con checksum modificado post-deploy.
    """
    aplicadas = _obtener_aplicadas(cur)
    archivos = _obtener_archivos()

    print(f"\n{BOLD('Estado de migraciones')} — BD: {DB_NAME}@{DB_HOST}\n")

    if not archivos:
        print(f"  {WARN} No hay archivos .sql en {MIGRATIONS_DIR}")
        return

    pendientes = 0
    for archivo in archivos:
        contenido = archivo.read_text(encoding="utf-8")
        checksum = _checksum(contenido)
        nombre = archivo.name

        if nombre not in aplicadas:
            print(f"  {SKIP} {nombre}  {_color('33', '[PENDIENTE]')}")
            pendientes += 1
        else:
            checksum_bd = aplicadas[nombre]
            if checksum_bd != "legacy" and checksum_bd != checksum:
                # El archivo fue modificado después de aplicarse
                print(
                    f"  {WARN} {nombre}  {_color('33', '[MODIFICADO - checksum no coincide]')}"
                )
            else:
                print(f"  {OK} {nombre}  {_color('90', '[aplicada]')}")

    print()
    if pendientes:
        print(
            f"  {WARN} {pendientes} migración(es) pendiente(s). Corre: python migrate.py\n"
        )
    else:
        print(f"  {OK} Todas las migraciones están aplicadas.\n")


def cmd_migrate(cur, conn, dry_run: bool = False) -> None:
    """
    Aplica todas las migraciones pendientes en orden.

    Si dry_run=True, solo muestra qué se aplicaría sin ejecutar nada.
    Si alguna migración falla, hace rollback de esa migración y detiene
    el proceso — las anteriores ya aplicadas en este run se mantienen.
    """
    aplicadas = _obtener_aplicadas(cur)
    archivos = _obtener_archivos()

    pendientes = [f for f in archivos if f.name not in aplicadas]

    modo = _color("33", "[DRY RUN] ") if dry_run else ""
    print(f"\n{BOLD('Migraciones')} {modo}— BD: {DB_NAME}@{DB_HOST}\n")

    if not pendientes:
        print(f"  {OK} No hay migraciones pendientes. La BD está al día.\n")
        return

    print(f"  {INFO} {len(pendientes)} migración(es) pendiente(s):\n")

    aplicadas_ok = 0
    aplicadas_err = 0

    for archivo in pendientes:
        nombre = archivo.name
        contenido = archivo.read_text(encoding="utf-8")
        checksum = _checksum(contenido)

        if dry_run:
            print(f"  {SKIP} {nombre}  {_color('33', '[se aplicaría]')}")
            continue

        print(f"  → Aplicando {BOLD(nombre)} ...", end=" ", flush=True)
        inicio = time.monotonic()

        try:
            # Cada migración corre en su propia transacción.
            # Si el archivo ya tiene BEGIN/COMMIT, psycopg2 lo respeta.
            # Si no, la transacción es implícita.
            conn.autocommit = False
            cur.execute(contenido)

            # Registrar la migración como aplicada
            duracion_ms = int((time.monotonic() - inicio) * 1000)
            cur.execute(
                """
                INSERT INTO public.schema_migrations (filename, checksum, applied_at, duration_ms)
                VALUES (%s, %s, NOW(), %s)
                ON CONFLICT (filename) DO NOTHING
                """,
                (nombre, checksum, duracion_ms),
            )
            conn.commit()
            conn.autocommit = True

            print(f"{OK}  {_color('90', f'({duracion_ms} ms)')}")
            aplicadas_ok += 1

        except Exception as e:
            # Rollback de esta migración — las anteriores en este run
            # ya están commiteadas y no se revierten.
            try:
                conn.rollback()
                conn.autocommit = True
            except Exception:
                pass

            print(f"{ERROR}")
            print(f"\n  {ERROR} {BOLD('Error al aplicar')} {nombre}:")
            print(f"     {e}")
            print(f"\n  Las migraciones aplicadas antes de este error se mantienen.")
            print(f"  Corrige el error en {archivo} y vuelve a correr migrate.py.\n")
            aplicadas_err += 1
            sys.exit(1)

    print()
    if not dry_run:
        if aplicadas_ok:
            print(f"  {OK} {aplicadas_ok} migración(es) aplicada(s) correctamente.\n")
    else:
        print(f"  {INFO} Dry run completado — no se aplicó ningún cambio.\n")


# Punto de entrada


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    solo_status = "--status" in sys.argv

    conn = _conectar()
    cur = conn.cursor()

    try:
        if solo_status:
            cmd_status(cur)
        else:
            cmd_migrate(cur, conn, dry_run=dry_run)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
