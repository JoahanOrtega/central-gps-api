"""
monitor_service.py — Servicio de monitoreo en vivo de unidades

Retorna unidades con su última telemetría lista para mostrar en el mapa.
Los campos vel_max, operador y grupo se incluyen para que el drawer de
unidades pueda mostrarlos sin peticiones adicionales.
"""

import logging
from db.connection import get_db_connection, release_db_connection
from services.telemetry_service import (
    get_latest_positions_by_imeis,
    get_latest_position_by_imei,
    to_app_iso,
)

logger = logging.getLogger(__name__)


def get_units_with_latest_telemetry(
    id_empresa: int, search: str | None = None
) -> list[dict]:
    """
    Retorna todas las unidades activas de la empresa con su última telemetría.

    Incluye:
      - vel_max     → para colorear la velocidad en el drawer y en el recorrido
      - operador    → nombre del operador asignado (o None si no tiene)
      - grupo       → nombre del grupo de unidades (o 'Sin Grupo' si no tiene)

    JOIN con t_operadores y t_grupos_unidades — LEFT JOIN para no excluir
    unidades sin operador o sin grupo asignado.
    """
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                u.id_unidad,
                u.numero                              AS numero,
                u.marca,
                u.modelo,
                u.anio,
                u.matricula,
                u.tipo,
                u.imagen,
                u.imei,
                u.chip,
                u.id_operador,
                u.status,
                u.vel_max,
                o.nombre                        AS operador,
                COALESCE(g.nombre, 'Sin Grupo') AS grupo
            FROM t_unidades u
            LEFT JOIN r_unidad_operador         ro ON ro.id_unidad_operador = u.id_unidad_operador
            LEFT JOIN t_operadores              o  ON o.id_operador         = ro.id_operador
            LEFT JOIN r_grupo_unidades_unidades rg ON rg.id_unidad          = u.id_unidad
            LEFT JOIN t_grupos_unidades         g  ON g.id_grupo_unidades   = rg.id_grupo_unidades
            WHERE u.id_empresa = %s AND u.status = 1
        """
        params = [id_empresa]

        if search:
            query += """
                AND (
                    LOWER(u.numero)   LIKE LOWER(%s) OR
                    LOWER(u.marca)    LIKE LOWER(%s) OR
                    LOWER(u.modelo)   LIKE LOWER(%s) OR
                    LOWER(o.nombre)   LIKE LOWER(%s)
                )
            """
            like = f"%{search}%"
            params.extend([like, like, like, like])

        # Ordenar por grupo y luego por número de unidad
        query += " ORDER BY COALESCE(g.nombre, 'zzz') ASC, u.numero ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        units = []
        imeis = []
        for row in rows:
            unit_imei = str(row[8]).strip() if row[8] else ""
            units.append(
                {
                    "id": row[0],
                    "numero": row[1],
                    "marca": row[2],
                    "modelo": row[3],
                    "anio": row[4],
                    "matricula": row[5],
                    "tipo": row[6],
                    "imagen": row[7],
                    "imei": unit_imei,
                    "chip": row[9],
                    "id_operador": row[10],
                    "status": row[11],
                    "vel_max": float(row[12]) if row[12] is not None else None,
                    "operador": row[13],
                    "grupo": row[14],
                }
            )
            if unit_imei:
                imeis.append(unit_imei)

        # Una sola query de telemetría para todas las unidades
        telemetry_map = {t["imei"]: t for t in get_latest_positions_by_imeis(imeis)}

        return [
            {**unit, "telemetry": telemetry_map.get(unit["imei"])} for unit in units
        ]

    except Exception:
        logger.exception(
            "Error en get_units_with_latest_telemetry id_empresa=%s", id_empresa
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_unit_summary_by_imei(imei: str, id_empresa: int) -> dict | None:
    """
    Retorna el resumen de una unidad para el TripDrawer:
    datos básicos de la unidad + última telemetría.
    """
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id_unidad, numero, marca, modelo, imei, vel_max
            FROM t_unidades
            WHERE imei = %s AND id_empresa = %s AND status = 1
            LIMIT 1
            """,
            (imei, id_empresa),
        )
        row = cursor.fetchone()
        if not row:
            return None

        clean_imei = str(row[4]).strip() if row[4] else ""
        telemetry = get_latest_position_by_imei(clean_imei)

        return {
            "id": row[0],
            "numero": row[1],
            "marca": row[2],
            "modelo": row[3],
            "imei": clean_imei,
            "vel_max": float(row[5]) if row[5] is not None else None,
            "last_report": telemetry.get("fecha_hora_gps") if telemetry else None,
            "status": (
                telemetry.get("status", "Sin información")
                if telemetry
                else "Sin información"
            ),
            "hasTelemetry": telemetry is not None,
        }

    except Exception:
        logger.exception("Error en get_unit_summary_by_imei imei=%s", imei)
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
