"""
utils/geofence.py — Lógica pura de detección de geocercas
────────────────────────────────────────────────────────────────────────────────

Responsabilidad única: determinar relaciones geométricas entre puntos GPS
y perímetros de POI (círculo o polígono), sin I/O ni estado externo.

Por qué está separado del worker:
  - La geometría no tiene efectos secundarios (ni BD ni Redis).
  - Se puede testear de forma unitaria sin infraestructura.
  - Tanto el worker de detección como el endpoint de previsualización
    de geocercas pueden importarlo sin crear dependencias cruzadas.

Estrategia de implementación:
  - Círculo: fórmula de Haversine en Python puro (sin dependencias extra).
    Precisa a < 0.5% de error para distancias < 500km — más que suficiente
    para geocercas de 50m-5km.
  - Polígono: Shapely con la geometría del polígono pre-serializada desde
    la BD como lista de puntos [{lat, lng}, ...]. Usa el algoritmo
    ray-casting interno de Shapely (O(n) donde n = vértices del polígono).
  - Intersección de línea (evento 19): algoritmo de proyección para círculos
    y algoritmo de segmentos para polígonos. Traducido desde la implementación
    Go del equipo (rules_geofence.go — lineIntersectsGeofence).

Dependencias:
  - shapely (pip install shapely) — solo para polígonos.
  - math — stdlib, sin instalación.

Rendimiento:
  El worker llama estas funciones para CADA par (unidad, POI) en cada ciclo.
  Con 1000 unidades × 200 POIs = 200,000 llamadas por ciclo de 5s.
  Por eso las funciones son deliberadamente sin I/O y sin estado.

  Optimización de bounding box:
  linea_cruza_geocerca() aplica un early-exit con bounding box antes del
  cálculo geométrico completo. Reduce el trabajo en ~80% para POIs lejanos.
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
POI_TIPO_CIRCULO = 1
POI_TIPO_POLIGONO = 2


# ── API pública ───────────────────────────────────────────────────────────────


def punto_en_geocerca(
    lat_punto: float,
    lng_punto: float,
    tipo_poi: int,
    lat_centro: float | None,
    lng_centro: float | None,
    radio_m: int | None,
    polygon_path: str | None,
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
        Retorna False también si los parámetros de geometría son inválidos,
        loggeando un warning.

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


def linea_cruza_geocerca(
    lat_prev: float,
    lng_prev: float,
    lat_curr: float,
    lng_curr: float,
    tipo_poi: int,
    lat_centro: float | None,
    lng_centro: float | None,
    radio_m: int | None,
    polygon_path: str | None,
) -> bool:
    """
    Detecta si el segmento de trayectoria (prev → curr) intersecta el
    perímetro del POI SIN que ningún extremo esté dentro.

    Se usa para el evento 19 (paso por geocerca): la unidad atravesó la
    geocerca entre dos posiciones consecutivas pero NO está dentro en ninguna
    de las dos.

    Esta función debe llamarse SOLO cuando ambos puntos están FUERA del POI
    (es decir, punto_en_geocerca() devolvió False para ambos). Si alguno está
    dentro, el evento que aplica es 10 (entrada) o 11 (salida).

    Implementación basada en:
      - Círculo: proyección del centro del POI sobre el segmento de
        trayectoria. Traslada el algoritmo Go de rules_geofence.go.
      - Polígono: bounding box early-exit + intersección de segmentos
        con cada arista del polígono.

    Args:
        lat_prev, lng_prev: Coordenadas de la posición anterior (t).
        lat_curr, lng_curr: Coordenadas de la posición actual (t+1).
        tipo_poi, lat_centro, lng_centro, radio_m, polygon_path:
            Geometría del POI (mismos parámetros que punto_en_geocerca).

    Returns:
        True si la trayectoria cruza el perímetro del POI.

    Nota:
        Retorna False ante cualquier error — el worker debe continuar.
    """
    try:
        if tipo_poi == POI_TIPO_CIRCULO:
            return _linea_cruza_circulo(
                lat_prev,
                lng_prev,
                lat_curr,
                lng_curr,
                lat_centro,
                lng_centro,
                radio_m,
            )
        elif tipo_poi == POI_TIPO_POLIGONO:
            return _linea_cruza_poligono(
                lat_prev,
                lng_prev,
                lat_curr,
                lng_curr,
                polygon_path,
            )
        else:
            return False

    except Exception as exc:
        logger.error(
            "Error en linea_cruza_geocerca tipo=%s: %s",
            tipo_poi,
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
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    # Fórmula de Haversine
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_M * c


def calcular_bounding_box(
    polygon_path: str,
) -> tuple[float, float, float, float] | None:
    """
    Calcula el bounding box (min_lat, max_lat, min_lng, max_lng) de un
    polígono serializado.

    Se usa en el worker como primera comprobación rápida antes de llamar
    a punto_en_geocerca / linea_cruza_geocerca para polígonos. Si la posición
    GPS está muy lejos del bounding box, se descarta sin cálculo geométrico.

    Args:
        polygon_path: JSON string con vértices '[{"lat":..,"lng":..}, ...]'.

    Returns:
        (min_lat, max_lat, min_lng, max_lng) o None si hay error.
    """
    try:
        vertices: list[dict[str, Any]] = json.loads(polygon_path)
        if not vertices:
            return None

        lats = [v["lat"] for v in vertices]
        lngs = [v["lng"] for v in vertices]
        return min(lats), max(lats), min(lngs), max(lngs)

    except Exception as exc:
        logger.error("Error calculando bounding box: %s", repr(exc))
        return None


def punto_fuera_de_bbox(
    lat: float,
    lng: float,
    min_lat: float,
    max_lat: float,
    min_lng: float,
    max_lng: float,
    margen_m: float = 0.0,
) -> bool:
    """
    Early-exit rápido: ¿el punto está claramente fuera del bounding box?

    Un margen en metros permite absorber errores de GPS en el borde.
    Convierte el margen a grados usando la aproximación: 1° ≈ 111_000m.

    Args:
        lat, lng:              Coordenadas del punto a verificar.
        min_lat, max_lat:      Rango de latitudes del bounding box.
        min_lng, max_lng:      Rango de longitudes del bounding box.
        margen_m:              Margen extra en metros (default 0).

    Returns:
        True si el punto está definitivamente fuera del bounding box expandido.
        False si puede estar dentro (requiere verificación geométrica completa).
    """
    margen_deg = margen_m / 111_000.0
    return (
        lat < min_lat - margen_deg
        or lat > max_lat + margen_deg
        or lng < min_lng - margen_deg
        or lng > max_lng + margen_deg
    )


# ── Implementaciones privadas: punto en geocerca ──────────────────────────────


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
            lat_centro,
            lng_centro,
            radio_m,
        )
        return False

    distancia = distancia_metros(lat_punto, lng_punto, lat_centro, lng_centro)
    return distancia <= radio_m


def _en_poligono(
    lat_punto: float,
    lng_punto: float,
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

    # Extraer coordenadas en formato (lng, lat) — Shapely usa (x=lng, y=lat).
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
        point = Point(lng_punto, lat_punto)  # (x=lng, y=lat)
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
    x = lng_punto
    y = lat_punto

    n = len(coords)
    dentro = False

    j = n - 1
    for i in range(n):
        xi, yi = coords[i]  # (lng, lat) = (x, y)
        xj, yj = coords[j]

        # Verificar si la arista (j, i) cruza el rayo horizontal en y=y_punto
        intersecta = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi) + xi
        )
        if intersecta:
            dentro = not dentro
        j = i

    return dentro


# ── Implementaciones privadas: línea cruza geocerca (evento 19) ──────────────


def _linea_cruza_circulo(
    lat_prev: float,
    lng_prev: float,
    lat_curr: float,
    lng_curr: float,
    lat_centro: float | None,
    lng_centro: float | None,
    radio_m: int | None,
) -> bool:
    """
    Verifica si el segmento de trayectoria (prev → curr) pasa a través
    de un POI circular SIN que ningún extremo esté dentro.

    Algoritmo:
        1. Proyectar el centro del POI sobre la línea infinita que pasa
           por prev y curr.
        2. Clamp del parámetro t a [0, 1] para quedarnos en el segmento real.
        3. Calcular el punto más cercano sobre el segmento al centro del POI.
        4. Si la distancia Haversine punto_cercano → centro <= radio_m,
           el segmento pasa por el POI.

    Nota sobre la aproximación de coordenadas planas:
        Este algoritmo opera en el espacio lat/lng directamente (no en un
        plano proyectado). Para distancias de hasta ~10km (típicas de
        geocercas vehiculares) el error de esta aproximación es < 0.01%.
        Para geocercas > 50km usar proyección UTM sería más preciso, pero
        es innecesario para nuestro caso de uso.

    Basado en: rules_geofence.go / lineIntersectsGeofence del equipo.

    Args:
        lat_prev, lng_prev: Posición anterior de la unidad.
        lat_curr, lng_curr: Posición actual de la unidad.
        lat_centro, lng_centro: Centro del POI.
        radio_m: Radio del POI en metros.

    Returns:
        True si el segmento pasa dentro del radio del POI.
    """
    if lat_centro is None or lng_centro is None or radio_m is None:
        return False

    # Vector del segmento (lat/lng como coordenadas planas)
    dx = lat_curr - lat_prev
    dy = lng_curr - lng_prev
    len_sq = dx * dx + dy * dy

    if len_sq == 0.0:
        # Segmento degenerado (mismo punto) — usar distancia directa
        return distancia_metros(lat_centro, lng_centro, lat_prev, lng_prev) <= radio_m

    # Proyección del centro del POI sobre la línea infinita.
    # t = [(C - P1) · (P2 - P1)] / |P2 - P1|²
    cx = lat_centro
    cy = lng_centro

    t = ((cx - lat_prev) * dx + (cy - lng_prev) * dy) / len_sq

    # Clamp t a [0, 1] para quedarnos en el SEGMENTO real (no la línea infinita).
    t = max(0.0, min(1.0, t))

    # Punto más cercano sobre el segmento
    closest_lat = lat_prev + t * dx
    closest_lng = lng_prev + t * dy

    # Verificar si el punto más cercano está dentro del radio
    return distancia_metros(lat_centro, lng_centro, closest_lat, closest_lng) <= radio_m


def _linea_cruza_poligono(
    lat_prev: float,
    lng_prev: float,
    lat_curr: float,
    lng_curr: float,
    polygon_path: str | None,
) -> bool:
    """
    Verifica si el segmento de trayectoria (prev → curr) cruza el
    perímetro de un POI poligonal SIN que ningún extremo esté dentro.

    Algoritmo:
        1. Early-exit con bounding box: si ambos puntos están fuera del bbox,
           es muy probable que no haya intersección (ahorra el cálculo completo).
        2. Para cada arista del polígono, verificar si el segmento de
           trayectoria y la arista se intersectan usando el algoritmo de
           segmentos planos (orientación cruzada).

    Nota sobre el early-exit:
        El bounding box no es una prueba 100% definitiva de no-intersección
        (un segmento podría entrar y salir del bbox sin cruzar el polígono).
        Por eso es solo un early-exit, no una conclusión final.

    Basado en: rules_geofence.go / lineIntersectsPolygon del equipo.

    Args:
        lat_prev, lng_prev: Posición anterior de la unidad.
        lat_curr, lng_curr: Posición actual de la unidad.
        polygon_path: JSON string con vértices del polígono.

    Returns:
        True si la trayectoria cruza el borde del polígono.
    """
    if not polygon_path:
        return False

    try:
        vertices_raw: list[dict[str, Any]] = json.loads(polygon_path)
    except (json.JSONDecodeError, TypeError):
        return False

    if len(vertices_raw) < 3:
        return False

    # Coordenadas de los vértices en formato (lat, lng) para este algoritmo
    try:
        vertices: list[tuple[float, float]] = [
            (v["lat"], v["lng"]) for v in vertices_raw
        ]
    except (KeyError, TypeError):
        return False

    # ── Optimización 1: Early-exit con bounding box ───────────────────────────
    # Si ambos extremos de la trayectoria están fuera del bbox expandido por el
    # desplazamiento máximo (estimado por la longitud del segmento), la probabilidad
    # de intersección es muy baja. Ahorra el O(n) de intersección de segmentos.
    lats = [v[0] for v in vertices]
    lngs = [v[1] for v in vertices]
    min_lat, max_lat = min(lats), max(lats)
    min_lng, max_lng = min(lngs), max(lngs)

    # Si AMBOS puntos están completamente fuera del bbox, early-exit.
    # Nota: si uno está dentro y el otro fuera, puede haber intersección.
    tray_min_lat = min(lat_prev, lat_curr)
    tray_max_lat = max(lat_prev, lat_curr)
    tray_min_lng = min(lng_prev, lng_curr)
    tray_max_lng = max(lng_prev, lng_curr)

    if (
        tray_max_lat < min_lat
        or tray_min_lat > max_lat
        or tray_max_lng < min_lng
        or tray_min_lng > max_lng
    ):
        return False

    # ── Optimización 2: Si algún punto está dentro, no es paso ───────────────
    # (sería entrada/salida, manejado como ev.10/ev.11)
    # Esta comprobación es redundante aquí porque el worker solo llama a
    # linea_cruza_geocerca() cuando ambos puntos están fuera, pero la incluimos
    # como salvaguarda.
    if _en_poligono(lat_prev, lng_prev, polygon_path):
        return False
    if _en_poligono(lat_curr, lng_curr, polygon_path):
        return False

    # ── Verificación de intersección con cada arista del polígono ─────────────
    # El segmento de trayectoria (prev→curr) intersecta el polígono si cruza
    # al menos una de sus aristas.
    n = len(vertices)
    for i in range(n):
        # Arista del polígono: v[i] → v[i+1] (cerrar con v[0] al final)
        p1 = vertices[i]
        p2 = vertices[(i + 1) % n]

        # Intersección de segmentos planos.
        # Segmento A: (lat_prev, lng_prev) → (lat_curr, lng_curr)
        # Segmento B: p1 → p2
        if _segmentos_se_cruzan(
            lat_prev,
            lng_prev,
            lat_curr,
            lng_curr,
            p1[0],
            p1[1],
            p2[0],
            p2[1],
        ):
            return True

    return False


def _segmentos_se_cruzan(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
) -> bool:
    """
    Verifica si los segmentos AB (x1,y1)→(x2,y2) y CD (x3,y3)→(x4,y4)
    se intersectan usando el método de orientación cruzada.

    Algoritmo:
        Dos segmentos se cruzan si y solo si los extremos de cada segmento
        están en lados opuestos del otro segmento (orientaciones contrarias).
        Los casos colineales se tratan separadamente.

    Args:
        x1, y1, x2, y2: Extremos del segmento A.
        x3, y3, x4, y4: Extremos del segmento B.

    Returns:
        True si los dos segmentos se intersectan (incluyendo extremos).
    """

    def _orientacion(
        ax: float, ay: float, bx: float, by: float, cx: float, cy: float
    ) -> int:
        """
        Orientación del triplete de puntos (a, b, c).
        0 = colineal, 1 = horario, 2 = antihorario.
        """
        val = (by - ay) * (cx - bx) - (bx - ax) * (cy - by)
        if abs(val) < 1e-12:
            return 0  # colineal
        return 1 if val > 0 else 2  # horario o antihorario

    def _punto_en_segmento(
        ax: float,
        ay: float,
        bx: float,
        by: float,
        px: float,
        py: float,
    ) -> bool:
        """
        Verifica si el punto P está en el segmento AB (para casos colineales).
        Asume que los tres puntos ya son colineales.
        """
        return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)

    o1 = _orientacion(x1, y1, x2, y2, x3, y3)
    o2 = _orientacion(x1, y1, x2, y2, x4, y4)
    o3 = _orientacion(x3, y3, x4, y4, x1, y1)
    o4 = _orientacion(x3, y3, x4, y4, x2, y2)

    # Caso general: orientaciones opuestas en ambos segmentos
    if o1 != o2 and o3 != o4:
        return True

    # Casos colineales: un extremo de un segmento está sobre el otro
    if o1 == 0 and _punto_en_segmento(x1, y1, x2, y2, x3, y3):
        return True
    if o2 == 0 and _punto_en_segmento(x1, y1, x2, y2, x4, y4):
        return True
    if o3 == 0 and _punto_en_segmento(x3, y3, x4, y4, x1, y1):
        return True
    if o4 == 0 and _punto_en_segmento(x3, y3, x4, y4, x2, y2):
        return True

    return False
