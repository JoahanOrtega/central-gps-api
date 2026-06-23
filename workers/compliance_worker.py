#!/usr/bin/env python3
"""
compliance_worker.py — Worker de detección de cumplimiento GPS.

Arquitectura híbrida:
  - Este script: puente mínimo entre las dos BDs (GCP y local)
  - La detección geométrica: función PL/pgSQL con ST_DWithin() en PostgreSQL
  - El estado se guarda en la BD local (t_itinerario_fecha_parada,
    t_itinerario_fecha_unidad)

Flujo por ciclo:
  1. Leer pings nuevos de BD remota (GCP) para los IMEIs activos
  2. Para cada ping, llamar a detectar_eventos_parada() en BD local
  3. La función SQL hace todo el trabajo de geometría y actualiza métricas
  4. Emitir pg_notify para que el monitor en tiempo real (3C) reciba eventos

Uso:
  python compliance_worker.py              # correr indefinidamente
  python compliance_worker.py --once       # un solo ciclo (para testing)
  python compliance_worker.py --dry-run    # leer pings pero no escribir nada

Variables de entorno:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD  ← BD local
  TELEMETRY_HOST, TELEMETRY_PORT, TELEMETRY_DB,
  TELEMETRY_USER, TELEMETRY_PASSWORD               ← BD remota GCP
  WORKER_INTERVAL_SECONDS  (default: 15)
  WORKER_LOOKBACK_SECONDS  (default: 60)  ← ventana de pings a leer por ciclo
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

# ── Configuración ─────────────────────────────────────────────────────────────

# BD local (centralgps_project — donde viven itinerarios y cumplimiento)
LOCAL_DB = {
    "host": os.getenv("DB_HOST", "db"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "centralgps_project"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
}

# BD remota GCP (centralgps — donde viven los pings GPS en t_data)
TELEMETRY_DB = {
    "host": os.getenv("TELEMETRY_HOST", "136.119.58.28"),
    "port": int(os.getenv("TELEMETRY_PORT", "5432")),
    "dbname": os.getenv("TELEMETRY_DB", "centralgps"),
    "user": os.getenv("TELEMETRY_USER", "postgres"),
    "password": os.getenv("TELEMETRY_PASSWORD", ""),
    "connect_timeout": 10,
}

# Intervalo entre ciclos en segundos
INTERVAL = int(os.getenv("WORKER_INTERVAL_SECONDS", "15"))

# Ventana de pings a leer por ciclo (segundos hacia atrás)
# Con INTERVAL=15 y LOOKBACK=60 hay un overlap de 45s — garantiza que
# ningún ping se pierda aunque el ciclo anterior tardara más de lo esperado
LOOKBACK = int(os.getenv("WORKER_LOOKBACK_SECONDS", "60"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("compliance_worker")


# ── Query de pings ────────────────────────────────────────────────────────────

# Lee los pings de t_data de la BD remota para los IMEIs activos.
# Excluye solo los puntos con FIX='0' explícito (sin señal GPS); FIX NULL,
# ausente o atributos no-JSON se incluyen. Mismo criterio de fondo que
# telemetry_service (IS DISTINCT FROM '0'), pero más defensivo: cubre el NULL
# y el atributos no parseable por separado antes de castear a jsonb.
_QUERY_PINGS = """
    SELECT
        imei,
        fecha_hora_gps,
        latitud,
        longitud,
        velocidad,
        odometro,
        status
    FROM public.t_data
    WHERE imei = ANY(%s)
      AND fecha_hora_gps >= NOW() AT TIME ZONE 'UTC' - INTERVAL '{lookback} seconds'
      AND latitud  IS NOT NULL
      AND longitud IS NOT NULL
      AND (
        atributos IS NULL
        OR atributos NOT LIKE '{%%'
        OR (atributos::jsonb->>'FIX') IS NULL
        OR (atributos::jsonb->>'FIX') != '0'
      )
    ORDER BY imei, fecha_hora_gps ASC
"""

# ── Funciones de conexión ─────────────────────────────────────────────────────


def _conectar_local() -> psycopg2.extensions.connection:
    """Abre conexión a la BD local con autocommit=True."""
    conn = psycopg2.connect(**LOCAL_DB)
    conn.autocommit = True
    return conn


def _conectar_telemetria() -> psycopg2.extensions.connection:
    """Abre conexión a la BD remota de telemetría (GCP)."""
    conn = psycopg2.connect(**TELEMETRY_DB)
    conn.autocommit = True
    return conn


# ── Obtener IMEIs activos ─────────────────────────────────────────────────────


def _get_imeis_activos(conn_local) -> list[str]:
    """
    Retorna los IMEIs de unidades con itinerarios en curso ahora mismo.

    Un itinerario está "en curso" si:
    - t_itinerario_fecha.status = 2 (en curso)
    - t_itinerario_fecha_unidad.status = 0 (activo)
    - La ventana horaria incluye el momento actual
    """
    with conn_local.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ifu.imei
            FROM t_itinerario_fecha_unidad ifu
            INNER JOIN t_itinerario_fecha itf
                    ON itf.id_itinerario_fecha = ifu.id_itinerario_fecha
            WHERE itf.status = 2          -- en curso
              AND ifu.status = 0          -- unidad activa
              AND ifu.imei IS NOT NULL
              AND itf.fecha_hora_inicio <= NOW()
              AND itf.fecha_hora_fin     >= NOW() - INTERVAL '30 minutes'
            """)
        return [row[0] for row in cur.fetchall()]


# ── Función principal de detección (PL/pgSQL) ─────────────────────────────────
_SQL_DETECTAR = """
    SELECT detectar_eventos_parada(
        %s::text,
        %s::numeric,
        %s::numeric,
        %s::timestamp,
        %s::numeric,
        %s::numeric
    )
"""


# ── Ciclo principal ───────────────────────────────────────────────────────────


def procesar_ciclo(
    conn_local,
    conn_telemetria,
    dry_run: bool = False,
) -> dict:
    """
    Ejecuta un ciclo completo del worker.

    Returns:
        { imeis_activos, pings_leidos, eventos_detectados, errores }
    """
    stats = {
        "imeis_activos": 0,
        "pings_leidos": 0,
        "eventos_detectados": 0,
        "errores": 0,
    }

    # 1. Obtener IMEIs con itinerarios activos ahora
    imeis = _get_imeis_activos(conn_local)
    stats["imeis_activos"] = len(imeis)

    if not imeis:
        return stats

    # 2. Leer pings de la BD remota para esos IMEIs
    query = _QUERY_PINGS.format(lookback=LOOKBACK)
    with conn_telemetria.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(query, (imeis,))
        pings = cur.fetchall()

    stats["pings_leidos"] = len(pings)

    if not pings or dry_run:
        if dry_run and pings:
            logger.info(
                "[DRY RUN] %d pings encontrados para %d IMEIs — no se procesa",
                len(pings),
                len(imeis),
            )
        return stats

    # 3. Procesar cada ping con la función PL/pgSQL
    with conn_local.cursor() as cur:
        for ping in pings:
            try:
                cur.execute(
                    _SQL_DETECTAR,
                    (
                        ping["imei"],
                        ping["latitud"],
                        ping["longitud"],
                        ping["fecha_hora_gps"],
                        ping["velocidad"] or 0,
                        ping["odometro"] or 0,
                    ),
                )
                stats["eventos_detectados"] += 1
            except Exception as e:
                stats["errores"] += 1
                logger.warning(
                    "Error procesando ping IMEI=%s fecha=%s: %s",
                    ping["imei"],
                    ping["fecha_hora_gps"],
                    e,
                )

    return stats


def run(once: bool = False, dry_run: bool = False) -> None:
    """
    Loop principal del worker.

    Args:
        once:    Si True, ejecuta un solo ciclo y termina.
        dry_run: Si True, lee pings pero no llama a detectar_eventos_parada.
    """
    logger.info(
        "Worker iniciado — intervalo=%ds lookback=%ds dry_run=%s",
        INTERVAL,
        LOOKBACK,
        dry_run,
    )

    conn_local = None
    conn_telemetria = None

    try:
        conn_local = _conectar_local()
        conn_telemetria = _conectar_telemetria()
        logger.info("Conexiones establecidas (local + telemetría GCP)")

        while True:
            ciclo_inicio = time.monotonic()

            try:
                stats = procesar_ciclo(conn_local, conn_telemetria, dry_run)

                if stats["imeis_activos"] > 0 or stats["errores"] > 0:
                    logger.info(
                        "Ciclo: IMEIs=%d pings=%d eventos=%d errores=%d (%.0fms)",
                        stats["imeis_activos"],
                        stats["pings_leidos"],
                        stats["eventos_detectados"],
                        stats["errores"],
                        (time.monotonic() - ciclo_inicio) * 1000,
                    )

            except psycopg2.OperationalError as e:
                # Reconectar si la BD remota cerró la conexión
                logger.warning("Conexión perdida — reconectando: %s", e)
                try:
                    conn_local.close()
                    conn_telemetria.close()
                except Exception:
                    pass
                time.sleep(5)
                conn_local = _conectar_local()
                conn_telemetria = _conectar_telemetria()
                logger.info("Reconexión exitosa")

            if once:
                break

            # Esperar hasta el siguiente ciclo
            elapsed = time.monotonic() - ciclo_inicio
            sleep_time = max(0, INTERVAL - elapsed)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Worker detenido por el usuario")
    finally:
        if conn_local:
            conn_local.close()
        if conn_telemetria:
            conn_telemetria.close()


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    run(once=once, dry_run=dry_run)
