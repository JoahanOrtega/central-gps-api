"""
compliance_service.py — Lógica de negocio del módulo de Cumplimiento (Entrega 3A).

Cubre la capa de Programación:
  - Crear/consultar t_itinerario_fecha (programar itinerario para una fecha)
  - Asignar/desasignar unidad ejecutora (t_itinerario_fecha_unidad)
  - Inicializar paradas con geocercas PostGIS (t_itinerario_fecha_parada)
  - Cancelar programación

Las métricas de cumplimiento (paradas abordadas, km, alarmas) las actualiza
el worker Python de la Entrega 3B directamente en la BD.
"""

import json
import logging
from datetime import datetime, date, time

from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


# ── Constantes ────────────────────────────────────────────────────────────────

# Estados de t_itinerario_fecha
STATUS_PROGRAMADO = 1
STATUS_EN_CURSO = 2
STATUS_COMPLETADO = 3
STATUS_CANCELADO = 0

# Tipos de asignación de unidad
TIPO_TITULAR = 1
TIPO_APOYO = 2


# ── Helpers internos ──────────────────────────────────────────────────────────


def _row_to_dict(cur, row: tuple) -> dict:
    return dict(zip([d[0] for d in cur.description], row))


def _serialize(record: dict) -> dict:
    """Normaliza fechas, horas y decimales para JSON."""
    from decimal import Decimal

    for k, v in record.items():
        if isinstance(v, datetime):
            record[k] = v.strftime("%Y-%m-%dT%H:%M:%S-06:00")
        elif isinstance(v, time):
            # TIME de PostgreSQL llega como datetime.time → "HH:MM"
            record[k] = v.strftime("%H:%M")
        elif isinstance(v, date):
            record[k] = v.isoformat()
        elif isinstance(v, Decimal):
            # NUMERIC/DECIMAL de PostgreSQL → float
            record[k] = float(v)
    return record


# ══════════════════════════════════════════════════════════════════════════════
# PROGRAMACIÓN — t_itinerario_fecha
# ══════════════════════════════════════════════════════════════════════════════


def get_programacion(
    id_empresa: int,
    fecha_inicio: str,
    fecha_fin: str,
    id_itinerario: int | None = None,
    id_ruta: int | None = None,
    status: int | None = None,
) -> list[dict]:
    """
    Lista la programación de itinerarios para un rango de fechas.

    Útil para el calendario del módulo de cumplimiento y para que el
    worker sepa qué itinerarios evaluar cada día.

    Args:
        id_empresa:    Empresa del usuario autenticado.
        fecha_inicio:  Inicio del rango (YYYY-MM-DD).
        fecha_fin:     Fin del rango (YYYY-MM-DD).
        id_itinerario: Filtro opcional por itinerario.
        id_ruta:       Filtro opcional por ruta.
        status:        Filtro opcional por estado (0-3).
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa, fecha_inicio, fecha_fin]
            clauses = []

            if id_itinerario:
                clauses.append("AND itf.id_itinerario = %s")
                params.append(id_itinerario)
            if id_ruta:
                clauses.append("AND i.id_ruta = %s")
                params.append(id_ruta)
            if status is not None:
                clauses.append("AND itf.status = %s")
                params.append(status)

            cur.execute(
                f"""
                SELECT
                    itf.id_itinerario_fecha,
                    itf.id_itinerario,
                    itf.fecha,
                    itf.fecha_hora_inicio,
                    itf.fecha_hora_fin,
                    itf.status,
                    itf.apoyos,
                    i.turno,
                    i.hora_inicio,
                    i.hora_fin,
                    i.dias,
                    i.total_paradas,
                    r.nombre  AS nombre_ruta,
                    r.clave   AS clave_ruta,
                    -- Unidad titular asignada (si existe)
                    u.id_unidad,
                    u.numero  AS numero_unidad,
                    u.marca   AS marca_unidad,
                    ifu.id_itinerario_fecha_unidad,
                    ifu.imei,
                    ifu.porcentaje_cumplimiento,
                    ifu.paradas_abordadas,
                    ifu.paradas_omitidas,
                    ifu.en_ruta,
                    ifu.en_curso
                FROM t_itinerario_fecha itf
                INNER JOIN t_itinerarios i     ON i.id_itinerario = itf.id_itinerario
                INNER JOIN t_rutas r           ON r.id_ruta = i.id_ruta
                LEFT  JOIN t_itinerario_fecha_unidad ifu
                        ON ifu.id_itinerario_fecha = itf.id_itinerario_fecha
                       AND ifu.tipo_asignacion = {TIPO_TITULAR}
                LEFT  JOIN t_unidades u        ON u.id_unidad = ifu.id_unidad
                WHERE itf.id_empresa = %s
                  AND itf.fecha BETWEEN %s AND %s
                  {"".join(clauses)}
                ORDER BY itf.fecha, i.hora_inicio, i.turno
                """,
                params,
            )
            cols = [d[0] for d in cur.description]
            return [_serialize(dict(zip(cols, row))) for row in cur.fetchall()]
    finally:
        release_db_connection(conn)


def get_programacion_by_id(id_itinerario_fecha: int, id_empresa: int) -> dict | None:
    """
    Detalle completo de una programación con sus paradas y estado actual.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    itf.id_itinerario_fecha,
                    itf.id_itinerario,
                    itf.fecha,
                    itf.fecha_hora_inicio,
                    itf.fecha_hora_fin,
                    itf.status,
                    itf.apoyos,
                    i.turno, i.hora_inicio, i.hora_fin, i.dias, i.total_paradas,
                    r.nombre AS nombre_ruta, r.clave AS clave_ruta,
                    l.tipo_logistica
                FROM t_itinerario_fecha itf
                INNER JOIN t_itinerarios i     ON i.id_itinerario = itf.id_itinerario
                INNER JOIN t_rutas r           ON r.id_ruta = i.id_ruta
                INNER JOIN t_logisticas_ruta l ON l.id_logistica_ruta = i.id_logistica_ruta
                WHERE itf.id_itinerario_fecha = %s
                  AND itf.id_empresa = %s
                """,
                (id_itinerario_fecha, id_empresa),
            )
            row = cur.fetchone()
            if not row:
                return None

            prog = _serialize(_row_to_dict(cur, row))

            # Unidades asignadas (titular + apoyos)
            cur.execute(
                """
                SELECT
                    ifu.id_itinerario_fecha_unidad,
                    ifu.id_unidad,
                    ifu.imei,
                    ifu.tipo_asignacion,
                    ifu.status,
                    ifu.porcentaje_cumplimiento,
                    ifu.paradas_abordadas,
                    ifu.paradas_omitidas,
                    ifu.en_ruta,
                    ifu.en_curso,
                    ifu.vel_max,
                    ifu.kms_servicio,
                    ifu.fecha_hora_llegada_f1,
                    ifu.fecha_hora_salida_f1,
                    ifu.fecha_hora_llegada_destino,
                    u.numero AS numero_unidad,
                    u.marca  AS marca_unidad
                FROM t_itinerario_fecha_unidad ifu
                INNER JOIN t_unidades u ON u.id_unidad = ifu.id_unidad
                WHERE ifu.id_itinerario_fecha = %s
                ORDER BY ifu.tipo_asignacion, ifu.fecha_registro
                """,
                (id_itinerario_fecha,),
            )
            ucols = [d[0] for d in cur.description]
            prog["unidades"] = [_serialize(dict(zip(ucols, r))) for r in cur.fetchall()]

            # Paradas con estado
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
                    ifp.geocerca_radio,
                    p.nombre,
                    p.latitud,
                    p.longitud,
                    p.tipo_geocerca
                FROM t_itinerario_fecha_parada ifp
                INNER JOIN t_paradas_ruta p ON p.id_parada = ifp.id_parada
                WHERE ifp.id_itinerario_fecha_unidad IN (
                    SELECT id_itinerario_fecha_unidad
                    FROM t_itinerario_fecha_unidad
                    WHERE id_itinerario_fecha = %s
                      AND tipo_asignacion = %s
                )
                ORDER BY ifp.numero
                """,
                (id_itinerario_fecha, TIPO_TITULAR),
            )
            pcols = [d[0] for d in cur.description]
            prog["paradas"] = [_serialize(dict(zip(pcols, r))) for r in cur.fetchall()]

            return prog
    finally:
        release_db_connection(conn)


def crear_programacion(
    id_itinerario: int,
    fecha: str,
    id_empresa: int,
    id_usuario: int,
) -> dict:
    """
    Programa un itinerario para una fecha concreta.

    Crea t_itinerario_fecha. No asigna unidad aún — eso se hace
    con asignar_unidad() después.

    Returns:
        { id_itinerario_fecha, ya_existia }
        ya_existia=True si el itinerario ya estaba programado para esa fecha.

    Raises:
        ValueError si el itinerario no existe o no pertenece a la empresa.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificar que el itinerario existe y obtener sus horarios
            cur.execute(
                """
                SELECT id_itinerario, hora_inicio, hora_fin
                FROM t_itinerarios
                WHERE id_itinerario = %s AND id_empresa = %s AND status = 1
                """,
                (id_itinerario, id_empresa),
            )
            itin = cur.fetchone()
            if not itin:
                raise ValueError(f"Itinerario {id_itinerario} no encontrado")

            _, hora_inicio, hora_fin = itin

            # Calcular ventana horaria absoluta
            fecha_hora_inicio = f"{fecha} {hora_inicio}" if hora_inicio else None
            fecha_hora_fin = f"{fecha} {hora_fin}" if hora_fin else None

            # Crear o recuperar la programación (idempotente)
            cur.execute(
                """
                INSERT INTO t_itinerario_fecha
                    (id_itinerario, id_empresa, fecha,
                     fecha_hora_inicio, fecha_hora_fin,
                     status, id_usuario_registro)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id_itinerario, fecha) DO NOTHING
                RETURNING id_itinerario_fecha
                """,
                (
                    id_itinerario,
                    id_empresa,
                    fecha,
                    fecha_hora_inicio,
                    fecha_hora_fin,
                    STATUS_PROGRAMADO,
                    id_usuario,
                ),
            )
            row = cur.fetchone()

            if row:
                id_itinerario_fecha = row[0]
                ya_existia = False
            else:
                # Ya existía — recuperar el id
                cur.execute(
                    "SELECT id_itinerario_fecha FROM t_itinerario_fecha "
                    "WHERE id_itinerario = %s AND fecha = %s",
                    (id_itinerario, fecha),
                )
                id_itinerario_fecha = cur.fetchone()[0]
                ya_existia = True

            conn.commit()
            return {
                "id_itinerario_fecha": id_itinerario_fecha,
                "ya_existia": ya_existia,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def cancelar_programacion(id_itinerario_fecha: int, id_empresa: int) -> bool:
    """
    Cancela la programación de un itinerario para una fecha (status=0).
    No elimina el registro — mantiene el historial.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_itinerario_fecha
                SET status = %s, fecha_cambio = CURRENT_TIMESTAMP
                WHERE id_itinerario_fecha = %s
                  AND id_empresa = %s
                  AND status != %s
                """,
                (STATUS_CANCELADO, id_itinerario_fecha, id_empresa, STATUS_CANCELADO),
            )
            updated = cur.rowcount > 0
            conn.commit()
            return updated
    finally:
        release_db_connection(conn)


# ══════════════════════════════════════════════════════════════════════════════
# ASIGNACIÓN DE UNIDAD — t_itinerario_fecha_unidad + t_itinerario_fecha_parada
# ══════════════════════════════════════════════════════════════════════════════


def asignar_unidad(
    id_itinerario_fecha: int,
    id_unidad: int,
    tipo_asignacion: int,
    id_empresa: int,
    id_usuario: int,
) -> int:
    """
    Asigna una unidad a una programación e inicializa sus paradas con
    las geocercas PostGIS copiadas de t_paradas_ruta.

    Al crear t_itinerario_fecha_parada, copia las columnas de geometría
    de las paradas base para que el worker de la 3B pueda usar
    ST_DWithin() sin hacer JOIN a t_paradas_ruta en cada ping.

    Returns:
        id_itinerario_fecha_unidad creado.

    Raises:
        ValueError si la programación no existe, no pertenece a la empresa,
        o si ya hay una unidad titular asignada y se intenta asignar otra.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificar que la programación existe y es de la empresa
            cur.execute(
                """
                SELECT itf.id_itinerario_fecha, i.id_itinerario,
                       i.id_logistica_ruta
                FROM t_itinerario_fecha itf
                INNER JOIN t_itinerarios i ON i.id_itinerario = itf.id_itinerario
                WHERE itf.id_itinerario_fecha = %s
                  AND itf.id_empresa = %s
                  AND itf.status IN (%s, %s)
                """,
                (id_itinerario_fecha, id_empresa, STATUS_PROGRAMADO, STATUS_EN_CURSO),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"Programación {id_itinerario_fecha} no encontrada o cancelada"
                )
            _, _, id_logistica_ruta = row

            # Si es titular, verificar que no haya otra unidad titular
            if tipo_asignacion == TIPO_TITULAR:
                cur.execute(
                    """
                    SELECT 1 FROM t_itinerario_fecha_unidad
                    WHERE id_itinerario_fecha = %s
                      AND tipo_asignacion = %s
                      AND status != 2
                    """,
                    (id_itinerario_fecha, TIPO_TITULAR),
                )
                if cur.fetchone():
                    raise ValueError(
                        "Ya hay una unidad titular asignada a esta programación"
                    )

            # Obtener IMEI de la unidad
            cur.execute(
                "SELECT imei FROM t_unidades WHERE id_unidad = %s",
                (id_unidad,),
            )
            imei_row = cur.fetchone()
            imei = imei_row[0] if imei_row else None

            # Crear el registro de unidad
            cur.execute(
                """
                INSERT INTO t_itinerario_fecha_unidad
                    (id_itinerario_fecha, id_unidad, imei,
                     tipo_asignacion, id_usuario_registro)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id_itinerario_fecha_unidad
                """,
                (id_itinerario_fecha, id_unidad, imei, tipo_asignacion, id_usuario),
            )
            id_ifu = cur.fetchone()[0]

            # Inicializar paradas con geocercas PostGIS.
            # ST_MakePoint(longitud, latitud) — PostGIS usa (x=lng, y=lat)
            cur.execute(
                """
                INSERT INTO t_itinerario_fecha_parada
                    (id_itinerario_fecha_unidad, id_parada, numero,
                     hora_abordaje_programada,
                     geocerca_punto, geocerca_radio, geocerca_poligono,
                     status)
                SELECT
                    %s,
                    p.id_parada,
                    p.numero,
                    rip.hora_abordaje,
                    -- Geocerca circular: convertir lat/lng a GEOGRAPHY Point
                    ST_SetSRID(
                        ST_MakePoint(p.longitud::float, p.latitud::float),
                        4326
                    )::geography,
                    p.radio,
                    -- Geocerca poligonal: se llena en 3B cuando el tipo es poligonal/rectangular.
                    -- Por ahora NULL — el worker usará geocerca_punto + geocerca_radio
                    -- para paradas circulares (que son la mayoría).
                    NULL,
                    0  -- status pendiente
                FROM r_itinerario_paradas rip
                INNER JOIN t_paradas_ruta p ON p.id_parada = rip.id_parada
                WHERE rip.id_itinerario = (
                    SELECT id_itinerario FROM t_itinerario_fecha
                    WHERE id_itinerario_fecha = %s
                )
                ORDER BY p.numero
                """,
                (id_ifu, id_itinerario_fecha),
            )

            # Actualizar status de la programación a "en curso" si es titular
            if tipo_asignacion == TIPO_TITULAR:
                cur.execute(
                    """
                    UPDATE t_itinerario_fecha
                    SET status = %s, fecha_cambio = CURRENT_TIMESTAMP
                    WHERE id_itinerario_fecha = %s
                      AND status = %s
                    """,
                    (STATUS_EN_CURSO, id_itinerario_fecha, STATUS_PROGRAMADO),
                )

            conn.commit()
            return id_ifu

    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def desasignar_unidad(
    id_itinerario_fecha_unidad: int,
    id_empresa: int,
) -> bool:
    """
    Desasigna una unidad de una programación (status=2 = cancelado).
    No elimina el registro para mantener el historial.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificar que pertenece a la empresa
            cur.execute(
                """
                UPDATE t_itinerario_fecha_unidad ifu
                SET status = 2, fecha_cambio = CURRENT_TIMESTAMP
                FROM t_itinerario_fecha itf
                WHERE ifu.id_itinerario_fecha = itf.id_itinerario_fecha
                  AND ifu.id_itinerario_fecha_unidad = %s
                  AND itf.id_empresa = %s
                  AND ifu.status = 0
                """,
                (id_itinerario_fecha_unidad, id_empresa),
            )
            updated = cur.rowcount > 0
            conn.commit()
            return updated
    finally:
        release_db_connection(conn)
