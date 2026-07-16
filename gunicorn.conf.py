"""
================================================================================

Por que este archivo existe:
  Con gevent y multiples workers, create_app() se ejecuta UNA VEZ por cada
  worker hijo. Si iniciar_worker() esta dentro de create_app(), el scheduler
  del POI Worker arranca en TODOS los workers — con 4 workers, cada evento
  se detecta e inserta 4 veces.

  La solucion es iniciar el worker en el hook post_fork, pero solo en el
  primer worker (age == 1). Los demas workers procesan requests HTTP
  normalmente sin scheduler.

Hooks de gunicorn usados:
  post_fork(server, worker):
    Se ejecuta en el proceso hijo DESPUES del fork. El parametro worker.age
    es el numero de orden del worker (1, 2, 3...). Solo el worker con age=1
    inicia el scheduler.

  worker_exit(server, worker):
    Se ejecuta cuando un worker muere o es reemplazado. Detiene el scheduler
    si este worker era el que lo tenia activo.

Por que solo el worker age=1 y no el master:
  El master de gunicorn no procesa requests — solo hace fork de workers y
  los supervisa. Iniciar el scheduler en el master causaria que el scheduler
  corriera en un proceso sin acceso al contexto de Flask (sin app, sin pools
  de BD inicializados). El primer worker hijo tiene todo inicializado y es
  reemplazado automaticamente por gunicorn si muere, disparando worker_exit
  para limpiar el scheduler.

Referencia: https://docs.gunicorn.org/en/stable/settings.html#server-hooks
"""

import logging

logger = logging.getLogger(__name__)

# Gunicorn hooks para iniciar y detener el POI Worker scheduler en el worker age=1.


def post_fork(server, worker):
    """
    Hook ejecutado en el worker hijo DESPUES del fork. Inicia el scheduler
    del POI Worker solo en el primer worker (age=1). Los demas workers
    procesan requests HTTP normalmente sin scheduler.
    """
    from psycogreen.gevent import patch_psycopg

    patch_psycopg()
    # El patch_psycopg() debe ejecutarse en cada worker hijo, no en el master.
    server.log.info("psycopg2 parchado para gevent en worker pid=%s", worker.pid)

    if worker.age == 1:
        # Inicia el scheduler del POI Worker solo en el primer worker (age=1).
        try:
            from workers.poi_worker import iniciar_worker, get_scheduler
            from workers.unit_state_worker import registrar_en_scheduler

            # 1. El POI worker crea el scheduler único del proceso
            iniciar_worker()

            # 2. Nuestro job se registra en ese MISMO scheduler
            #    (dos schedulers separados bajo gevent = uno muere, ver
            #    incidente 2026-06-12)
            sched = get_scheduler()
            if sched:
                registrar_en_scheduler(sched)

            server.log.info(
                "Scheduler iniciado (jobs: POI + Unit State) en worker pid=%s age=%s",
                worker.pid,
                worker.age,
            )
        except Exception as exc:
            server.log.error(
                "Error al iniciar scheduler en worker pid=%s: %s",
                worker.pid,
                repr(exc),
            )
    else:
        logger.debug(
            "Worker pid=%s age=%s — sin POI Worker scheduler (solo HTTP)",
            worker.pid,
            worker.age,
        )


def worker_exit(server, worker):
    """
    Hook ejecutado cuando un worker muere o es reemplazado. Detiene el
    scheduler del POI Worker si este worker era el que lo tenia activo.
    """
    if worker.age == 1:
        try:
            from workers.poi_worker import detener_worker

            # Un solo detener: el scheduler es compartido, apagar el del
            # POI worker detiene también el job de Unit State.
            detener_worker()
            logger.info(
                "Scheduler detenido — worker pid=%s age=%s termino",
                worker.pid,
                worker.age,
            )
        except Exception as exc:
            logger.error(
                "Error al detener scheduler en worker_exit pid=%s: %s",
                worker.pid,
                repr(exc),
            )