from datetime import datetime, timedelta, time, timezone
from math import radians, sin, cos, sqrt, atan2
from db.connection import (
    get_db_telemetry_connection,
    release_db_telemetry_connection,
    get_db_connection as get_main_db_connection,
    release_db_connection,
)

UTC_TIMEZONE = timezone.utc
APP_TIMEZONE = timezone(timedelta(hours=-6))

STATUS_ON = "100000000"
STATUS_OFF = "000000000"
MIN_MOVING_SPEED = 1.0


def check_unit_belongs_to_company(imei, id_empresa):
    """Verifica que el IMEI pertenezca a una unidad de la empresa dada."""
    connection = None
    cursor = None
    try:
        connection = get_main_db_connection()
        cursor = connection.cursor()
        query = "SELECT id_unidad FROM t_unidades WHERE imei = %s AND id_empresa = %s LIMIT 1"
        cursor.execute(query, (imei, id_empresa))
        return cursor.fetchone() is not None
    finally:
        if cursor:
            cursor.close()
        if connection:
            # Devolver al pool — nunca llamar .close() directamente
            release_db_connection(connection)


def get_status_code(status):
    return (status or "").strip()


def is_unit_off(status):
    return get_status_code(status) == STATUS_OFF


def is_unit_on(status):
    return get_status_code(status) == STATUS_ON


def get_safe_speed(speed):
    try:
        return float(speed) if speed is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def is_real_moving(status, speed):
    return is_unit_on(status) and get_safe_speed(speed) >= MIN_MOVING_SPEED


def is_stop(status, speed):
    return is_unit_on(status) and get_safe_speed(speed) < MIN_MOVING_SPEED


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


def to_app_iso(dt):
    if dt is None:
        return None

    # La BD viene naive, pero realmente representa UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TIMEZONE)

    dt = dt.astimezone(APP_TIMEZONE)

    return dt.isoformat(timespec="seconds")


def map_route_row(row):
    speed = float(row[3]) if row[3] is not None else None
    status_code = row[5]

    if is_unit_off(status_code):
        movement_state = "apagado"
    elif is_stop(status_code, speed):
        movement_state = "stop"
    elif is_real_moving(status_code, speed):
        movement_state = "movimiento"
    else:
        movement_state = "desconocido"

    return {
        "fecha_hora_gps": to_app_iso(row[0]),
        "latitud": float(row[1]) if row[1] is not None else None,
        "longitud": float(row[2]) if row[2] is not None else None,
        "velocidad": speed,
        "grados": float(row[4]) if row[4] is not None else None,
        "status": status_code,
        "movement_state": movement_state,
    }


def get_day_range(day_offset=0):
    target_day = datetime.now().date() - timedelta(days=day_offset)
    start_dt = datetime.combine(target_day, time.min)
    end_dt = datetime.combine(target_day, time.max)
    return start_dt, end_dt


def get_latest_route_between_last_two_power_offs(imei):
    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        last_two_offs_query = """
            SELECT fecha_hora_gps
            FROM public.t_data
            WHERE imei = %s
              AND fecha_hora_gps IS NOT NULL
              AND status = %s
            ORDER BY fecha_hora_gps DESC
            LIMIT 2;
        """

        cursor.execute(last_two_offs_query, (imei, STATUS_OFF))
        off_rows = cursor.fetchall()

        if len(off_rows) == 0:
            return []

        if len(off_rows) == 1:
            latest_off_time = off_rows[0][0]

            route_query = """
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
                  AND latitud IS NOT NULL
                  AND longitud IS NOT NULL
                ORDER BY fecha_hora_gps ASC;
            """

            cursor.execute(route_query, (imei, latest_off_time))
            rows = cursor.fetchall()
            return [map_route_row(row) for row in rows]

        latest_off_time = off_rows[0][0]
        previous_off_time = off_rows[1][0]

        route_query = """
            SELECT
                fecha_hora_gps,
                latitud,
                longitud,
                velocidad,
                grados,
                status
            FROM public.t_data
            WHERE imei = %s
              AND fecha_hora_gps > %s
              AND fecha_hora_gps <= %s
              AND latitud IS NOT NULL
              AND longitud IS NOT NULL
            ORDER BY fecha_hora_gps ASC;
        """

        cursor.execute(route_query, (imei, previous_off_time, latest_off_time))
        rows = cursor.fetchall()

        return [map_route_row(row) for row in rows]

    finally:
        if cursor:
            cursor.close()
        if connection:
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


def get_latest_position_by_imei(imei, id_empresa=None):
    # Si se proporciona id_empresa, validar pertenencia
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return None
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
            "fecha_hora_sistema": to_app_iso(row[1]),
            "fecha_hora_gmt": to_app_iso(row[2]),
            "fecha_hora_gps": to_app_iso(row[3]),
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
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


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
            items.append(
                {
                    "imei": row[0],
                    "fecha_hora_gps": to_app_iso(row[1]),
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
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


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
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


def get_route_by_mode(imei, mode, id_empresa=None):
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

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
        return get_latest_route_between_last_two_power_offs(imei)

    return []


def get_trip_by_id(imei, trip_id, id_empresa=None):
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return None

    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        start_date = datetime.now() - timedelta(days=7)
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
            ORDER BY fecha_hora_gps ASC;
        """

        cursor.execute(query, (imei, start_date, end_date))
        rows = cursor.fetchall()

        trips = build_recent_trips_from_rows(rows, limit=50)
        selected_trip = next((trip for trip in trips if trip["id"] == trip_id), None)

        if not selected_trip:
            return None

        return [map_route_row(row) for row in selected_trip["rows"]]

    finally:
        if cursor:
            cursor.close()
        if connection:
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


def get_recent_trips_by_imei(imei, limit=10, id_empresa=None):
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    connection = None
    cursor = None

    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        start_date = datetime.now() - timedelta(days=7)
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
            ORDER BY fecha_hora_gps ASC;
        """

        cursor.execute(query, (imei, start_date, end_date))
        rows = cursor.fetchall()

        trips = build_recent_trips_from_rows(rows, limit)

        return [
            {
                "id": trip["id"],
                "label": trip["label"],
                "start_time": trip["start_time"],
                "end_time": trip["end_time"],
                "duration_seconds": trip["duration_seconds"],
                "distance_km": trip["distance_km"],
                "movement_state": trip["movement_state"],
                "stop_count": trip["stop_count"],
            }
            for trip in trips
        ]

    finally:
        if cursor:
            cursor.close()
        if connection:
            # Devolver al pool — nunca llamar .close() directamente
            release_db_telemetry_connection(connection)


def get_route_by_custom_range(
    imei, start_date, start_time, end_date, end_time, limit=5000, id_empresa=None
):
    """
    Obtiene puntos de ruta para un rango personalizado.
    Las fechas/horas se reciben en hora local (America/Mexico_City) y se convierten a UTC
    antes de consultar la base de datos.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    def normalize_time(time_str):
        if not time_str:
            return "00:00:00"
        parts = time_str.strip().split(":")
        if len(parts) == 2:
            return f"{time_str}:00"
        return time_str

    start_datetime_str = f"{start_date} {normalize_time(start_time)}"
    end_datetime_str = f"{end_date} {normalize_time(end_time)}"

    try:
        start_naive = datetime.strptime(start_datetime_str, "%Y-%m-%d %H:%M:%S")
        end_naive = datetime.strptime(end_datetime_str, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise ValueError(f"Formato de fecha/hora inválido: {e}")

    # Asignar zona horaria local (America/Mexico_City)
    start_local = start_naive.replace(tzinfo=APP_TIMEZONE)
    end_local = end_naive.replace(tzinfo=APP_TIMEZONE)

    # Convertir a UTC para consultar la BD
    start_utc = start_local.astimezone(UTC_TIMEZONE)
    end_utc = end_local.astimezone(UTC_TIMEZONE)

    # Eliminar tzinfo para compatibilidad con funciones existentes
    start_utc_naive = start_utc.replace(tzinfo=None)
    end_utc_naive = end_utc.replace(tzinfo=None)

    return get_positions_history_by_imei(imei, start_utc_naive, end_utc_naive, limit)


def build_recent_trips_from_rows(rows, limit=10):
    if not rows:
        return []

    trips = []
    current_trip = []

    for row in rows:
        fecha_hora_gps = row[0]
        latitud = row[1]
        longitud = row[2]
        velocidad = row[3]
        status = row[5]

        if latitud is None or longitud is None:
            continue

        current_trip.append(row)

        if is_unit_off(status):
            if len(current_trip) >= 2:
                trips.append(current_trip)
            current_trip = []

    if len(current_trip) >= 2:
        trips.append(current_trip)

    recent_trip_items = []
    recent_trips = list(reversed(trips))[:limit]

    for index, trip_rows in enumerate(recent_trips, start=1):
        start_row = trip_rows[0]
        end_row = trip_rows[-1]

        distance_km = 0.0
        has_real_movement = False
        stop_count = 0

        for point_index in range(1, len(trip_rows)):
            prev_point = trip_rows[point_index - 1]
            next_point = trip_rows[point_index]

            prev_status = prev_point[5]
            prev_speed = prev_point[3]

            if is_real_moving(prev_status, prev_speed):
                has_real_movement = True

            if is_stop(prev_status, prev_speed):
                stop_count += 1

            distance_km += haversine_km(
                float(prev_point[1]),
                float(prev_point[2]),
                float(next_point[1]),
                float(next_point[2]),
            )

        end_status = end_row[5]
        end_speed = end_row[3]

        if is_real_moving(end_status, end_speed):
            has_real_movement = True

        if is_stop(end_status, end_speed):
            stop_count += 1

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

        movement_state = "stop" if not has_real_movement else "movimiento"

        rounded_distance_km = round(distance_km, 2)

        # Ignorar recorridos sin movimiento real o con distancia insignificante
        if not has_real_movement or rounded_distance_km <= 0.05:
            continue

        recent_trip_items.append(
            {
                "id": f"trip_{index}",
                "label": label,
                "start_time": to_app_iso(start_row[0]),
                "end_time": to_app_iso(end_row[0]),
                "duration_seconds": duration_seconds,
                "distance_km": rounded_distance_km,
                "movement_state": movement_state,
                "stop_count": stop_count,
                "rows": trip_rows,
            }
        )

    return recent_trip_items
