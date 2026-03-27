from db.connection import get_db_telemetry_connection


def get_latest_positions_by_imeis(imeis: list[str]):
    if not imeis:
        return []

    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        query = """
            SELECT DISTINCT ON (imei)
                imei,
                fecha_hora_gps,
                latitud,
                longitud,
                velocidad,
                grados,
                status,
                voltaje,
                voltaje_bateria,
                odometro,
                tipo_alerta
            FROM t_data
            WHERE imei = ANY(%s)
            ORDER BY imei, fecha_hora_gps DESC;
        """

        cursor.execute(query, (imeis,))
        rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append(
                {
                    "imei": row[0],
                    "fecha_hora_gps": row[1].isoformat() if row[1] else None,
                    "latitud": float(row[2]) if row[2] is not None else None,
                    "longitud": float(row[3]) if row[3] is not None else None,
                    "velocidad": float(row[4]) if row[4] is not None else None,
                    "grados": float(row[5]) if row[5] is not None else None,
                    "status": row[6],
                    "voltaje": float(row[7]) if row[7] is not None else None,
                    "voltaje_bateria": float(row[8]) if row[8] is not None else None,
                    "odometro": row[9],
                    "tipo_alerta": row[10],
                }
            )

        return items

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_positions_history_by_imei(imei: str, start_date, end_date, limit: int = 500):
    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_data,
                fecha_hora_gps,
                imei,
                latitud,
                longitud,
                velocidad,
                grados,
                status,
                voltaje,
                voltaje_bateria,
                odometro,
                tipo_alerta
            FROM t_data
            WHERE imei = %s
              AND fecha_hora_gps >= %s
              AND fecha_hora_gps <= %s
            ORDER BY fecha_hora_gps DESC
            LIMIT %s;
        """

        cursor.execute(query, (imei, start_date, end_date, limit))
        rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append(
                {
                    "id_data": row[0],
                    "fecha_hora_gps": row[1].isoformat() if row[1] else None,
                    "imei": row[2],
                    "latitud": float(row[3]) if row[3] is not None else None,
                    "longitud": float(row[4]) if row[4] is not None else None,
                    "velocidad": float(row[5]) if row[5] is not None else None,
                    "grados": float(row[6]) if row[6] is not None else None,
                    "status": row[7],
                    "voltaje": float(row[8]) if row[8] is not None else None,
                    "voltaje_bateria": float(row[9]) if row[9] is not None else None,
                    "odometro": row[10],
                    "tipo_alerta": row[11],
                }
            )

        return items

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_latest_position_by_imei(imei: str):
    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_data,
                fecha_hora_sistema,
                fecha_hora_gmt,
                fecha_hora_gps,
                imei,
                tipo_alerta,
                latitud,
                longitud,
                velocidad,
                grados,
                status,
                voltaje,
                adc,
                voltaje_bateria,
                odometro,
                rfid,
                data,
                tipo_dato,
                tipo_reporte,
                numero_reporte,
                id_ear,
                atributos
            FROM t_data
            WHERE imei = %s
            ORDER BY fecha_hora_gps DESC
            LIMIT 1;
        """

        cursor.execute(query, (imei,))
        row = cursor.fetchone()

        if not row:
            return None

        return {
            "id_data": row[0],
            "fecha_hora_sistema": row[1].isoformat() if row[1] else None,
            "fecha_hora_gmt": row[2].isoformat() if row[2] else None,
            "fecha_hora_gps": row[3].isoformat() if row[3] else None,
            "imei": row[4],
            "tipo_alerta": row[5],
            "latitud": float(row[6]) if row[6] is not None else None,
            "longitud": float(row[7]) if row[7] is not None else None,
            "velocidad": float(row[8]) if row[8] is not None else None,
            "grados": float(row[9]) if row[9] is not None else None,
            "status": row[10],
            "voltaje": float(row[11]) if row[11] is not None else None,
            "adc": float(row[12]) if row[12] is not None else None,
            "voltaje_bateria": float(row[13]) if row[13] is not None else None,
            "odometro": row[14],
            "rfid": row[15],
            "data": row[16],
            "tipo_dato": row[17],
            "tipo_reporte": row[18],
            "numero_reporte": row[19],
            "id_ear": row[20],
            "atributos": row[21],
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
