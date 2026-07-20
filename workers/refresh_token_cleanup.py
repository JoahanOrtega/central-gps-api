"""
Limpieza diaria de refresh tokens huérfanos.

El problema
───────────
Cada login inserta una fila en t_refresh_tokens y NADA la borra jamás:
  - Si el usuario nunca hace logout, su token expira pero la fila queda.
  - Cada rotación en /auth/refresh marca la fila vieja revoked=TRUE
    y crea una nueva — la revocada también queda para siempre.
Con logins diarios de la operación, la tabla crece sin límite.

El criterio de borrado (y por qué NO es "revoked = TRUE")
─────────────────────────────────────────────────────────
La detección de robo de sesión depende de las filas revocadas: si un
atacante reproduce un token ya rotado, validate_and_rotate_refresh_token
lo encuentra con revoked=TRUE y revoca TODAS las sesiones del usuario.
Borrar revocados de inmediato destruiría esa detección — el replay
caería en "token no encontrado" (401 simple) sin disparar la alarma.

Por eso el criterio es por expiración con colchón:

    DELETE ... WHERE expires_at < NOW() - INTERVAL '<retención> días'

Una fila revocada conserva el expires_at con el que nació, así que
sobrevive todo su ciclo de vida original + el colchón — ventana más
que suficiente para detectar replays. Y las huérfanas (expiradas sin
uso) se van por el mismo camino. Sin columnas nuevas, sin migración.

Frecuencia: 1 vez al día a las 04:15 (America/Mexico_City, horario
de mínima operación de las flotas). Un DELETE diario sobre un índice
de timestamptz es despreciable; correrlo más seguido no aporta nada.
"""

import logging
import os

from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)

# Días extra que una fila expirada permanece antes de borrarse.
# Protege la ventana de detección de replay (ver docstring del módulo).
RETENCION_DIAS: int = int(os.getenv("REFRESH_TOKEN_RETENCION_DIAS", "7"))


def _ejecutar_limpieza() -> None:
    """
    Ciclo del job: borra tokens expirados hace más de RETENCION_DIAS.

    Captura toda excepción y la loggea — un fallo aquí (BD ocupada,
    deadlock improbable) NO debe tumbar el scheduler compartido, que
    también ejecuta el POI worker y el unit state worker.
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # make_interval evita interpolar el número dentro del SQL:
            # el parámetro viaja como bind variable, no como texto.
            cur.execute(
                """
                DELETE FROM t_refresh_tokens
                WHERE expires_at < NOW() - make_interval(days => %s)
                """,
                (RETENCION_DIAS,),
            )
            borrados = cur.rowcount
        conn.commit()

        # Siempre loggear, incluso con 0 borrados: la ausencia del log
        # diario es la señal de que el job dejó de correr.
        logger.info(
            "Limpieza de refresh tokens: %d filas borradas "
            "(expiradas hace más de %d días)",
            borrados,
            RETENCION_DIAS,
        )
    except Exception as exc:
        if conn is not None:
            conn.rollback()
        logger.error("Limpieza de refresh tokens falló: %s", repr(exc))
    finally:
        if conn is not None:
            release_db_connection(conn)


def registrar_en_scheduler(scheduler) -> None:
    """
    Registra el job de limpieza en el scheduler EXISTENTE del POI worker.

    Mismo patrón que unit_state_worker: un solo BackgroundScheduler por
    proceso — dos schedulers separados bajo gevent provocan que uno
    muera (incidente 2026-06-12).
    """
    if os.getenv("REFRESH_CLEANUP_ENABLED", "true").lower() == "false":
        logger.info("Limpieza de refresh tokens deshabilitada por entorno.")
        return

    scheduler.add_job(
        func=_ejecutar_limpieza,
        trigger="cron",
        hour=4,
        minute=15,
        # El contenedor corre en UTC; sin timezone explícita el cron
        # dispararía a las 04:15 UTC = 22:15 local, hora pico de flotas
        # nocturnas. Todo el pipeline vive en UTC-6.
        timezone="America/Mexico_City",
        id="refresh_token_cleanup",
        name="Limpieza de refresh tokens",
    )
    logger.info(
        "Limpieza de refresh tokens registrada — diaria 04:15 %s, "
        "retención +%d días tras expirar",
        "America/Mexico_City",
        RETENCION_DIAS,
    )