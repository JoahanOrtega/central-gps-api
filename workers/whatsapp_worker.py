#   - poi_worker, unit_state_worker insertan filas con status=0 al detectar eventos.
#   - Este CONSUMIDOR toma filas pendientes, las envía vía whatsapp_service
#     (Evolution API) y marca el resultado.

# Estados de t_alertas_grupo_whatsapp.status:
#   0 = pendiente   1 = enviada   2 = expirada

#   1. FASES SEPARADAS: se lee la cola y se CIERRA la conexión ANTES de hacer
#      cualquier envío HTTP. Los envíos ocurren sin transacción viva. Luego se
#      reabre una conexión corta solo para marcar resultados. Así el HTTP nunca
#      pasa con la BD abierta — igual que los otros workers nunca hacen HTTP.
#   2. WATCHDOG gevent.Timeout: si aun así algo se cuelga (DNS, socket raro),
#      el ciclo se aborta a los CICLO_TIMEOUT_SEC y el siguiente arranca limpio.

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta

import gevent
from apscheduler.schedulers.background import BackgroundScheduler

from db.connection import get_db_connection, release_db_connection
from services.whatsapp_service import enviar_a_grupo

logger = logging.getLogger(__name__)

# ── Configuración (sobrescribible por variables de entorno) ───────────────────

POLL_INTERVAL: int = int(os.getenv("WHATSAPP_WORKER_POLL_SEC", "30"))
LOTE_MAX: int = int(os.getenv("WHATSAPP_WORKER_LOTE_MAX", "20"))
MAX_EDAD_HORAS: int = int(os.getenv("WHATSAPP_MAX_EDAD_HORAS", "6"))

# Límite absoluto de duración de un ciclo. Menor que POLL_INTERVAL para que
# un ciclo colgado se aborte antes de que llegue el siguiente.
CICLO_TIMEOUT_SEC: int = int(os.getenv("WHATSAPP_CICLO_TIMEOUT_SEC", "25"))

# ── SQL ───────────────────────────────────────────────────────────────────────

# FASE 1 — leer pendientes
_SQL_TOMAR_PENDIENTES = """
    SELECT a.id_whatsapp, a.mensaje, d.chatid
      FROM public.t_alertas_whatsapp a
      JOIN public.t_destinos_whatsapp d
        ON d.id_destino_whatsapp = a.id_destino_whatsapp
       AND d.status = 1
     WHERE a.status = 0
       AND a.fecha > NOW() - make_interval(hours => %(max_edad)s)
     ORDER BY a.fecha
     LIMIT %(lote)s
"""

_SQL_MARCAR_ENVIADA = """
    UPDATE public.t_alertas_whatsapp
       SET status = 1, fecha_envio = NOW()
     WHERE id_whatsapp = ANY(%(ids)s)
"""

_SQL_EXPIRAR_VIEJAS = """
    UPDATE public.t_alertas_whatsapp
       SET status = 2
     WHERE status = 0
       AND fecha <= NOW() - make_interval(hours => %(max_edad)s)
"""


def _leer_pendientes() -> list[tuple]:
    """
    FASE 1: expira viejas y lee el lote de pendientes. Abre y CIERRA su propia
    conexión — al volver, no queda ninguna transacción viva. Ninguna llamada
    HTTP ocurre aquí.

    Returns:
        Lista de tuplas (id_whatsapp, mensaje, chatid). Vacía si no hay nada.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(_SQL_EXPIRAR_VIEJAS, {"max_edad": MAX_EDAD_HORAS})
        if cur.rowcount:
            logger.info("Alertas expiradas por edad: %s", cur.rowcount)

        cur.execute(
            _SQL_TOMAR_PENDIENTES, {"max_edad": MAX_EDAD_HORAS, "lote": LOTE_MAX}
        )
        filas = cur.fetchall()
        conn.commit()
        return filas
    finally:
        if conn:
            try:
                release_db_connection(conn)
            except Exception as exc:
                logger.warning("Error liberando conexión (lectura): %s", repr(exc))


def _marcar_enviadas(ids: list[int]) -> None:
    """
    FASE 3: marca como enviadas (status=1) las filas que salieron. Conexión
    corta, sin HTTP de por medio.
    """
    if not ids:
        return
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(_SQL_MARCAR_ENVIADA, {"ids": ids})
        conn.commit()
    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.error("Error marcando enviadas %s: %s", ids, repr(exc))
    finally:
        if conn:
            try:
                release_db_connection(conn)
            except Exception as exc:
                logger.warning("Error liberando conexión (marcado): %s", repr(exc))


def _trabajo_ciclo() -> None:
    """
    Orquesta las 3 fases:
      1. Leer pendientes  (BD abierta y cerrada)
      2. Enviar por HTTP  (SIN BD abierta)
      3. Marcar enviadas  (BD abierta y cerrada)
    Las que fallan se quedan en status=0 y se reintentan el próximo ciclo.
    """
    # ── FASE 1: leer (con BD, sin HTTP) ───────────────────────────────────
    pendientes = _leer_pendientes()
    if not pendientes:
        return

    # ── FASE 2: enviar (con HTTP, SIN BD) ─────────────────────────────────
    ids_enviados: list[int] = []
    fallidas = 0
    for id_whatsapp, mensaje, chatid in pendientes:
        ok, detalle = enviar_a_grupo(chatid, mensaje)
        if ok:
            ids_enviados.append(id_whatsapp)
        else:
            # Queda en status=0 → reintento natural el próximo ciclo.
            fallidas += 1
            logger.warning(
                "Envío fallido (id_whatsapp=%s, destino=%s): %s",
                id_whatsapp,
                chatid,
                detalle,
            )

    # ── FASE 3: marcar (con BD, sin HTTP) ─────────────────────────────────
    _marcar_enviadas(ids_enviados)

    if ids_enviados or fallidas:
        logger.info(
            "Ciclo WhatsApp: %s enviadas, %s fallidas, %s en lote.",
            len(ids_enviados),
            fallidas,
            len(pendientes),
        )


def _procesar_cola() -> None:
    """
    Ejecuta un ciclo bajo watchdog. NINGUNA excepción ni cuelgue escapa:
    si el trabajo excede CICLO_TIMEOUT_SEC, gevent.Timeout lo aborta y el
    siguiente ciclo arranca limpio.
    """
    try:
        with gevent.Timeout(CICLO_TIMEOUT_SEC):
            _trabajo_ciclo()
    except gevent.Timeout:
        logger.error(
            "Ciclo del whatsapp_worker abortado por watchdog (>%ss). "
            "Los pendientes se reintentan en el próximo ciclo.",
            CICLO_TIMEOUT_SEC,
        )
    except Exception as exc:  # el worker NUNCA debe tumbar al scheduler
        logger.error("Ciclo del whatsapp_worker falló: %s", repr(exc), exc_info=True)


def registrar_en_scheduler(sched: BackgroundScheduler) -> None:
    """
    Registra el job en el scheduler compartido (mismo patrón que
    unit_state_worker y la limpieza de tokens — ver gunicorn.conf.py).
    """
    sched.add_job(
        _procesar_cola,
        trigger="interval",
        seconds=POLL_INTERVAL,
        id="whatsapp_worker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
        replace_existing=True,
        # start_date=datetime.now(timezone.utc) + timedelta(seconds=13),
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=13),
    )
    logger.info(
        "WhatsApp Worker registrado (cada %ss, lote=%s, expira=%sh, watchdog=%ss).",
        POLL_INTERVAL,
        LOTE_MAX,
        MAX_EDAD_HORAS,
        CICLO_TIMEOUT_SEC,
    )
