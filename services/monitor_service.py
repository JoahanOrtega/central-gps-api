"""
monitor_service.py — Lógica de negocio del monitor y el histórico de cumplimiento.

Monitor en tiempo real:
  - get_monitor(): snapshot actual de todos los itinerarios del día
  - Usa pg_notify('cumplimiento_evento') para push en tiempo real (SSE)

Histórico:
  - get_historico(): itinerarios pasados con métricas de cumplimiento
  - get_historico_paradas(): detalle de paradas de un itinerario ejecutado
  - get_historico_eventos(): línea de tiempo de eventos de una ejecución
"""

import logging
from datetime import date

from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _rows_to_dicts(cur) -> list[dict]:
    """Convierte todas las filas del cursor a lista de dicts."""
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _serialize_row(record: dict) -> dict:
    """Normaliza tipos para JSON."""
    from decimal import Decimal
    from datetime import datetime, time, date

    for k, v in record.items():
        if isinstance(v, datetime):
            record[k] = v.strftime("%Y-%m-%dT%H:%M:%S-06:00")
        elif isinstance(v, time):
            record[k] = v.strftime("%H:%M")
        elif isinstance(v, date):
            record[k] = v.isoformat()
        elif isinstance(v, Decimal):
            record[k] = float(v)
    return record


# ══════════════════════════════════════════════════════════════════════════════
# MONITOR EN TIEMPO REAL
# ══════════════════════════════════════════════════════════════════════════════


def get_monitor(
    id_empresa: int,
    fecha: str | None = None,
    id_ruta: int | None = None,
    id_itinerario: int | None = None,
) -> list[dict]:
    """
    Snapshot del estado actual de todos los itinerarios programados para hoy.

    Para cada itinerario devuelve:
    - Datos del itinerario (ruta, turno, horario)
    - Unidad asignada con su estado en tiempo real (en_ruta, en_curso)
    - Métricas actualizadas por el worker (paradas_abordadas, porcentaje)
    - Parada actual, anterior y siguiente
    - Alarmas activas
    - Progreso de paradas

    Args:
        id_empresa:    Empresa del usuario.
        fecha:         Fecha a monitorear (default: hoy).
        id_ruta:       Filtro opcional por ruta.
        id_itinerario: Filtro opcional por itinerario específico.
    """
    fecha_consulta = fecha or date.today().isoformat()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa, fecha_consulta]
            clauses = []

            if id_ruta:
                clauses.append("AND r.id_ruta = %s")
                params.append(id_ruta)
            if id_itinerario:
                clauses.append("AND i.id_itinerario = %s")
                params.append(id_itinerario)

            cur.execute(
                f"""
                SELECT
                    -- Programación
                    itf.id_itinerario_fecha,
                    itf.fecha,
                    itf.fecha_hora_inicio,
                    itf.fecha_hora_fin,
                    itf.status AS status_programacion,

                    -- Itinerario base
                    i.id_itinerario,
                    i.turno,
                    i.tipo,
                    i.dias,
                    i.hora_inicio,
                    i.hora_fin,
                    i.total_paradas,
                    i.minutos_tolerancia_inicio,
                    i.minutos_tolerancia_fin,

                    -- Ruta
                    r.id_ruta,
                    r.nombre  AS nombre_ruta,
                    r.clave   AS clave_ruta,
                    l.tipo_logistica,
                    l.trace_color,
                    l.kilometros,

                    -- Unidad ejecutora (titular)
                    ifu.id_itinerario_fecha_unidad,
                    ifu.id_unidad,
                    ifu.imei,
                    ifu.tipo_asignacion,
                    ifu.status AS status_unidad,

                    -- Estado en tiempo real (actualizado por el worker)
                    ifu.en_ruta,
                    ifu.en_curso,
                    ifu.vel_max,
                    ifu.velocidad_actual,
                    ifu.fecha_hora_update,

                    -- Métricas de cumplimiento
                    ifu.paradas_abordadas,
                    ifu.paradas_omitidas,
                    ifu.porcentaje_cumplimiento,
                    ifu.porcentaje_ruta,
                    ifu.kms_servicio,
                    ifu.kms_vacio,

                    -- Hitos
                    ifu.fecha_hora_encendido,
                    ifu.fecha_hora_arranque,
                    ifu.fecha_hora_llegada_f1,
                    ifu.fecha_hora_salida_f1,
                    ifu.fecha_hora_llegada_destino,

                    -- Alarmas activas
                    ifu.alarma_encendido,
                    ifu.alarma_arranque,
                    ifu.alarma_llegada_f1,
                    ifu.alarma_retraso,
                    ifu.alarma_anticipacion,
                    ifu.alarma_desviacion,
                    ifu.alarma_parada_omitida,
                    ifu.alarma_relenti,
                    ifu.alarma_unidad_detenida,

                    -- Parada actual, anterior y siguiente
                    -- (ids guardados por el worker para el monitor)
                    ifu.id_parada_actual,
                    ifu.id_parada_anterior,
                    ifu.id_parada_siguiente,

                    -- Datos de la unidad
                    u.numero  AS numero_unidad,
                    u.marca   AS marca_unidad,

                    -- Número de apoyos asignados
                    itf.apoyos

                FROM t_itinerario_fecha itf
                INNER JOIN t_itinerarios i      ON i.id_itinerario = itf.id_itinerario
                INNER JOIN t_rutas r            ON r.id_ruta = i.id_ruta
                INNER JOIN t_logisticas_ruta l  ON l.id_logistica_ruta = i.id_logistica_ruta
                LEFT  JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha = itf.id_itinerario_fecha
                       AND ifu.tipo_asignacion = 1   -- solo titular
                       AND ifu.status != 2           -- excluir desasignadas
                LEFT  JOIN t_unidades u         ON u.id_unidad = ifu.id_unidad
                WHERE itf.id_empresa = %s
                  AND itf.fecha = %s
                  AND itf.status != 0              -- excluir cancelados
                  {"".join(clauses)}
                ORDER BY
                    i.hora_inicio,
                    r.nombre,
                    i.turno
                """,
                params,
            )
            rows = _rows_to_dicts(cur)

        return [_serialize_row(r) for r in rows]
    finally:
        release_db_connection(conn)


def get_monitor_paradas(
    id_itinerario_fecha_unidad: int,
    id_empresa: int,
) -> list[dict]:
    """
    Estado detallado de las paradas de un itinerario en ejecución.

    Usado por el frontend para mostrar el panel de paradas del monitor.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ifp.id_itinerario_fecha_parada,
                    ifp.id_parada,
                    ifp.numero,
                    ifp.hora_abordaje_programada,
                    ifp.fecha_hora_llegada,
                    ifp.fecha_hora_salida,
                    ifp.minutos_diferencia,
                    ifp.status,
                    ifp.dentro_geocerca,
                    ifp.geocerca_radio,
                    p.nombre,
                    p.latitud,
                    p.longitud,
                    p.tipo_geocerca
                FROM t_itinerario_fecha_parada ifp
                INNER JOIN t_paradas_ruta p ON p.id_parada = ifp.id_parada
                -- Verificar que pertenece a la empresa
                INNER JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha_unidad = ifp.id_itinerario_fecha_unidad
                INNER JOIN t_itinerario_fecha itf
                        ON itf.id_itinerario_fecha = ifu.id_itinerario_fecha
                WHERE ifp.id_itinerario_fecha_unidad = %s
                  AND itf.id_empresa = %s
                ORDER BY ifp.numero ASC
                """,
                (id_itinerario_fecha_unidad, id_empresa),
            )
            rows = _rows_to_dicts(cur)

        return [_serialize_row(r) for r in rows]
    finally:
        release_db_connection(conn)


# ══════════════════════════════════════════════════════════════════════════════
# HISTÓRICO
# ══════════════════════════════════════════════════════════════════════════════


def get_historico(
    id_empresa: int,
    fecha_inicio: str,
    fecha_fin: str,
    id_ruta: int | None = None,
    id_itinerario: int | None = None,
    id_unidad: int | None = None,
    status: int | None = None,
) -> list[dict]:
    """
    Listado histórico de itinerarios ejecutados con sus métricas.

    Incluye solo itinerarios que ya tienen unidad asignada (con o sin
    cumplimiento registrado). Los programados sin unidad se excluyen.

    Equivale a getHistorico() de la v2.5.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa, fecha_inicio, fecha_fin]
            clauses = []

            if id_ruta:
                clauses.append("AND r.id_ruta = %s")
                params.append(id_ruta)
            if id_itinerario:
                clauses.append("AND i.id_itinerario = %s")
                params.append(id_itinerario)
            if id_unidad:
                clauses.append("AND ifu.id_unidad = %s")
                params.append(id_unidad)
            if status is not None:
                clauses.append("AND itf.status = %s")
                params.append(status)

            cur.execute(
                f"""
                SELECT
                    -- Identificadores
                    itf.id_itinerario_fecha,
                    ifu.id_itinerario_fecha_unidad,
                    itf.fecha,

                    -- Itinerario y ruta
                    i.id_itinerario,
                    i.turno,
                    i.tipo,
                    i.total_paradas,
                    r.id_ruta,
                    r.nombre  AS nombre_ruta,
                    r.clave   AS clave_ruta,

                    -- Ventana horaria programada
                    itf.fecha_hora_inicio,
                    itf.fecha_hora_fin,
                    EXTRACT(EPOCH FROM (itf.fecha_hora_fin - itf.fecha_hora_inicio))::integer / 60
                        AS minutos_programados,

                    -- Estado
                    itf.status AS status_programacion,
                    ifu.status AS status_unidad,

                    -- Unidad
                    ifu.id_unidad,
                    ifu.imei,
                    ifu.tipo_asignacion,
                    u.numero  AS numero_unidad,
                    u.marca   AS marca_unidad,

                    -- Hitos de ejecución
                    ifu.fecha_hora_encendido,
                    ifu.fecha_hora_arranque,
                    ifu.fecha_hora_llegada_f1,
                    ifu.fecha_hora_salida_f1,
                    ifu.fecha_hora_llegada_destino,
                    ifu.fecha_hora_salida_destino,

                    -- Tiempo real de recorrido (salida F1 → llegada destino)
                    CASE
                        WHEN ifu.fecha_hora_salida_f1 IS NOT NULL
                             AND ifu.fecha_hora_llegada_destino IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (
                            ifu.fecha_hora_llegada_destino - ifu.fecha_hora_salida_f1
                        ))::integer / 60
                        ELSE NULL
                    END AS minutos_real,

                    -- Métricas de cumplimiento
                    ifu.paradas_abordadas,
                    ifu.paradas_omitidas,
                    ifu.porcentaje_cumplimiento,
                    ifu.porcentaje_ruta,
                    ifu.porcentaje_paradas,
                    ifu.kms_servicio,
                    ifu.kms_vacio,
                    ifu.kms_totales,
                    ifu.vel_max,
                    ifu.eventos_vel_max,
                    ifu.m_fuera_ruta,
                    ifu.m_fuera_ruta_pct,
                    ifu.tiempo_total,
                    ifu.tiempo_en_ruta,
                    ifu.tiempo_fuera_ruta,
                    ifu.abordajes

                FROM t_itinerario_fecha itf
                INNER JOIN t_itinerarios i      ON i.id_itinerario = itf.id_itinerario
                INNER JOIN t_rutas r            ON r.id_ruta = i.id_ruta
                INNER JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha = itf.id_itinerario_fecha
                LEFT  JOIN t_unidades u         ON u.id_unidad = ifu.id_unidad
                WHERE itf.id_empresa = %s
                  AND itf.fecha BETWEEN %s AND %s
                  {"".join(clauses)}
                ORDER BY
                    itf.fecha DESC,
                    i.hora_inicio,
                    r.nombre,
                    i.turno
                """,
                params,
            )
            rows = _rows_to_dicts(cur)

        return [_serialize_row(r) for r in rows]
    finally:
        release_db_connection(conn)


def get_historico_paradas(
    id_itinerario_fecha_unidad: int,
    id_empresa: int,
) -> list[dict]:
    """
    Detalle de paradas con tiempos reales de llegada/salida y diferencias.
    Usado para el reporte de cumplimiento individual de un itinerario.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    ifp.id_itinerario_fecha_parada,
                    ifp.id_parada,
                    ifp.numero,
                    ifp.hora_abordaje_programada,
                    ifp.fecha_hora_llegada,
                    ifp.fecha_hora_salida,
                    ifp.minutos_diferencia,
                    ifp.status,
                    p.nombre,
                    p.latitud,
                    p.longitud,
                    p.tipo_geocerca,
                    p.radio,
                    -- Tiempo de permanencia en la parada (segundos)
                    CASE
                        WHEN ifp.fecha_hora_llegada IS NOT NULL
                             AND ifp.fecha_hora_salida IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (
                            ifp.fecha_hora_salida - ifp.fecha_hora_llegada
                        ))::integer
                        ELSE NULL
                    END AS segundos_permanencia
                FROM t_itinerario_fecha_parada ifp
                INNER JOIN t_paradas_ruta p ON p.id_parada = ifp.id_parada
                INNER JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha_unidad = ifp.id_itinerario_fecha_unidad
                INNER JOIN t_itinerario_fecha itf
                        ON itf.id_itinerario_fecha = ifu.id_itinerario_fecha
                WHERE ifp.id_itinerario_fecha_unidad = %s
                  AND itf.id_empresa = %s
                ORDER BY ifp.numero ASC
                """,
                (id_itinerario_fecha_unidad, id_empresa),
            )
            rows = _rows_to_dicts(cur)

        return [_serialize_row(r) for r in rows]
    finally:
        release_db_connection(conn)


def get_historico_eventos(
    id_itinerario_fecha_unidad: int,
    id_empresa: int,
) -> list[dict]:
    """
    Línea de tiempo de eventos GPS de una ejecución.
    Muestra cada llegada, salida y stop registrado por el worker.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    e.id_evento,
                    e.evento,
                    CASE e.evento
                        WHEN 1 THEN 'llegada'
                        WHEN 2 THEN 'salida'
                        WHEN 3 THEN 'abordaje'
                        WHEN 4 THEN 'inicio_stop'
                        WHEN 5 THEN 'fin_stop'
                        ELSE 'desconocido'
                    END AS tipo_evento,
                    e.fecha_hora_gps,
                    e.latitud,
                    e.longitud,
                    e.velocidad,
                    e.odometro,
                    -- Parada relacionada
                    ifp.id_parada,
                    ifp.numero AS numero_parada,
                    p.nombre   AS nombre_parada
                FROM t_itinerario_fecha_parada_eventos e
                INNER JOIN t_itinerario_fecha_parada ifp
                        ON ifp.id_itinerario_fecha_parada = e.id_itinerario_fecha_parada
                INNER JOIN t_paradas_ruta p
                        ON p.id_parada = ifp.id_parada
                INNER JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha_unidad = e.id_itinerario_fecha_unidad
                INNER JOIN t_itinerario_fecha itf
                        ON itf.id_itinerario_fecha = ifu.id_itinerario_fecha
                WHERE e.id_itinerario_fecha_unidad = %s
                  AND itf.id_empresa = %s
                ORDER BY e.fecha_hora_gps ASC
                """,
                (id_itinerario_fecha_unidad, id_empresa),
            )
            rows = _rows_to_dicts(cur)

        return [_serialize_row(r) for r in rows]
    finally:
        release_db_connection(conn)
