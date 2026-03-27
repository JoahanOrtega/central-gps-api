from db.connection import get_db_connection
from services.telemetry_service import get_latest_positions_by_imeis


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
            unit = {
                "id": row[0],
                "numero": row[1],
                "marca": row[2],
                "modelo": row[3],
                "anio": row[4],
                "matricula": row[5],
                "tipo": row[6],
                "imagen": row[7],
                "imei": row[8],
                "chip": row[9],
                "id_operador": row[10],
                "status": row[11],
            }

            units.append(unit)

            if row[8]:
                imeis.append(row[8])

        latest_positions = get_latest_positions_by_imeis(imeis)
        telemetry_map = {item["imei"]: item for item in latest_positions}

        result = []
        for unit in units:
            result.append({**unit, "telemetry": telemetry_map.get(unit["imei"])})

        return result

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
