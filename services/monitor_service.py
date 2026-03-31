from db.connection import get_db_connection
from services.telemetry_service import (
    get_latest_positions_by_imeis,
    get_latest_position_by_imei,
)


def get_units_with_latest_telemetry(search=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_unidad,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                imei,
                chip,
                id_operador,
                status
            FROM t_unidades
            WHERE status = 1
        """

        params = []

        if search:
            query += " AND LOWER(numero) LIKE LOWER(%s)"
            params.append(f"%{search}%")

        query += " ORDER BY id_unidad ASC;"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        units = []
        imeis = []

        for row in rows:
            unit_imei = str(row[8]).strip() if row[8] else ""

            unit = {
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
            }

            units.append(unit)

            if unit_imei:
                imeis.append(unit_imei)

        latest_positions = get_latest_positions_by_imeis(imeis)
        telemetry_map = {item["imei"]: item for item in latest_positions}

        result = []
        for unit in units:
            result.append({
                **unit,
                "telemetry": telemetry_map.get(unit["imei"])
            })

        return result

    except Exception as error:
        print("ERROR EN get_units_with_latest_telemetry:", repr(error))
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_unit_summary_by_imei(imei):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_unidad,
                numero,
                marca,
                modelo,
                imei
            FROM t_unidades
            WHERE imei = %s
            LIMIT 1;
        """

        cursor.execute(query, (imei,))
        row = cursor.fetchone()

        if not row:
            return None

        clean_imei = str(row[4]).strip() if row[4] else ""
        latest_telemetry = get_latest_position_by_imei(clean_imei)

        return {
            "id": row[0],
            "numero": row[1],
            "imei": clean_imei,
            "marca": row[2] or "",
            "modelo": row[3] or "",
            "status": latest_telemetry.get("status", "Sin información") if latest_telemetry else "Sin información",
            "last_report": latest_telemetry.get("fecha_hora_gps") if latest_telemetry else None,
            "hasTelemetry": latest_telemetry is not None,
        }

    except Exception as error:
        print("ERROR EN get_unit_summary_by_imei:", repr(error))
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()