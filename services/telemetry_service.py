"""
telemetry_service.py — Servicio de telemetría GPS

Reglas de fechas:
  - t_data almacena fechas en UTC naive (sin tzinfo).
  - El frontend opera en UTC-6 (America/Mexico_City).
  - to_app_iso() convierte cualquier datetime de BD → ISO 8601 con offset -06:00.
  - now_utc() es la única fuente de "ahora" para queries.
  - day_range_utc() calcula rangos de día correctamente en UTC-6.

Reglas de recorridos:
  - Un recorrido comienza en tipo_alerta=33 (encendido motor) o en el
    primer punto ON después del último apagado.
  - Un recorrido termina en tipo_alerta=34 (apagado motor) o en el
    último punto antes del siguiente encendido.
  - Los puntos con ignorar_registro=True se excluyen del polyline
    pero se incluyen para calcular eventos.
  - strokeColor se calcula por punto según vel_max de la unidad.
"""

import logging
from datetime import datetime, timedelta, time, timezone, date
from math import radians, sin, cos, sqrt, atan2
from db.connection import (
    get_db_telemetry_connection,
    release_db_telemetry_connection,
    get_db_connection as get_main_db_connection,
    release_db_connection,
)

logger = logging.getLogger(__name__)

# ── Zonas horarias ─────────────────────────────────────────────────────────────
UTC_TZ = timezone.utc
APP_TZ = timezone(timedelta(hours=-6))  # America/Mexico_City (sin DST)

# ── Constantes ────────────────────────────────────────────────────────────────
STATUS_ON = "100000000"
STATUS_OFF = "000000000"
MIN_MOVING_SPEED = 1.0  # km/h
MIN_TRIP_DISTANCE_KM = 0.05  # km mínimo para incluir un recorrido
MIN_TRIP_POINTS = 3  # puntos mínimos para un recorrido válido
RECENT_TRIPS_DAYS = 7  # ventana de búsqueda de recorridos recientes

# Tipo de alerta — fiel al legacy PHP
TIPO_ALERTA_ENCENDIDO = 33
TIPO_ALERTA_APAGADO = 34

# Colores de polyline — fiel al legacy (CASE WHEN velocidad < vel_max-5 …)
COLOR_NORMAL = "#4caf50"  # verde — velocidad normal
COLOR_WARNING = "#ff9800"  # naranja — cerca del límite (vel_max - 5)
COLOR_DANGER = "#ea1f25"  # rojo — exceso de velocidad


# ── Helpers de tiempo ──────────────────────────────────────────────────────────


def now_utc() -> datetime:
    """Instante actual en UTC (aware). Única fuente de 'ahora' para queries."""
    return datetime.now(UTC_TZ)


def now_local() -> datetime:
    """Instante actual en UTC-6 (aware)."""
    return datetime.now(APP_TZ)


def to_utc(dt: datetime) -> datetime:
    """Convierte a UTC. Naive → asume UTC. Aware → convierte."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(UTC_TZ)


def to_app_iso(dt) -> str | None:
    """
    Datetime de BD (UTC naive) → ISO 8601 con offset -06:00.
    "2026-04-17 18:33:00" → "2026-04-17T12:33:00-06:00"
    El frontend parsea esto directamente con new Date() sin ambigüedad.
    """
    if dt is None:
        return None
    return to_utc(dt).astimezone(APP_TZ).isoformat(timespec="seconds")


def day_range_utc(day_offset: int = 0) -> tuple[datetime, datetime]:
    """
    Rango (inicio, fin) de un día en UTC dado el offset en días desde hoy UTC-6.
    day_offset=0 → hoy, 1 → ayer, 2 → antier.

    Ejemplo (hoy=2026-04-17 en UTC-6):
      inicio local = 2026-04-17 00:00:00-06:00 → 2026-04-17 06:00:00 UTC
      fin local    = 2026-04-17 23:59:59-06:00 → 2026-04-18 05:59:59 UTC
    """
    target = now_local().date() - timedelta(days=day_offset)
    start = datetime.combine(target, time.min, tzinfo=APP_TZ).astimezone(UTC_TZ)
    end = datetime.combine(target, time.max, tzinfo=APP_TZ).astimezone(UTC_TZ)
    return start, end


# ── Helpers de estado ──────────────────────────────────────────────────────────


def safe_speed(speed) -> float:
    try:
        return float(speed) if speed is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def is_off(status: str | None) -> bool:
    return (status or "").strip() == STATUS_OFF


def is_on(status: str | None) -> bool:
    return (status or "").strip() == STATUS_ON


def is_moving(status: str | None, speed) -> bool:
    return is_on(status) and safe_speed(speed) >= MIN_MOVING_SPEED


def is_idle(status: str | None, speed) -> bool:
    return is_on(status) and safe_speed(speed) < MIN_MOVING_SPEED


def classify_movement(status: str | None, speed) -> str:
    """
    Estado semántico del punto — fiel al legacy PHP.
    apagado   → status empieza en 0
    stop      → encendido + velocidad < 1 (relentí)
    movimiento → encendido + velocidad ≥ 1
    """
    if is_off(status):
        return "apagado"
    if is_idle(status, speed):
        return "stop"
    if is_moving(status, speed):
        return "movimiento"
    return "desconocido"


def get_stroke_color(speed: float, vel_max: float) -> str:
    """
    Color del polyline por punto — fiel al CASE WHEN del legacy:
      velocidad <  vel_max-5 → verde
      velocidad >= vel_max-5 → naranja
      velocidad >= vel_max   → rojo
    Si vel_max=0 siempre verde.
    """
    if vel_max <= 0:
        return COLOR_NORMAL
    spd = round(speed)
    if spd >= vel_max:
        return COLOR_DANGER
    if spd >= vel_max - 5:
        return COLOR_WARNING
    return COLOR_NORMAL


# ── Haversine ──────────────────────────────────────────────────────────────────


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ── Mapper de fila ─────────────────────────────────────────────────────────────


def map_route_row(row, vel_max: float = 0.0) -> dict:
    """
    Convierte una fila de t_data al dict que espera el frontend.

    Columnas esperadas (índices):
      0  fecha_hora_gps
      1  latitud
      2  longitud
      3  velocidad
      4  grados
      5  status
      6  tipo_alerta   (nuevo — permite al frontend identificar inicio/fin exacto)
    """
    speed = float(row[3]) if row[3] is not None else None
    status = (row[5] or "").strip()
    tipo_alerta = row[6] if len(row) > 6 else None

    return {
        "fecha_hora_gps": to_app_iso(row[0]),
        "latitud": float(row[1]) if row[1] is not None else None,
        "longitud": float(row[2]) if row[2] is not None else None,
        "velocidad": speed,
        "grados": float(row[4]) if row[4] is not None else None,
        "status": status,
        "tipo_alerta": tipo_alerta,
        "movement_state": classify_movement(status, speed),
        "strokeColor": get_stroke_color(speed or 0.0, vel_max),
    }


# ── Validación de pertenencia ──────────────────────────────────────────────────


def check_unit_belongs_to_company(imei: str, id_empresa: int) -> bool:
    """Verifica que el IMEI pertenezca a una unidad activa de la empresa."""
    connection = cursor = None
    try:
        connection = get_main_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT 1 FROM t_unidades WHERE imei = %s AND id_empresa = %s AND status = 1 LIMIT 1",
            (imei, id_empresa),
        )
        return cursor.fetchone() is not None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def _get_vel_max(imei: str) -> float:
    """Obtiene vel_max de la unidad desde la BD principal."""
    connection = cursor = None
    try:
        connection = get_main_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT vel_max FROM t_unidades WHERE imei = %s LIMIT 1", (imei,)
        )
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ── Query base de puntos de ruta ───────────────────────────────────────────────

_ROUTE_QUERY = """
    SELECT
        fecha_hora_gps,
        latitud,
        longitud,
        velocidad,
        grados,
        status,
        tipo_alerta
    FROM public.t_data
    WHERE imei = %s
      AND fecha_hora_gps >= %s
      AND fecha_hora_gps <= %s
      AND latitud  IS NOT NULL
      AND longitud IS NOT NULL
    ORDER BY fecha_hora_gps ASC
    LIMIT %s
"""


def get_positions_in_range(
    imei: str,
    start_utc: datetime,
    end_utc: datetime,
    limit: int = 5000,
    vel_max: float = 0.0,
) -> list[dict]:
    """
    Consulta t_data en un rango UTC y devuelve puntos enriquecidos con
    strokeColor, tipo_alerta y movement_state.
    start_utc / end_utc deben ser aware (UTC).
    """
    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()
        cursor.execute(_ROUTE_QUERY, (imei, start_utc, end_utc, limit))
        return [map_route_row(row, vel_max) for row in cursor.fetchall()]
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_telemetry_connection(connection)


# ── Posición más reciente ──────────────────────────────────────────────────────


def get_latest_position_by_imei(
    imei: str, id_empresa: int | None = None
) -> dict | None:
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return None
    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT
                id_data, fecha_hora_sistema, fecha_hora_gmt, fecha_hora_gps,
                imei, tipo_alerta, latitud, longitud, velocidad, grados,
                status, voltaje, adc, voltaje_bateria, odometro,
                rfid, data, tipo_dato, tipo_reporte, numero_reporte, id_ear, atributos
            FROM public.t_data
            WHERE imei = %s
            ORDER BY fecha_hora_gps DESC
            LIMIT 1
            """,
            (imei,),
        )
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
            release_db_telemetry_connection(connection)


def get_latest_positions_by_imeis(imeis: list[str]) -> list[dict]:
    """Posición más reciente de una lista de IMEIs en una sola query."""
    filtered = [i for i in imeis if i]
    if not filtered:
        return []
    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT DISTINCT ON (imei)
                imei, fecha_hora_gps, latitud, longitud, velocidad,
                grados, status, voltaje, voltaje_bateria, odometro, tipo_alerta
            FROM public.t_data
            WHERE imei = ANY(%s::varchar[])
            ORDER BY imei, fecha_hora_gps DESC
            """,
            (filtered,),
        )
        return [
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
            for row in cursor.fetchall()
        ]
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_telemetry_connection(connection)


# ── Recorrido por modo predefinido ─────────────────────────────────────────────


def get_route_by_mode(
    imei: str,
    mode: str,
    id_empresa: int | None = None,
) -> list[dict]:
    """
    Modos: today | yesterday | day_before_yesterday | latest
    Todos los rangos se calculan en UTC-6 y se consultan en UTC.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    vel_max = _get_vel_max(imei)

    if mode in ("today", "yesterday", "day_before_yesterday"):
        offset = {"today": 0, "yesterday": 1, "day_before_yesterday": 2}[mode]
        start_utc, end_utc = day_range_utc(offset)
        return get_positions_in_range(imei, start_utc, end_utc, 5000, vel_max)

    if mode == "latest":
        return _get_latest_trip(imei, vel_max)

    return []


def _get_latest_trip(imei: str, vel_max: float = 0.0) -> list[dict]:
    """
    Recorrido más reciente delimitado por tipo_alerta=34 (apagado motor).

    Lógica fiel al legacy PHP:
      1. Busca los 2 últimos eventos tipo_alerta=34 (apagado real del motor)
      2. El recorrido son los puntos ENTRE prev_off y latest_off (excl/incl)
      3. Si solo hay un apagado, retorna desde ese punto hasta ahora

    Usar tipo_alerta=34 (apagado) es más preciso que STATUS_OFF porque
    filtra falsos positivos de puntos con status=0 por falta de señal.
    """
    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()

        # Buscar los 2 últimos apagados reales de motor
        cursor.execute(
            """
            SELECT fecha_hora_gps FROM public.t_data
            WHERE imei = %s
              AND tipo_alerta = %s
              AND fecha_hora_gps IS NOT NULL
            ORDER BY fecha_hora_gps DESC
            LIMIT 2
            """,
            (imei, TIPO_ALERTA_APAGADO),
        )
        offs = cursor.fetchall()

        # Fallback: si no hay tipo_alerta=34, usar STATUS_OFF
        if not offs:
            cursor.execute(
                """
                SELECT fecha_hora_gps FROM public.t_data
                WHERE imei = %s AND status = %s AND fecha_hora_gps IS NOT NULL
                ORDER BY fecha_hora_gps DESC
                LIMIT 2
                """,
                (imei, STATUS_OFF),
            )
            offs = cursor.fetchall()

        if not offs:
            return []

        latest_off = offs[0][0]

        if len(offs) == 1:
            # Un solo apagado → desde ese punto hasta ahora
            cursor.execute(
                _ROUTE_QUERY,
                (imei, latest_off, now_utc(), 5000),
            )
        else:
            prev_off = offs[1][0]
            # Entre el apagado anterior (excl) y el apagado más reciente (incl)
            cursor.execute(
                """
                SELECT fecha_hora_gps, latitud, longitud, velocidad, grados, status, tipo_alerta
                FROM public.t_data
                WHERE imei = %s
                  AND fecha_hora_gps > %s
                  AND fecha_hora_gps <= %s
                  AND latitud  IS NOT NULL
                  AND longitud IS NOT NULL
                ORDER BY fecha_hora_gps ASC
                """,
                (imei, prev_off, latest_off),
            )

        return [map_route_row(row, vel_max) for row in cursor.fetchall()]

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_telemetry_connection(connection)


# ── Rango personalizado ────────────────────────────────────────────────────────


def get_route_by_custom_range(
    imei: str,
    start_date: str,
    start_time: str | None,
    end_date: str,
    end_time: str | None,
    limit: int = 5000,
    id_empresa: int | None = None,
) -> list[dict]:
    """
    Recibe fecha/hora en UTC-6 (como las envía el frontend).
    Convierte a UTC antes de consultar.
    end_time default = 23:59:59 (cubre todo el día final).
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    def _norm(t: str | None, default: str) -> str:
        if not t:
            return default
        parts = t.strip().split(":")
        h, m = parts[0], parts[1] if len(parts) > 1 else "00"
        s = parts[2] if len(parts) > 2 else "00"
        return f"{h}:{m}:{s}"

    try:
        start_naive = datetime.strptime(
            f"{start_date} {_norm(start_time, '00:00:00')}", "%Y-%m-%d %H:%M:%S"
        )
        end_naive = datetime.strptime(
            f"{end_date} {_norm(end_time, '23:59:59')}", "%Y-%m-%d %H:%M:%S"
        )
    except ValueError as exc:
        raise ValueError(f"Formato de fecha/hora inválido: {exc}") from exc

    start_utc = start_naive.replace(tzinfo=APP_TZ).astimezone(UTC_TZ)
    end_utc = end_naive.replace(tzinfo=APP_TZ).astimezone(UTC_TZ)

    vel_max = _get_vel_max(imei)
    return get_positions_in_range(imei, start_utc, end_utc, limit, vel_max)


# ── Recorridos recientes ───────────────────────────────────────────────────────


def get_recent_trips_by_imei(
    imei: str,
    limit: int = 10,
    id_empresa: int | None = None,
) -> list[dict]:
    """
    Últimos `limit` recorridos de los últimos RECENT_TRIPS_DAYS días.

    Un recorrido se delimita preferentemente por tipo_alerta=34 (apagado motor).
    Fallback a STATUS_OFF si no hay eventos tipo_alerta en el rango.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    end_utc = now_utc()
    start_utc = end_utc - timedelta(days=RECENT_TRIPS_DAYS)
    vel_max = _get_vel_max(imei)

    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()
        cursor.execute(_ROUTE_QUERY, (imei, start_utc, end_utc, 50000))
        rows = cursor.fetchall()
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_telemetry_connection(connection)

    trips = _split_trips(rows)
    return _format_trip_list(trips, limit, vel_max)


def get_trip_by_id(
    imei: str,
    trip_id: str,
    id_empresa: int | None = None,
) -> list[dict] | None:
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return None

    end_utc = now_utc()
    start_utc = end_utc - timedelta(days=RECENT_TRIPS_DAYS)
    vel_max = _get_vel_max(imei)

    connection = cursor = None
    try:
        connection = get_db_telemetry_connection()
        cursor = connection.cursor()
        cursor.execute(_ROUTE_QUERY, (imei, start_utc, end_utc, 50000))
        rows = cursor.fetchall()
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_telemetry_connection(connection)

    trips = _split_trips(rows)
    trip_list = _format_trip_list(trips, limit=50, vel_max=vel_max)
    selected = next((t for t in trip_list if t["id"] == trip_id), None)
    if not selected:
        return None

    return [map_route_row(row, vel_max) for row in selected["rows"]]


# ── Segmentación de recorridos ─────────────────────────────────────────────────


def _split_trips(rows: list) -> list[list]:
    """
    Divide una secuencia de puntos en recorridos.

    Criterio de corte (en orden de preferencia):
      1. tipo_alerta = TIPO_ALERTA_APAGADO (34) — apagado real del motor
      2. STATUS_OFF en punto con velocidad < 1 — fallback para AVLs
         que no envían tipo_alerta

    El punto de corte se INCLUYE al final del recorrido actual
    (representa el punto de apagado).
    """
    trips: list[list] = []
    current: list = []

    for row in rows:
        # row: (fecha_hora_gps, lat, lon, vel, grados, status, tipo_alerta)
        lat = row[1]
        lon = row[2]
        tipo = row[6] if len(row) > 6 else None

        if lat is None or lon is None:
            continue

        current.append(row)

        # Corte en apagado real del motor
        is_engine_off = tipo == TIPO_ALERTA_APAGADO or (
            is_off(row[5]) and safe_speed(row[3]) < MIN_MOVING_SPEED
        )

        if is_engine_off:
            if len(current) >= MIN_TRIP_POINTS:
                trips.append(current)
            current = []

    # Recorrido activo al final (unidad aún encendida)
    if len(current) >= MIN_TRIP_POINTS:
        trips.append(current)

    return trips


def _format_trip_list(
    trips: list[list],
    limit: int,
    vel_max: float = 0.0,
) -> list[dict]:
    """
    Convierte segmentos de filas brutas al formato de respuesta.
    Ordena más reciente primero. Descarta viajes sin movimiento real
    o con distancia < MIN_TRIP_DISTANCE_KM.

    Métricas calculadas por punto (fiel al legacy SQL con variables):
      moving_seconds  → segundos con status=ON y velocidad ≥ 1
      idle_seconds    → segundos con status=ON y velocidad < 1 (relentí)
      off_seconds     → segundos con status=OFF
      speeding_count  → número de puntos con exceso de velocidad
    """
    today_local = now_local().date()
    yesterday_local = today_local - timedelta(days=1)
    result = []

    for idx, trip_rows in enumerate(reversed(trips), start=1):
        if len(result) >= limit:
            break

        start_row = trip_rows[0]
        end_row = trip_rows[-1]

        distance_km = 0.0
        has_movement = False
        stop_count = 0
        moving_seconds = 0
        idle_seconds = 0
        off_seconds = 0
        speeding_count = 0
        in_excess = False  # evitar contar el mismo exceso varias veces

        for i in range(1, len(trip_rows)):
            prev = trip_rows[i - 1]
            curr = trip_rows[i]

            # Tiempo entre puntos consecutivos (en segundos)
            dt = max(0, int((curr[0] - prev[0]).total_seconds()))

            prev_status = (prev[5] or "").strip()
            prev_speed = safe_speed(prev[3])

            # Distancia acumulada
            distance_km += haversine_km(
                float(prev[1]),
                float(prev[2]),
                float(curr[1]),
                float(curr[2]),
            )

            # Clasificar tiempo del intervalo
            if is_moving(prev_status, prev_speed):
                has_movement = True
                moving_seconds += dt
            elif is_idle(prev_status, prev_speed):
                idle_seconds += dt
                stop_count += 1
            elif is_off(prev_status):
                off_seconds += dt

            # Conteo de excesos de velocidad (nuevo evento al entrar al exceso)
            if vel_max > 0:
                over = round(prev_speed) >= vel_max
                if over and not in_excess:
                    speeding_count += 1
                in_excess = over

        rounded_dist = round(distance_km, 2)

        # Descartar recorridos sin movimiento real o insignificantes
        if not has_movement or rounded_dist < MIN_TRIP_DISTANCE_KM:
            continue

        # Etiqueta del día en UTC-6
        start_local_date = to_utc(start_row[0]).astimezone(APP_TZ).date()
        if start_local_date == today_local:
            label = "HOY"
        elif start_local_date == yesterday_local:
            label = "AYER"
        else:
            label = start_local_date.strftime("%d/%m/%Y")

        duration_s = max(0, int((end_row[0] - start_row[0]).total_seconds()))

        result.append(
            {
                "id": f"trip_{idx}",
                "label": label,
                "start_time": to_app_iso(start_row[0]),
                "end_time": to_app_iso(end_row[0]),
                "duration_seconds": duration_s,
                "distance_km": rounded_dist,
                "moving_seconds": moving_seconds,
                "idle_seconds": idle_seconds,
                "off_seconds": off_seconds,
                "stop_count": stop_count,
                "speeding_count": speeding_count,
                "movement_state": "movimiento",
                "rows": trip_rows,  # solo para get_trip_by_id
            }
        )

    return result


# ── Compatibilidad con monitor_service ────────────────────────────────────────


def get_positions_history_by_imei(
    imei: str,
    start_date,
    end_date,
    limit: int = 500,
    id_empresa: int | None = None,
) -> list[dict]:
    """
    Wrapper de compatibilidad. Acepta datetime aware/naive o str 'YYYY-MM-DD'.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    def _to_aware(dt) -> datetime:
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d")
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC_TZ)
        return dt.astimezone(UTC_TZ)

    vel_max = _get_vel_max(imei)
    return get_positions_in_range(
        imei, _to_aware(start_date), _to_aware(end_date), limit, vel_max
    )
