#!/usr/bin/env python3
import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg2

# Configuración
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "centralgps_project")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Colores ANSI
_IS_TTY = sys.stdout.isatty()


def _color(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _IS_TTY else text


OK = _color("32", "✓")
SKIP = _color("33", "→")
ERR = _color("31", "✗")
WARN = _color("33", "!")
INFO = _color("36", "i")
BOLD = lambda t: _color("1", t)


# Utilidades


def _checksum(content: str) -> str:
    """SHA-256 del contenido del archivo SQL."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _conectar() -> psycopg2.extensions.connection:
    """
    Abre conexión a la BD con autocommit=True desde el inicio.

    autocommit=True es necesario porque:
    1. Las migraciones SQL tienen su propio BEGIN/COMMIT.
    2. psycopg2 abre una transacción implícita en cuanto ejecuta la
       primera query, incluyendo el SELECT de schema_migrations.
    3. Si ya hay una transacción abierta, el BEGIN del SQL falla con
       'set_session cannot be used inside a transaction'.

    El registro en schema_migrations abre su propia transacción
    explícita con conn.autocommit=False solo para ese INSERT.
    """
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            connect_timeout=10,
        )
        # Activar autocommit inmediatamente — antes de cualquier query
        conn.autocommit = True
        return conn
    except psycopg2.OperationalError as e:
        print(f"\n{ERR} No se pudo conectar a la BD: {e}")
        print(f"   Host: {DB_HOST}:{DB_PORT}  BD: {DB_NAME}  Usuario: {DB_USER}")
        sys.exit(1)


def _obtener_aplicadas(conn) -> dict[str, str]:
    """Retorna {filename: checksum} de las migraciones ya registradas."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT filename, checksum FROM public.schema_migrations ORDER BY applied_at"
        )
        return {row[0]: row[1] for row in cur.fetchall()}


def _obtener_archivos() -> list[Path]:
    """Lista de archivos .sql en migrations/ ordenados numéricamente."""
    if not MIGRATIONS_DIR.exists():
        print(f"{ERR} No se encontró la carpeta migrations/ en {MIGRATIONS_DIR}")
        sys.exit(1)
    return sorted(
        [f for f in MIGRATIONS_DIR.glob("*.sql") if f.name[0].isdigit()],
        key=lambda f: f.name,
    )


# Comandos


def cmd_status(conn) -> None:
    """Muestra el estado de todas las migraciones."""
    aplicadas = _obtener_aplicadas(conn)
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
                print(f"  {WARN} {nombre}  {_color('33', '[MODIFICADO]')}")
            else:
                print(f"  {OK} {nombre}  {_color('90', '[aplicada]')}")

    print()
    if pendientes:
        print(
            f"  {WARN} {pendientes} migración(es) pendiente(s). Corre: python migrate.py\n"
        )
    else:
        print(f"  {OK} Todas las migraciones están aplicadas.\n")


def cmd_migrate(conn, dry_run: bool = False) -> None:
    """
    Aplica todas las migraciones pendientes en orden.

    La conexión ya viene con autocommit=True desde _conectar().
    Esto permite que el BEGIN/COMMIT de cada archivo SQL maneje
    su propia transacción sin conflicto con psycopg2.

    El registro en schema_migrations usa su propia transacción
    explícita (autocommit=False temporalmente) para garantizar
    atomicidad del INSERT.
    """
    aplicadas = _obtener_aplicadas(conn)
    archivos = _obtener_archivos()
    pendientes = [f for f in archivos if f.name not in aplicadas]

    modo = _color("33", "[DRY RUN] ") if dry_run else ""
    print(f"\n{BOLD('Migraciones')} {modo}— BD: {DB_NAME}@{DB_HOST}\n")

    if not pendientes:
        print(f"  {OK} No hay migraciones pendientes. La BD está al día.\n")
        return

    print(f"  {INFO} {len(pendientes)} migración(es) pendiente(s):\n")

    aplicadas_ok = 0

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
            # 1. Ejecutar el SQL con autocommit=True
            # La conexión ya está en autocommit=True. El BEGIN/COMMIT del
            # archivo controla su propia transacción directamente.
            with conn.cursor() as cur:
                cur.execute(contenido)

            duracion_ms = int((time.monotonic() - inicio) * 1000)

            # 2. Registrar migración en schema_migrations
            # Transacción explícita solo para este INSERT — garantiza que
            # el registro sea atómico: o se guarda correctamente o no.
            conn.autocommit = False
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO public.schema_migrations
                            (filename, checksum, applied_at, duration_ms)
                        VALUES (%s, %s, NOW(), %s)
                        ON CONFLICT (filename) DO NOTHING
                        """,
                        (nombre, checksum, duracion_ms),
                    )
                conn.commit()
            finally:
                # Restaurar autocommit=True para la siguiente migración
                conn.autocommit = True

            print(f"{OK}  {_color('90', f'({duracion_ms} ms)')}")
            aplicadas_ok += 1

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.autocommit = True  # restaurar siempre

            print(f"{ERR}")
            print(f"\n  {ERR} {BOLD('Error al aplicar')} {nombre}:")
            print(f"     {e}")
            print(f"\n  Corrige el error en {archivo} y vuelve a correr migrate.py.\n")
            sys.exit(1)

    print()
    if not dry_run and aplicadas_ok:
        print(f"  {OK} {aplicadas_ok} migración(es) aplicada(s) correctamente.\n")
    elif dry_run:
        print(f"  {INFO} Dry run completado — no se aplicó ningún cambio.\n")


# Punto de entrada


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    solo_status = "--status" in sys.argv

    conn = _conectar()
    try:
        if solo_status:
            cmd_status(conn)
        else:
            cmd_migrate(conn, dry_run=dry_run)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
