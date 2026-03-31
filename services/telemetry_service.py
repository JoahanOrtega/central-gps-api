from datetime import datetime, timedelta, time
from math import radians, sin, cos, sqrt, atan2
from db.connection import get_db_telemetry_connection


def haversine_km(lat1, lon1, lat2, lon2):
    earth_radius_km = 6371.0

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return earth_radius_km * c


def map_route_row(row):
    return {
        "fecha_hora_gps": row[0].isoformat() if row[0] else None,
        "latitud": float(row[1]) if row[1] is not None else None,
        "longitud": float(row[2]) if row[2] is not None else None,
        "velocidad": float(row[3]) if row[3] is not None else None,
        "grados": float(row[4]) if row[4] is not None else None,
        "status": row[5],
    }


def get_day_range(day_offset=0):
    target_day = datetime.now().date() - timedelta(days=day_offset)
    start_dt = datetime.combine(target_day, time.min)
    end_dt = datetime.combine(target_day, time.max)
    return start_dt, end_dt


def get_latest_position_by_imei(imei):
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
            FROM public.t_data
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


def get_latest_positions_by_imeis(imeis):
    if not imeis:
        return []

    filtered_imeis = [imei for imei in imeis if imei]

    if not filtered_imeis:
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
            FROM public.t_data
            WHERE imei = ANY(%s::varchar[])
            ORDER BY imei, fecha_hora_gps DESC;
        """

        cursor.execute(query, (filtered_imeis,))
        rows = cursor.fetchall()

        items = []
        for row in rows:
            items.append({
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
            })

        return items

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_positions_history_by_imei(imei, start_date, end_date, limit=500):
    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                fecha_hora_gps,
                latitud,
                longitud,
                velocidad,
                grados,
                status
            FROM public.t_data
            WHERE imei = %s
              AND fecha_hora_gps >= %s
              AND fecha_hora_gps <= %s
              AND latitud IS NOT NULL
              AND longitud IS NOT NULL
            ORDER BY fecha_hora_gps ASC
            LIMIT %s;
        """

        cursor.execute(query, (imei, start_date, end_date, limit))
        rows = cursor.fetchall()

        return [map_route_row(row) for row in rows]

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_route_by_mode(imei, mode):
    if mode == "today":
        start_date, end_date = get_day_range(0)
        return get_positions_history_by_imei(imei, start_date, end_date, 2000)

    if mode == "yesterday":
        start_date, end_date = get_day_range(1)
        return get_positions_history_by_imei(imei, start_date, end_date, 2000)

    if mode == "day_before_yesterday":
        start_date, end_date = get_day_range(2)
        return get_positions_history_by_imei(imei, start_date, end_date, 2000)

    if mode == "latest":
        recent_trips = get_recent_trips_by_imei(imei, limit=1)

        if not recent_trips:
            return []

        latest_trip = recent_trips[0]

        return get_positions_history_by_imei(
            imei,
            latest_trip["start_time"],
            latest_trip["end_time"],
            2000,
        )

    return []


def get_recent_trips_by_imei(imei, limit=10):
    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        start_date = datetime.now() - timedelta(days=2)
        end_date = datetime.now()

        query = """
            SELECT
                fecha_hora_gps,
                latitud,
                longitud,
                velocidad,
                grados,
                status
            FROM public.t_data
            WHERE imei = %s
              AND fecha_hora_gps >= %s
              AND fecha_hora_gps <= %s
              AND latitud IS NOT NULL
              AND longitud IS NOT NULL
            ORDER BY fecha_hora_gps ASC;
        """

        cursor.execute(query, (imei, start_date, end_date))
        rows = cursor.fetchall()

        if not rows:
            return []

        trips = []
        current_trip = []

        for row in rows:
            if not current_trip:
                current_trip.append(row)
                continue

            previous_row = current_trip[-1]
            current_time = row[0]
            previous_time = previous_row[0]

            if (current_time - previous_time).total_seconds() > 600:
                if len(current_trip) >= 2:
                    trips.append(current_trip)
                current_trip = [row]
                continue

            current_trip.append(row)

        if len(current_trip) >= 2:
            trips.append(current_trip)

        recent_trip_items = []

        recent_trips = list(reversed(trips))[:limit]

        for index, trip_rows in enumerate(recent_trips, start=1):
            start_row = trip_rows[0]
            end_row = trip_rows[-1]

            distance_km = 0.0

            for point_index in range(1, len(trip_rows)):
                prev_point = trip_rows[point_index - 1]
                next_point = trip_rows[point_index]

                distance_km += haversine_km(
                    float(prev_point[1]),
                    float(prev_point[2]),
                    float(next_point[1]),
                    float(next_point[2]),
                )

            duration_seconds = int((end_row[0] - start_row[0]).total_seconds())

            trip_date = start_row[0].date()
            today = datetime.now().date()
            yesterday = today - timedelta(days=1)

            if trip_date == today:
                label = "HOY"
            elif trip_date == yesterday:
                label = "AYER"
            else:
                label = trip_date.strftime("%d/%m/%Y")

            recent_trip_items.append({
                "id": f"trip_{index}",
                "label": label,
                "start_time": start_row[0].isoformat(),
                "end_time": end_row[0].isoformat(),
                "duration_seconds": duration_seconds,
                "distance_km": round(distance_km, 2),
            })

        return recent_trip_items

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()