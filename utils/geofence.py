"""
utils/geofence.py — Lógica pura de detección de geocercas
────────────────────────────────────────────────────────────────────────────────

Responsabilidad única: determinar si un punto GPS (lat, lng) está
dentro de un perímetro definido como círculo o polígono.

Por qué está separado del worker:
  - La geometría no tiene efectos secundarios (ni BD ni Redis).
  - Se puede testear de forma unitaria sin infraestructura.
  - Tanto el worker de detección como el endpoint de previsualización
    de geocercas pueden importarlo sin crear dependencias cruzadas.

Estrategia de implementación:
  - Círculo: fórmula de Haversine en Python puro (sin dependencias extra).
    Precisa a < 0.5% de error para distancias < 500km — más que suficiente
    para geocercas de 50m-5km. La fórmula del legacy PHP
    (computeDistanceBetween) es equivalente.
  - Polígono: Shapely con la geometría del polígono pre-serializada desde
    la BD como lista de puntos [{lat, lng}, ...]. Usa el algoritmo
    ray-casting interno de Shapely (O(n) donde n = vértices del polígono).

Dependencias:
  - shapely (pip install shapely) — solo para polígonos.
  - math — stdlib, sin instalación.

Rendimiento:
  El worker llama esta función para CADA par (unidad, POI) en cada ciclo.
  Con 1000 unidades × 200 POIs = 200,000 llamadas por ciclo de 15s.
  Por eso la función es deliberadamente sin I/O y sin estado.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

# Radio de la Tierra en metros (WGS84 media aritmética).
# El legacy PHP usa 6371000m — igualamos para consistencia de resultados.
_EARTH_RADIUS_M = 6_371_000.0

# Tipo de POI — refleja los valores en t_pois.tipo_poi
POI_TIPO_CIRCULO  = 1
POI_TIPO_POLIGONO = 2


# ── API pública ───────────────────────────────────────────────────────────────

def punto_en_geocerca(
    lat_punto:    float,
    lng_punto:    float,
    tipo_poi:     int,
    lat_centro:   float | None,
    lng_centro:   float | None,
    radio_m:      int   | None,
    polygon_path: str   | None,
) -> bool:
    """
    Determina si el punto (lat_punto, lng_punto) está dentro del perímetro
    del POI definido por sus parámetros de geometría.

    Args:
        lat_punto:    Latitud del punto GPS de la unidad.
        lng_punto:    Longitud del punto GPS de la unidad.
        tipo_poi:     1 = círculo, 2 = polígono (valores de t_pois.tipo_poi).
        lat_centro:   Latitud del centro del POI (solo para círculo).
        lng_centro:   Longitud del centro del POI (solo para círculo).
        radio_m:      Radio en metros (solo para círculo).
        polygon_path: JSON string con la lista de vértices del polígono,
                      formato: '[{"lat": 21.8, "lng": -102.3}, ...]'.
                      Solo para polígono.

    Returns:
        True si el punto está dentro del perímetro, False en caso contrario.
        Retorna False también si los parámetros de geometría son inválidos
        (None donde se esperaba valor), loggeando un warning.

    Nota sobre manejo de errores:
        No lanza excepciones — el worker no debe interrumpirse por un POI
        con datos corruptos. En su lugar retorna False (fuera = seguro)
        y loggea el problema para investigación.
    """
    try:
        if tipo_poi == POI_TIPO_CIRCULO:
            return _en_circulo(lat_punto, lng_punto, lat_centro, lng_centro, radio_m)
        elif tipo_poi == POI_TIPO_POLIGONO:
            return _en_poligono(lat_punto, lng_punto, polygon_path)
        else:
            logger.warning(
                "tipo_poi desconocido: %s — se asume fuera del POI",
                tipo_poi,
            )
            return False

    except Exception as exc:
        # Captura cualquier fallo inesperado (datos nulos, JSON malformado,
        # Shapely indisponible). No propagar al worker — retornar False seguro.
        logger.error(
            "Error en punto_en_geocerca tipo=%s lat=%.6f lng=%.6f: %s",
            tipo_poi,
            lat_punto,
            lng_punto,
            repr(exc),
        )
        return False


def distancia_metros(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
) -> float:
    """
    Calcula la distancia en metros entre dos coordenadas GPS usando Haversine.

    Equivalente a la función `computeDistanceBetween` del legacy PHP.
    Precisión: < 0.5% de error para distancias < 500km.

    Útil para el worker cuando necesita prefiltar POIs lejanos antes de
    ejecutar la detección completa (optimización de bbox).

    Args:
        lat1, lng1: Coordenadas del primer punto.
        lat2, lng2: Coordenadas del segundo punto.

    Returns:
        Distancia en metros como float.
    """
    # Convertir grados a radianes
    phi1   = math.radians(lat1)
    phi2   = math.radians(lat2)
    dphi   = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    # Fórmula de Haversine
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_M * c


# ── Implementaciones privadas ─────────────────────────────────────────────────

def _en_circulo(
    lat_punto: float,
    lng_punto: float,
    lat_centro: float | None,
    lng_centro: float | None,
    radio_m: int | None,
) -> bool:
    """
    Detecta si el punto está dentro de un POI circular.

    Lógica:
        distancia(punto, centro) <= radio

    Args:
        lat_punto, lng_punto: Coordenadas del punto GPS.
        lat_centro, lng_centro: Centro del círculo (t_pois.lat, t_pois.lng).
        radio_m: Radio del círculo en metros (t_pois.radio).

    Returns:
        True si la distancia Haversine al centro es <= radio_m.
    """
    if lat_centro is None or lng_centro is None or radio_m is None:
        logger.warning(
            "POI circular con geometría incompleta: "
            "lat_centro=%s lng_centro=%s radio=%s",
            lat_centro, lng_centro, radio_m,
        )
        return False

    distancia = distancia_metros(lat_punto, lng_punto, lat_centro, lng_centro)
    return distancia <= radio_m


def _en_poligono(
    lat_punto:    float,
    lng_punto:    float,
    polygon_path: str | None,
) -> bool:
    """
    Detecta si el punto está dentro de un POI poligonal usando Shapely.

    El polygon_path viene de t_pois.polygon_path como JSON string.
    Formato esperado: '[{"lat": 21.88, "lng": -102.29}, ...]'

    Si Shapely no está instalado, cae al algoritmo de ray-casting Python puro
    (más lento pero sin dependencias). Logea una advertencia en ese caso.

    Args:
        lat_punto, lng_punto: Coordenadas del punto a evaluar.
        polygon_path: JSON string con los vértices del polígono.

    Returns:
        True si el punto está dentro del polígono (o en su borde).
    """
    if not polygon_path:
        logger.warning("POI poligonal sin polygon_path — se asume fuera")
        return False

    # Parsear vértices
    try:
        vertices_raw: list[dict[str, Any]] = json.loads(polygon_path)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error(
            "polygon_path JSON inválido: %s — %s",
            polygon_path[:80],
            repr(exc),
        )
        return False

    if len(vertices_raw) < 3:
        logger.warning(
            "Polígono con menos de 3 vértices (%d) — se asume fuera",
            len(vertices_raw),
        )
        return False

    # Extraer coordenadas en formato (lng, lat) — Shapely usa (x=lng, y=lat)
    # IMPORTANTE: Shapely trabaja en (x, y) = (longitud, latitud).
    # Invertir el orden es el error más común en geometría geoespacial.
    try:
        coords = [(v["lng"], v["lat"]) for v in vertices_raw]
    except (KeyError, TypeError) as exc:
        logger.error(
            "Vértices con formato inesperado: %s — %s",
            vertices_raw[:2],
            repr(exc),
        )
        return False

    # Intentar con Shapely (más robusto: maneja casos edge como puntos en borde)
    try:
        from shapely.geometry import Point, Polygon as ShapelyPolygon
        polygon = ShapelyPolygon(coords)
        point   = Point(lng_punto, lat_punto)  # (x=lng, y=lat)
        return polygon.contains(point) or polygon.touches(point)

    except ImportError:
        # Shapely no está instalado — usar ray-casting Python puro como fallback
        logger.warning(
            "Shapely no disponible — usando ray-casting Python para el polígono. "
            "Instalar: pip install shapely"
        )
        return _ray_casting(lat_punto, lng_punto, coords)


def _ray_casting(
    lat_punto: float,
    lng_punto: float,
    coords: list[tuple[float, float]],
) -> bool:
    """
    Algoritmo de ray-casting para verificar si un punto está dentro
    de un polígono. Fallback cuando Shapely no está disponible.

    El algoritmo lanza un rayo horizontal hacia la derecha desde el punto
    y cuenta cuántas aristas del polígono cruza. Si el conteo es impar,
    el punto está dentro; si es par, está fuera.

    Complejidad: O(n) donde n es el número de vértices.

    Args:
        lat_punto, lng_punto: Coordenadas del punto a evaluar.
        coords: Lista de (lng, lat) de los vértices del polígono.
                (Mismo formato que Shapely: x=lng, y=lat)

    Returns:
        True si el punto está dentro del polígono.
    """
    # Reordenar a (lat, lng) para este algoritmo que trabaja en (y=lat, x=lng)
    x = lng_punto
    y = lat_punto

    n = len(coords)
    dentro = False

    j = n - 1
    for i in range(n):
        xi, yi = coords[i]   # (lng, lat) = (x, y)
        xj, yj = coords[j]

        # Verificar si la arista (j, i) cruza el rayo horizontal en y=y_punto
        intersecta = (
            ((yi > y) != (yj > y))
            and (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
        )
        if intersecta:
            dentro = not dentro
        j = i

    return dentro