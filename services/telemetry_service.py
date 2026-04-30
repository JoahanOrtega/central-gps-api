"""
telemetry_service.py — Servicio de telemetría GPS

────────────────────────────────────────────────────────────────────────────────
Reglas de fechas
────────────────────────────────────────────────────────────────────────────────
  - t_data almacena fechas en UTC naive (sin tzinfo).
  - El frontend opera en UTC-6 (America/Mexico_City).
  - to_app_iso() convierte cualquier datetime de BD → ISO 8601 con offset -06:00.
  - now_utc() es la única fuente de "ahora" para queries.
  - day_range_utc() calcula rangos de día correctamente en UTC-6.

────────────────────────────────────────────────────────────────────────────────
Reglas del estado del motor
────────────────────────────────────────────────────────────────────────────────
  Toda la lógica de "¿está encendida?" vive en utils/engine_state.py — este
  archivo NO redefine constantes de tipo_alerta ni de status, solo las consume.

  Campo `engine_state` ("on" | "off" | "unknown") incluido en cada respuesta:
    - Permite al frontend NO recalcular el estado a partir de bits crudos.
    - Unifica criterios entre backend y frontend (una sola regla, un solo lugar).
    - Resuelve ambigüedades cuando `tipo_alerta` y `status` difieren
      (p. ej. pérdidas momentáneas de señal).

────────────────────────────────────────────────────────────────────────────────
Reglas de recorridos
────────────────────────────────────────────────────────────────────────────────
  - Un recorrido comienza en tipo_alerta=33 (encendido motor) o en el
    primer punto ON después del último apagado.
  - Un recorrido termina en tipo_alerta=34 (apagado motor) o en el
    último punto antes del siguiente encendido (con fallback a STATUS_OFF).
  - strokeColor se calcula por punto según vel_max de la unidad.
  - IDs de recorrido son ESTABLES: se derivan del timestamp del punto de
    inicio, no de la posición en la lista. Esto permite que el frontend
    abra un recorrido y aunque lleguen nuevos datos, el ID siga apuntando
    al mismo recorrido.

────────────────────────────────────────────────────────────────────────────────
Optimizaciones vs. versiones previas
────────────────────────────────────────────────────────────────────────────────
  - `vel_max` de t_unidades se cachea 5 min — cambia rarísima vez y se pide
    en prácticamente cada request de ruta.
  - Todas las consultas usan context managers (`main_cursor` / `telemetry_cursor`)
    que garantizan liberación del pool aunque haya excepción.
  - `get_latest_position_by_imei` ahora permite al caller pedir solo los
    campos que necesita con `include_sensors=False` (default). Reduce bytes
    ~75% en el caso común.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, time, timezone
from math import radians, sin, cos, sqrt, atan2
from typing import Any

from utils.db_cursor import main_cursor, telemetry_cursor
from utils.engine_state import (
    EngineState,
    STATUS_OFF,
    STATUS_ON,
    TIPO_ALERTA_APAGADO,
    TIPO_ALERTA_ENCENDIDO,
    is_engine_off_point,
    resolve_engine_state,
)
from utils.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

# ── Zonas horarias ─────────────────────────────────────────────────────────────
UTC_TZ = timezone.utc
APP_TZ = timezone(timedelta(hours=-6))  # America/Mexico_City (sin DST)

# ── Constantes de recorridos ──────────────────────────────────────────────────
MIN_MOVING_SPEED = 1.0  # km/h — umbral para considerar "en movimiento"
MIN_TRIP_DISTANCE_KM = 0.05  # km mínimo para incluir un recorrido
MIN_TRIP_POINTS = 3  # puntos mínimos para un recorrido válido
RECENT_TRIPS_DAYS = 7  # ventana de búsqueda de recorridos recientes

# ── Colores de polyline (fiel al CASE WHEN del legacy) ────────────────────────
COLOR_NORMAL = "#4caf50"  # verde   — velocidad normal
COLOR_WARNING = "#ff9800"  # naranja — cerca del límite (vel_max - 5)
COLOR_DANGER = "#ea1f25"  # rojo    — exceso de velocidad

# ── Cache de vel_max ──────────────────────────────────────────────────────────
# TTL de 5 min: la velocidad máxima de una unidad cambia cuando el catálogo
# se edita, un evento poco frecuente. El TTL asegura que una edición se
# propague en al menos 5 min sin invalidación manual. Invalidable explícitamente
# con `_vel_max_cache.invalidate(imei)` desde el handler del PATCH si se desea
# consistencia inmediata.
_VEL_MAX_TTL_SECONDS = 300
_vel_max_cache: TTLCache[float] = TTLCache(ttl_seconds=_VEL_MAX_TTL_SECONDS)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers de tiempo
# ══════════════════════════════════════════════════════════════════════════════


def now_utc() -> datetime:
    """Instante actual en UTC (aware). Única fuente de 'ahora' para queries."""
    return datetime.now(UTC_TZ)


def now_local() -> datetime:
    """Instante actual en UTC-6 (aware)."""
    return datetime.now(APP_TZ)


def to_utc(dt: datetime | None) -> datetime | None:
    """Convierte a UTC. Naive → asume UTC. Aware → convierte."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC_TZ)
    return dt.astimezone(UTC_TZ)


def to_app_iso(dt: datetime | None) -> str | None:
    """
    Datetime de BD (UTC naive) → ISO 8601 con offset -06:00.
    "2026-04-17 18:33:00" → "2026-04-17T12:33:00-06:00"

    El frontend parsea esto directamente con new Date() sin ambigüedad.
    """
    if dt is None:
        return None
    converted = to_utc(dt)
    # to_utc solo retorna None si dt es None; ya descartamos ese caso arriba.
    assert converted is not None
    return converted.astimezone(APP_TZ).isoformat(timespec="seconds")


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


# ══════════════════════════════════════════════════════════════════════════════
# Helpers numéricos y de movimiento
# ══════════════════════════════════════════════════════════════════════════════


def safe_speed(speed: Any) -> float:
    """Convierte cualquier valor de velocidad a float, 0.0 si falla."""
    try:
        return float(speed) if speed is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def classify_movement(engine_state: EngineState, speed: float) -> str:
    """
    Estado semántico del punto basado en el engine_state ya resuelto.

    apagado     → motor apagado
    stop        → motor encendido pero velocidad < MIN_MOVING_SPEED (relentí)
    movimiento  → motor encendido y velocidad ≥ MIN_MOVING_SPEED
    desconocido → engine_state == "unknown"
    """
    if engine_state == "off":
        return "apagado"
    if engine_state == "on":
        return "movimiento" if speed >= MIN_MOVING_SPEED else "stop"
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


# ══════════════════════════════════════════════════════════════════════════════
# Haversine
# ══════════════════════════════════════════════════════════════════════════════


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia entre dos coordenadas en kilómetros."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


# ══════════════════════════════════════════════════════════════════════════════
# Mapper de fila de t_data
# ══════════════════════════════════════════════════════════════════════════════


def map_route_row(row: tuple, vel_max: float = 0.0) -> dict[str, Any]:
    """
    Convierte una fila cruda de t_data al dict que espera el frontend.

    Columnas esperadas (por índice):
      0  fecha_hora_gps
      1  latitud
      2  longitud
      3  velocidad
      4  grados
      5  status
      6  tipo_alerta

    El campo derivado `engine_state` se calcula aquí UNA VEZ POR PUNTO para
    que el frontend no tenga que reinterpretar bits crudos de `status`.
    """
    speed_value: float | None = float(row[3]) if row[3] is not None else None
    speed = speed_value if speed_value is not None else 0.0
    status = (row[5] or "").strip() if row[5] is not None else None
    tipo_alerta = row[6] if len(row) > 6 else None

    engine_state = resolve_engine_state(tipo_alerta, status)

    return {
        "fecha_hora_gps": to_app_iso(row[0]),
        "latitud": float(row[1]) if row[1] is not None else None,
        "longitud": float(row[2]) if row[2] is not None else None,
        "velocidad": speed_value,
        "grados": float(row[4]) if row[4] is not None else None,
        "status": status,
        "tipo_alerta": tipo_alerta,
        "engine_state": engine_state,
        "movement_state": classify_movement(engine_state, speed),
        "strokeColor": get_stroke_color(speed, vel_max),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Validación de pertenencia
# ══════════════════════════════════════════════════════════════════════════════


def check_unit_belongs_to_company(imei: str, id_empresa: int) -> bool:
    """Verifica que el IMEI pertenezca a una unidad activa de la empresa."""
    with main_cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM t_unidades WHERE imei = %s AND id_empresa = %s "
            "AND status = 1 LIMIT 1",
            (imei, id_empresa),
        )
        return cursor.fetchone() is not None


def _query_vel_max_from_db(imei: str) -> float:
    """Lee vel_max de la BD. Uso interno: invocado solo por el cache."""
    with main_cursor() as cursor:
        cursor.execute(
            "SELECT vel_max FROM t_unidades WHERE imei = %s LIMIT 1",
            (imei,),
        )
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0


def _get_vel_max(imei: str) -> float:
    """
    Obtiene vel_max con cache TTL de 5 min.
    Para invalidación inmediata (tras editar el catálogo), llamar:
        from services.telemetry_service import invalidate_vel_max_cache
        invalidate_vel_max_cache(imei)
    """
    return _vel_max_cache.get_or_compute(
        key=imei,
        compute=lambda: _query_vel_max_from_db(imei),
    )


def invalidate_vel_max_cache(imei: str) -> None:
    """
    API pública para invalidar vel_max cacheada de una unidad.
    Llamar desde handlers que editan el catálogo de unidades.
    """
    _vel_max_cache.invalidate(imei)


# ══════════════════════════════════════════════════════════════════════════════
# Query base de puntos de ruta
# ══════════════════════════════════════════════════════════════════════════════

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


def _fetch_route_rows(
    imei: str,
    start_utc: datetime,
    end_utc: datetime,
    limit: int,
) -> list[tuple]:
    """
    Helper privado: ejecuta _ROUTE_QUERY y retorna filas crudas.
    Centraliza el acceso a la BD de telemetría para las funciones que
    luego segmentan en recorridos.
    """
    with telemetry_cursor() as cursor:
        cursor.execute(_ROUTE_QUERY, (imei, start_utc, end_utc, limit))
        return cursor.fetchall()


def get_positions_in_range(
    imei: str,
    start_utc: datetime,
    end_utc: datetime,
    limit: int = 5000,
    vel_max: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Consulta t_data en un rango UTC y devuelve puntos enriquecidos con
    strokeColor, engine_state, tipo_alerta y movement_state.

    `start_utc` / `end_utc` deben ser aware (UTC).
    """
    rows = _fetch_route_rows(imei, start_utc, end_utc, limit)
    return [map_route_row(row, vel_max) for row in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Posición más reciente — batch
# ══════════════════════════════════════════════════════════════════════════════


def get_latest_positions_by_imeis(imeis: list[str]) -> list[dict[str, Any]]:
    """
    Posición más reciente de una lista de IMEIs en una sola query.

    Versión liviana: solo los campos que el mapa en vivo necesita para pintar.
    Incluye:
      - `engine_state` derivado (no reinterpretar bits en frontend).
      - `segundos_en_estado_actual`: tiempo acumulado en el estado actual
        calculado desde el último evento tipo_alerta ∈ {33, 34}.

    Total de queries: 2 (una para posiciones, una para últimos cambios de
    estado). Ambas usan DISTINCT ON para procesar N imeis en tiempo
    constante en vez de N+1.
    """
    filtered = [i for i in imeis if i]
    if not filtered:
        return []

    # ── Query 1: posición más reciente por IMEI ──────────────────────────
    with telemetry_cursor() as cursor:
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
        rows = cursor.fetchall()

    # ── Query 2: batch de "último cambio de estado" por IMEI ─────────────
    state_change_map = _fetch_last_state_change_by_imeis(filtered)
    now = now_utc()

    # ── Ensamblado final ─────────────────────────────────────────────────
    result: list[dict[str, Any]] = []
    for row in rows:
        status = (row[6] or "").strip() if row[6] is not None else None
        tipo_alerta = row[10]
        imei = row[0]

        # Tiempo acumulado en estado actual (segundos desde el último
        # evento tipo_alerta ∈ {33, 34}). None si la unidad nunca ha
        # reportado un cambio de estado explícito.
        seconds_in_state = _compute_seconds_in_state(
            imei=imei,
            now=now,
            state_change_map=state_change_map,
        )

        result.append(
            {
                "imei": imei,
                "fecha_hora_gps": to_app_iso(row[1]),
                "latitud": float(row[2]) if row[2] is not None else None,
                "longitud": float(row[3]) if row[3] is not None else None,
                "velocidad": float(row[4]) if row[4] is not None else None,
                "grados": float(row[5]) if row[5] is not None else None,
                "status": status,
                "voltaje": float(row[7]) if row[7] is not None else None,
                "voltaje_bateria": float(row[8]) if row[8] is not None else None,
                "odometro": row[9],
                "tipo_alerta": tipo_alerta,
                "engine_state": resolve_engine_state(tipo_alerta, status),
                "segundos_en_estado_actual": seconds_in_state,
            }
        )
    return result


def _fetch_last_state_change_by_imeis(imeis: list[str]) -> dict[str, datetime]:
    """
    Devuelve un mapa {imei → fecha_hora_gps del último evento de cambio de
    estado del motor}.

    Un "cambio de estado" es un registro con tipo_alerta ∈ {33, 34}.
    La query usa DISTINCT ON para procesar todos los IMEIs en una sola
    pasada al índice (fiel al patrón N+1 evitado en esta codebase).

    Args:
        imeis: lista de IMEIs ya filtrada (sin vacíos).

    Returns:
        Diccionario imei → datetime naive UTC del último cambio.
        Los IMEIs que nunca han reportado tipo_alerta ∈ {33, 34} están
        ausentes del diccionario (el caller debe manejar el None).
    """
    if not imeis:
        return {}

    with telemetry_cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (imei)
                imei, fecha_hora_gps
            FROM public.t_data
            WHERE imei = ANY(%s::varchar[])
              AND tipo_alerta IN (%s, %s)
              AND fecha_hora_gps IS NOT NULL
            ORDER BY imei, fecha_hora_gps DESC
            """,
            (imeis, TIPO_ALERTA_ENCENDIDO, TIPO_ALERTA_APAGADO),
        )
        return {row[0]: row[1] for row in cursor.fetchall()}


def _compute_seconds_in_state(
    imei: str,
    now: datetime,
    state_change_map: dict[str, datetime],
) -> int | None:
    """
    Calcula los segundos transcurridos desde el último cambio de estado
    del motor para un IMEI.

    Args:
        imei:             IMEI de la unidad.
        now:              Instante de referencia (UTC aware).
        state_change_map: Mapa producido por _fetch_last_state_change_by_imeis.

    Returns:
        Entero no negativo de segundos, o None si la unidad nunca ha
        registrado un evento tipo_alerta ∈ {33, 34}.
    """
    last_change = state_change_map.get(imei)
    if last_change is None:
        return None

    # Normalizar a UTC aware para poder restar sin TypeError.
    last_change_utc = to_utc(last_change)
    if last_change_utc is None:
        return None

    delta = (now - last_change_utc).total_seconds()
    return max(0, int(delta))


def get_seconds_in_state_for_imei(imei: str) -> int | None:
    """
    Versión para un solo IMEI — usada por el endpoint de summary individual
    donde no tiene sentido pagar el costo de una query batch.

    Reutiliza la lógica interna de _fetch_last_state_change_by_imeis para
    garantizar que el cálculo sea idéntico al del endpoint de units-live.

    Args:
        imei: IMEI de la unidad (string no vacío).

    Returns:
        Segundos transcurridos desde el último cambio de estado del motor,
        o None si la unidad no ha reportado eventos tipo_alerta ∈ {33, 34}.
    """
    if not imei:
        return None

    state_change_map = _fetch_last_state_change_by_imeis([imei])
    return _compute_seconds_in_state(
        imei=imei,
        now=now_utc(),
        state_change_map=state_change_map,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Recorrido por modo predefinido
# ══════════════════════════════════════════════════════════════════════════════


def get_route_by_mode(
    imei: str,
    mode: str,
    id_empresa: int | None = None,
) -> list[dict[str, Any]]:
    """
    Modos: current | latest | today | yesterday | day_before_yesterday

    - current: viaje EN CURSO. Desde el último encendido hasta ahora.
               Solo tiene sentido cuando la unidad está prendida — si está
               apagada devuelve [] (el frontend ya oculta el botón Actual
               en ese caso).
    - latest:  ÚLTIMO viaje completado. Delimitado por los dos apagados
               más recientes.

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

    if mode == "current":
        return _get_current_trip(imei, vel_max)

    return []


def _get_latest_trip(imei: str, vel_max: float = 0.0) -> list[dict[str, Any]]:
    """
    Recorrido más reciente delimitado por tipo_alerta=34 (apagado motor).

    Lógica fiel al legacy PHP:
      1. Busca los 2 últimos eventos tipo_alerta=34 (apagado real del motor)
      2. El recorrido son los puntos ENTRE prev_off y latest_off (excl/incl)
      3. Si solo hay un apagado, retorna desde ese punto hasta ahora

    Usar tipo_alerta=34 (apagado) es más preciso que STATUS_OFF porque
    filtra falsos positivos de puntos con status=0 por falta de señal.
    """
    with telemetry_cursor() as cursor:
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
            cursor.execute(_ROUTE_QUERY, (imei, latest_off, now_utc(), 5000))
        else:
            prev_off = offs[1][0]
            # Entre el apagado anterior (excl) y el apagado más reciente (incl)
            cursor.execute(
                """
                SELECT fecha_hora_gps, latitud, longitud, velocidad,
                       grados, status, tipo_alerta
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

        rows = cursor.fetchall()

    return [map_route_row(row, vel_max) for row in rows]


def _get_current_trip(imei: str, vel_max: float = 0.0) -> list[dict[str, Any]]:
    """
    Recorrido EN CURSO — desde el último encendido hasta el momento actual.

    Diferencia con _get_latest_trip:
      - latest:  desde apagado anterior HASTA último apagado (cerrado)
      - current: desde último encendido HASTA ahora (abierto)

    Lógica:
      1. Buscar el evento más reciente con tipo_alerta=33 (encendido motor).
      2. Si NO existe encendido posterior al último apagado → la unidad
         está apagada → devolver lista vacía (no hay viaje en curso).
      3. Si SÍ existe → traer todos los puntos desde ese encendido
         hasta ahora.

    Casos cubiertos:
      - Unidad nueva sin telemetría: []
      - Unidad apagada hace tiempo: []
      - Unidad encendida ahora mismo: puntos desde el encendido hasta now()
      - Unidad encendida y luego apagada: [] (cae a "latest" mejor)
    """
    with telemetry_cursor() as cursor:
        # 1. Buscar el último encendido del motor.
        cursor.execute(
            """
            SELECT fecha_hora_gps FROM public.t_data
             WHERE imei = %s
               AND tipo_alerta = %s
               AND fecha_hora_gps IS NOT NULL
             ORDER BY fecha_hora_gps DESC
             LIMIT 1
            """,
            (imei, TIPO_ALERTA_ENCENDIDO),
        )
        last_on_row = cursor.fetchone()

        # Fallback: si no hay tipo_alerta=33, usar STATUS_ON.
        # Algunos dispositivos viejos no emiten tipo_alerta y solo cambian status.
        if not last_on_row:
            cursor.execute(
                """
                SELECT fecha_hora_gps FROM public.t_data
                 WHERE imei = %s
                   AND status = %s
                   AND fecha_hora_gps IS NOT NULL
                 ORDER BY fecha_hora_gps DESC
                 LIMIT 1
                """,
                (imei, STATUS_ON),
            )
            last_on_row = cursor.fetchone()

        if not last_on_row:
            # Unidad nunca se ha encendido — no hay viaje en curso.
            return []

        last_on_at = last_on_row[0]

        # 2. Verificar que NO haya un apagado POSTERIOR al último encendido.
        # Si hay apagado posterior → la unidad ya no está en viaje. Devolver
        # vacío para que el frontend muestre el mensaje "Sin viaje en curso".
        cursor.execute(
            """
            SELECT 1 FROM public.t_data
             WHERE imei = %s
               AND tipo_alerta = %s
               AND fecha_hora_gps IS NOT NULL
               AND fecha_hora_gps > %s
             LIMIT 1
            """,
            (imei, TIPO_ALERTA_APAGADO, last_on_at),
        )
        if cursor.fetchone():
            # Hay un apagado más reciente que el último encendido → ya no
            # está en viaje. El usuario debería usar "Último" en su lugar.
            return []

    # 3. Hay viaje en curso — devolver los puntos desde el encendido hasta ahora.
    # NOTA: usamos datetime.utcnow() en lugar de NOW() en SQL para que el
    # rango sea coherente con el resto de las funciones (que usan UTC).
    end_utc = datetime.utcnow()
    return get_positions_in_range(imei, last_on_at, end_utc, 5000, vel_max)


# ══════════════════════════════════════════════════════════════════════════════
# Rango personalizado
# ══════════════════════════════════════════════════════════════════════════════


def get_route_by_custom_range(
    imei: str,
    start_date: str,
    start_time: str | None,
    end_date: str,
    end_time: str | None,
    limit: int = 5000,
    id_empresa: int | None = None,
) -> list[dict[str, Any]]:
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


# ══════════════════════════════════════════════════════════════════════════════
# Recorridos recientes — helpers compartidos
# ══════════════════════════════════════════════════════════════════════════════


def _build_trip_id(start_row: tuple) -> str:
    """
    Construye un ID estable para un recorrido basado en el timestamp del
    punto de inicio. El formato es epoch en segundos prefijado con "t_".

    ¿Por qué no f"trip_{idx}"?
    El índice posicional NO es estable: si entre dos llamadas llega un
    recorrido nuevo, "trip_1" pasaría a apuntar a un recorrido distinto.
    Usar el timestamp del inicio garantiza que el ID apunte al MISMO
    recorrido mientras ese timestamp exista en la BD.
    """
    start_dt = start_row[0]
    start_utc = to_utc(start_dt)
    assert start_utc is not None
    return f"t_{int(start_utc.timestamp())}"


def _fetch_trips_for_window(
    imei: str,
    days_back: int = RECENT_TRIPS_DAYS,
    max_points: int = 50000,
) -> list[list[tuple]]:
    """
    Helper privado: consulta puntos de los últimos `days_back` días y los
    segmenta en recorridos. Evita duplicación entre get_recent_trips_by_imei
    y get_trip_by_id.
    """
    end_utc = now_utc()
    start_utc = end_utc - timedelta(days=days_back)
    rows = _fetch_route_rows(imei, start_utc, end_utc, max_points)
    return _split_trips(rows)


def get_recent_trips_by_imei(
    imei: str,
    limit: int = 10,
    id_empresa: int | None = None,
) -> list[dict[str, Any]]:
    """
    Últimos `limit` recorridos de los últimos RECENT_TRIPS_DAYS días.

    Un recorrido se delimita preferentemente por tipo_alerta=34 (apagado motor).
    Fallback a STATUS_OFF si no hay eventos tipo_alerta en el rango.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return []

    trips = _fetch_trips_for_window(imei)
    vel_max = _get_vel_max(imei)
    return _format_trip_list(trips, limit, vel_max)


def get_trip_by_id(
    imei: str,
    trip_id: str,
    id_empresa: int | None = None,
) -> list[dict[str, Any]] | None:
    """
    Devuelve los puntos del recorrido identificado por `trip_id`.

    El ID es estable (timestamp epoch del inicio con prefijo "t_"), así que
    puede llegar de una llamada previa a /recent-trips y seguir siendo válido
    mientras el recorrido exista en la ventana de RECENT_TRIPS_DAYS días.
    """
    if id_empresa is not None and not check_unit_belongs_to_company(imei, id_empresa):
        return None

    trips = _fetch_trips_for_window(imei)
    if not trips:
        return None

    # Buscar el recorrido cuyo ID coincida (sin formatear la lista completa).
    # Esto evita calcular métricas pesadas para todos los trips cuando solo
    # queremos uno específico.
    for trip_rows in trips:
        if _build_trip_id(trip_rows[0]) == trip_id:
            vel_max = _get_vel_max(imei)
            return [map_route_row(row, vel_max) for row in trip_rows]

    return None


# ══════════════════════════════════════════════════════════════════════════════
# Segmentación de recorridos
# ══════════════════════════════════════════════════════════════════════════════


def _split_trips(rows: list[tuple]) -> list[list[tuple]]:
    """
    Divide una secuencia de puntos en recorridos.

    El criterio de corte se delega a utils.engine_state.is_engine_off_point()
    para garantizar consistencia con el resto del sistema. Resumen:
      1. tipo_alerta == 34 → apagado real del motor (corte duro).
      2. tipo_alerta == 33 → nunca corta (encendido explícito gana).
      3. status OFF + velocidad < 1 → fallback para AVLs sin tipo_alerta.

    El punto de corte se INCLUYE al final del recorrido actual
    (representa el punto de apagado).
    """
    trips: list[list[tuple]] = []
    current: list[tuple] = []

    for row in rows:
        # row: (fecha_hora_gps, lat, lon, vel, grados, status, tipo_alerta)
        lat = row[1]
        lon = row[2]

        if lat is None or lon is None:
            continue

        current.append(row)

        if is_engine_off_point(
            tipo_alerta=row[6] if len(row) > 6 else None,
            status=row[5],
            speed_kmh=safe_speed(row[3]),
            min_moving_speed=MIN_MOVING_SPEED,
        ):
            if len(current) >= MIN_TRIP_POINTS:
                trips.append(current)
            current = []

    # Recorrido activo al final (unidad aún encendida)
    if len(current) >= MIN_TRIP_POINTS:
        trips.append(current)

    return trips


def _format_trip_list(
    trips: list[list[tuple]],
    limit: int,
    vel_max: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Convierte segmentos de filas brutas al formato de respuesta.
    Ordena más reciente primero. Descarta viajes sin movimiento real
    o con distancia < MIN_TRIP_DISTANCE_KM.

    Métricas calculadas por punto (fiel al legacy SQL con variables):
      moving_seconds  → segundos con motor ON y velocidad ≥ 1
      idle_seconds    → segundos con motor ON y velocidad < 1 (relentí)
      off_seconds     → segundos con motor OFF
      speeding_count  → número de puntos con exceso de velocidad
    """
    today_local = now_local().date()
    yesterday_local = today_local - timedelta(days=1)
    result: list[dict[str, Any]] = []

    for trip_rows in reversed(trips):
        if len(result) >= limit:
            break

        formatted = _compute_trip_metrics(
            trip_rows, today_local, yesterday_local, vel_max
        )
        if formatted is not None:
            result.append(formatted)

    return result


def _compute_trip_metrics(
    trip_rows: list[tuple],
    today_local,
    yesterday_local,
    vel_max: float,
) -> dict[str, Any] | None:
    """
    Calcula las métricas de un recorrido individual.
    Retorna None si el recorrido se descarta (sin movimiento o muy corto).

    Extraído de _format_trip_list para mejorar legibilidad y permitir
    reutilización futura (p. ej. endpoints de estadísticas).
    """
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

        # Tiempo entre puntos consecutivos (segundos)
        dt = max(0, int((curr[0] - prev[0]).total_seconds()))

        prev_status = (prev[5] or "").strip() if prev[5] is not None else None
        prev_tipo_alerta = prev[6] if len(prev) > 6 else None
        prev_speed = safe_speed(prev[3])
        prev_engine = resolve_engine_state(prev_tipo_alerta, prev_status)

        # Distancia acumulada
        distance_km += haversine_km(
            float(prev[1]),
            float(prev[2]),
            float(curr[1]),
            float(curr[2]),
        )

        # Clasificar tiempo del intervalo según engine_state
        if prev_engine == "on":
            if prev_speed >= MIN_MOVING_SPEED:
                has_movement = True
                moving_seconds += dt
            else:
                idle_seconds += dt
                stop_count += 1
        elif prev_engine == "off":
            off_seconds += dt
        # engine_state == "unknown" → no se suma a ninguna categoría

        # Conteo de excesos de velocidad (nuevo evento al entrar al exceso)
        if vel_max > 0:
            over = round(prev_speed) >= vel_max
            if over and not in_excess:
                speeding_count += 1
            in_excess = over

    rounded_dist = round(distance_km, 2)

    # Descartar recorridos sin movimiento real o insignificantes
    if not has_movement or rounded_dist < MIN_TRIP_DISTANCE_KM:
        return None

    # Etiqueta del día en UTC-6
    start_utc_dt = to_utc(start_row[0])
    assert start_utc_dt is not None
    start_local_date = start_utc_dt.astimezone(APP_TZ).date()
    if start_local_date == today_local:
        label = "HOY"
    elif start_local_date == yesterday_local:
        label = "AYER"
    else:
        label = start_local_date.strftime("%d/%m/%Y")

    duration_s = max(0, int((end_row[0] - start_row[0]).total_seconds()))

    return {
        "id": _build_trip_id(start_row),
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
    }


# ══════════════════════════════════════════════════════════════════════════════
# API pública
# ══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Constantes re-exportadas
    "STATUS_ON",
    "STATUS_OFF",
    "TIPO_ALERTA_ENCENDIDO",
    "TIPO_ALERTA_APAGADO",
    # Helpers de tiempo
    "now_utc",
    "now_local",
    "to_utc",
    "to_app_iso",
    "day_range_utc",
    # Helpers de estado / movimiento
    "safe_speed",
    "classify_movement",
    "get_stroke_color",
    "haversine_km",
    # Mapper
    "map_route_row",
    # Validación
    "check_unit_belongs_to_company",
    # Queries de telemetría
    "get_positions_in_range",
    "get_latest_positions_by_imeis",
    "get_route_by_mode",
    "get_route_by_custom_range",
    "get_recent_trips_by_imei",
    "get_trip_by_id",
    "get_seconds_in_state_for_imei",
    # Cache
    "invalidate_vel_max_cache",
]
