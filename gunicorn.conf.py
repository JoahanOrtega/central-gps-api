"""
gunicorn.conf.py — Configuracion del servidor gunicorn para CentralGPS
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

# ── Variables de entorno leidas por el Dockerfile CMD ────────────────────────
# Estos valores son defaults — el docker-compose.yml los sobreescribe via
# la seccion environment del servicio api.
#
# No se definen aqui para no duplicar la fuente de verdad — solo se usan
# en el CMD del Dockerfile como ${WORKERS:-4}.


def post_fork(server, worker):
    """
    Hook ejecutado en el proceso hijo despues del fork.

    Solo el worker con age=1 inicia el POI Worker scheduler.
    Los demas workers (age > 1) procesan requests HTTP sin scheduler.

    Args:
        server: Instancia del servidor gunicorn (proceso master).
        worker: Instancia del worker recien creado.
    """
    if worker.age == 1:
        # Importacion diferida — los modulos del proyecto no estan disponibles
        # en el scope global de este archivo (se ejecuta antes de que Flask
        # inicialice la app). El import aqui garantiza que los pools de BD
        # ya esten listos cuando el scheduler intente conectarse.
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

            logger.info(
                "Scheduler iniciado (jobs: POI + Unit State) en worker pid=%s age=%s",
                worker.pid,
                worker.age,
            )
        except Exception as exc:
            logger.error(
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
    Hook ejecutado cuando un worker termina (muerte natural, SIGTERM, restart).

    Detiene el scheduler si este worker era el que lo tenia activo.
    Sin esto, el scheduler queda en estado zombie cuando gunicorn reemplaza
    el worker age=1 tras un timeout o un crash.

    Args:
        server: Instancia del servidor gunicorn.
        worker: Instancia del worker que esta terminando.
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
