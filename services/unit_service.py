from db.connection import get_db_connection


def get_units():
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
            ORDER BY id_unidad ASC;
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        units = []
        for row in rows:
            units.append({
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
            })

        return units

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()