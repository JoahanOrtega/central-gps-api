import logging
from math import radians, sin, cos, sqrt, atan2

logger = logging.getLogger(__name__)

# Índices de columna en la tupla de t_data
# Si _ROUTE_QUERY cambia de orden, actualizar SOLO estos índices.
_IDX_FECHA = 0
_IDX_LAT = 1
_IDX_LNG = 2
_IDX_VEL = 3
_IDX_ODO = 7  # columna odometro — agregada en _ROUTE_QUERY para este filtro

# Umbrales del filtro

# Criterios con odómetro (portados de Mapamodel.php)
_UMBRAL_PM_ELIMINACION = 800.0  # divergencia absoluta normalizada
_UMBRAL_PCT_ELIMINACION = 1000.0  # divergencia porcentual extrema entre odómetros

# Criterios por velocidad estimada (independientes del odómetro)
_UMBRAL_VEL_TICK_SIMPLE = 300.0  # km/h — criterio de un solo tick (modo sin odo)

# Doble tick: umbral más bajo porque la doble confirmación reduce falsos positivos.
_UMBRAL_VEL_DOBLE_TICK = 130.0  # km/h — criterio de dos ticks consecutivos


def _haversine_metros(lat1, lon1, lat2, lon2) -> float:
    if any(v is None for v in (lat1, lon1, lat2, lon2)):
        return 0.0

    R = 6_371_000.0
    dlat = radians(float(lat2) - float(lat1))
    dlon = radians(float(lon2) - float(lon1))
    a = (
        sin(dlat / 2) ** 2
        + cos(radians(float(lat1))) * cos(radians(float(lat2))) * sin(dlon / 2) ** 2
    )
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _safe_float(value, default: float = 0.0) -> float:
    """Convierte a float de forma segura. NO usar para lat/lng — ver _haversine_metros."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _calcular_metricas(
    metros_lat_lng: float,
    metros_odometro: float,
    segundos: float,
    velocidad: float,
) -> dict:
    kms_lat_lng = metros_lat_lng / 1000.0
    kms_reportados = metros_odometro / 1000.0

    # Velocidad estimada a partir de coordenadas (km/h)
    vel_estimada = (metros_lat_lng / segundos) / 3.6 if segundos > 0 else 0.0

    tiene_odometro = metros_odometro > 0

    # pm: divergencia absoluta normalizada entre GPS y haversine
    pm = (
        abs(kms_lat_lng - kms_reportados) * 100.0 / kms_lat_lng
        if kms_lat_lng > 0 and tiene_odometro
        else 0.0
    )

    # porcentaje_diferencia: divergencia porcentual entre ambos odómetros
    pct_dif = (
        abs(100.0 - (kms_lat_lng / kms_reportados) * 100.0)
        if kms_reportados > 0
        else 0.0
    )

    return {
        "velocidad_estimada": vel_estimada,
        "pm": pm,
        "porcentaje_diferencia": pct_dif,
        "tiene_odometro": tiene_odometro,
    }


def _debe_eliminar_punto(metricas: dict, vel_estimada_siguiente: float) -> bool:
    # Criterios basados en odómetro (solo cuando el dispositivo lo reporta)
    if metricas["tiene_odometro"]:
        if metricas["pm"] > _UMBRAL_PM_ELIMINACION:
            return True
        if metricas["porcentaje_diferencia"] > _UMBRAL_PCT_ELIMINACION:
            return True

    # Criterio de velocidad — un solo tick imposible (300 km/h)
    # Aplica siempre, con o sin odómetro
    if metricas["velocidad_estimada"] > _UMBRAL_VEL_TICK_SIMPLE:
        return True

    # Criterio de velocidad — doble tick (130 km/h × 2 confirmaciones)
    if (
        metricas["velocidad_estimada"] > _UMBRAL_VEL_DOBLE_TICK
        and vel_estimada_siguiente > _UMBRAL_VEL_DOBLE_TICK
    ):
        return True

    return False


def filter_gps_spikes(rows: list[tuple]) -> list[tuple]:
    if len(rows) <= 1:
        return list(rows)

    # velocidades estimadas para el criterio de doble tick
    velocidades_estimadas: list[float] = []

    for i in range(len(rows) - 1):
        try:
            metros = _haversine_metros(
                rows[i][_IDX_LAT],
                rows[i][_IDX_LNG],
                rows[i + 1][_IDX_LAT],
                rows[i + 1][_IDX_LNG],
            )
            delta = rows[i + 1][_IDX_FECHA] - rows[i][_IDX_FECHA]
            segundos = max(0.0, delta.total_seconds())
            vel_est = (metros / segundos) / 3.6 if segundos > 0 else 0.0
            velocidades_estimadas.append(vel_est)
        except Exception:
            velocidades_estimadas.append(0.0)

    velocidades_estimadas.append(0.0)  # último punto no tiene siguiente

    # Filtrar punto por punto
    puntos_limpios: list[tuple] = [rows[0]]  # primer punto siempre válido
    puntos_eliminados = 0

    for i in range(1, len(rows)):
        actual = rows[i]
        anterior_array = rows[i - 1]  # para delta de odómetro
        anterior_limpio = puntos_limpios[-1]  # para velocidad estimada (lat/lng)
        try:
            metros_lat_lng = _haversine_metros(
                anterior_limpio[_IDX_LAT],
                anterior_limpio[_IDX_LNG],
                actual[_IDX_LAT],
                actual[_IDX_LNG],
            )
            metros_odometro = max(
                0.0,
                _safe_float(actual[_IDX_ODO]) - _safe_float(anterior_array[_IDX_ODO]),
            )
            delta = actual[_IDX_FECHA] - anterior_limpio[_IDX_FECHA]
            segundos = max(0.0, delta.total_seconds())
            velocidad = _safe_float(actual[_IDX_VEL])

            metricas = _calcular_metricas(
                metros_lat_lng,
                metros_odometro,
                segundos,
                velocidad,
            )

            if _debe_eliminar_punto(metricas, velocidades_estimadas[i]):
                puntos_eliminados += 1
                logger.debug(
                    "Punto GPS eliminado — lat=%.6f lng=%.6f "
                    "vel_est=%.1f km/h pm=%.1f pct_dif=%.1f tiene_odo=%s",
                    _safe_float(actual[_IDX_LAT]),
                    _safe_float(actual[_IDX_LNG]),
                    metricas["velocidad_estimada"],
                    metricas["pm"],
                    metricas["porcentaje_diferencia"],
                    metricas["tiene_odometro"],
                )
                continue

            puntos_limpios.append(actual)

        except Exception:
            # Conservar el punto ante error inesperado — no bloquear el recorrido
            logger.warning(
                "Error al analizar punto GPS índice %d — se conserva por precaución",
                i,
                exc_info=True,
            )
            puntos_limpios.append(actual)

    if puntos_eliminados > 0:
        logger.info(
            "filter_gps_spikes: %d/%d puntos eliminados (saltos GPS detectados)",
            puntos_eliminados,
            len(rows),
        )

    return puntos_limpios
