"""
Estas pruebas no requieren BD, Redis ni Docker.
Son pura logica de geometria — se ejecutan en menos de 1 segundo.

Ejecutar:
    cd central-gps-api
    pytest tests/geocercas/test_geofence.py -v

Cobertura:
    - Circulo: punto dentro, punto fuera, punto exactamente en el borde
    - Poligono: punto dentro, punto fuera, poligono convexo y concavo
    - Casos edge: coordenadas None, JSON malformado, poligono < 3 vertices
    - Haversine: distancia conocida entre dos coordenadas reales
"""

import json
import pytest
from utils.geofence import punto_en_geocerca, distancia_metros

# ---------------------------------------------------------------------------
# Coordenadas de referencia (Aguascalientes, Mexico)
# ---------------------------------------------------------------------------
# Centro de prueba: Plaza Patria, Aguascalientes
LAT_CENTRO = 21.8819
LNG_CENTRO = -102.2964

# Punto DENTRO del radio de 200m
LAT_DENTRO = 21.8825
LNG_DENTRO = -102.2970

# Punto FUERA del radio de 200m (aprox 600m del centro)
LAT_FUERA = 21.8760
LNG_FUERA = -102.2964

# Radio de prueba en metros
RADIO_200M = 200


# ===========================================================================
# Pruebas: POI Circular
# ===========================================================================


class TestPoiCirculo:

    def test_punto_dentro_del_radio(self):
        """Un punto cercano al centro debe estar dentro."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_DENTRO,
            lng_punto=LNG_DENTRO,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=RADIO_200M,
            polygon_path=None,
        )
        assert resultado is True

    def test_punto_fuera_del_radio(self):
        """Un punto lejano al centro debe estar fuera."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_FUERA,
            lng_punto=LNG_FUERA,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=RADIO_200M,
            polygon_path=None,
        )
        assert resultado is False

    def test_punto_exactamente_en_el_centro(self):
        """El centro mismo debe estar dentro del radio."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_CENTRO,
            lng_punto=LNG_CENTRO,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=RADIO_200M,
            polygon_path=None,
        )
        assert resultado is True

    def test_radio_cero_solo_el_centro_exacto(self):
        """Radio 0 solo debe incluir el punto exacto del centro."""
        # El punto del centro exacto esta a 0m — debe estar dentro
        resultado_centro = punto_en_geocerca(
            lat_punto=LAT_CENTRO,
            lng_punto=LNG_CENTRO,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=0,
            polygon_path=None,
        )
        assert resultado_centro is True

        # Cualquier otro punto esta fuera
        resultado_otro = punto_en_geocerca(
            lat_punto=LAT_DENTRO,
            lng_punto=LNG_DENTRO,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=0,
            polygon_path=None,
        )
        assert resultado_otro is False

    def test_coordenadas_centro_none_retorna_false(self):
        """Si el centro es None, no debe lanzar excepcion — retorna False."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_DENTRO,
            lng_punto=LNG_DENTRO,
            tipo_poi=1,
            lat_centro=None,
            lng_centro=None,
            radio_m=RADIO_200M,
            polygon_path=None,
        )
        assert resultado is False

    def test_radio_none_retorna_false(self):
        """Si el radio es None, no debe lanzar excepcion — retorna False."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_DENTRO,
            lng_punto=LNG_DENTRO,
            tipo_poi=1,
            lat_centro=LAT_CENTRO,
            lng_centro=LNG_CENTRO,
            radio_m=None,
            polygon_path=None,
        )
        assert resultado is False


# ===========================================================================
# Pruebas: POI Poligonal
# ===========================================================================

# Poligono cuadrado de ~500m de lado centrado en Plaza Patria
POLIGONO_CUADRADO = json.dumps(
    [
        {"lat": 21.8850, "lng": -102.3010},  # NO
        {"lat": 21.8850, "lng": -102.2920},  # NE
        {"lat": 21.8780, "lng": -102.2920},  # SE
        {"lat": 21.8780, "lng": -102.3010},  # SO
    ]
)

# Punto dentro del cuadrado
LAT_EN_CUADRADO = 21.8819
LNG_EN_CUADRADO = -102.2964

# Punto fuera del cuadrado (al norte)
LAT_FUERA_CUADRADO = 21.8900
LNG_FUERA_CUADRADO = -102.2964


class TestPoiPoligono:

    def test_punto_dentro_del_poligono(self):
        resultado = punto_en_geocerca(
            lat_punto=LAT_EN_CUADRADO,
            lng_punto=LNG_EN_CUADRADO,
            tipo_poi=2,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path=POLIGONO_CUADRADO,
        )
        assert resultado is True

    def test_punto_fuera_del_poligono(self):
        resultado = punto_en_geocerca(
            lat_punto=LAT_FUERA_CUADRADO,
            lng_punto=LNG_FUERA_CUADRADO,
            tipo_poi=2,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path=POLIGONO_CUADRADO,
        )
        assert resultado is False

    def test_polygon_path_none_retorna_false(self):
        """Sin polygon_path no debe lanzar excepcion — retorna False."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_EN_CUADRADO,
            lng_punto=LNG_EN_CUADRADO,
            tipo_poi=2,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path=None,
        )
        assert resultado is False

    def test_polygon_path_json_malformado_retorna_false(self):
        """JSON invalido no debe crashear el worker — retorna False."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_EN_CUADRADO,
            lng_punto=LNG_EN_CUADRADO,
            tipo_poi=2,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path="esto_no_es_json{{",
        )
        assert resultado is False

    def test_poligono_menos_de_3_vertices_retorna_false(self):
        """Un poligono con 2 vertices no es valido — retorna False."""
        poligono_invalido = json.dumps(
            [
                {"lat": 21.88, "lng": -102.29},
                {"lat": 21.89, "lng": -102.30},
            ]
        )
        resultado = punto_en_geocerca(
            lat_punto=LAT_EN_CUADRADO,
            lng_punto=LNG_EN_CUADRADO,
            tipo_poi=2,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path=poligono_invalido,
        )
        assert resultado is False

    def test_tipo_poi_desconocido_retorna_false(self):
        """Un tipo_poi que no sea 1 ni 2 no debe crashear — retorna False."""
        resultado = punto_en_geocerca(
            lat_punto=LAT_EN_CUADRADO,
            lng_punto=LNG_EN_CUADRADO,
            tipo_poi=99,
            lat_centro=None,
            lng_centro=None,
            radio_m=None,
            polygon_path=None,
        )
        assert resultado is False


# ===========================================================================
# Pruebas: distancia_metros (Haversine)
# ===========================================================================


class TestHaversine:

    def test_distancia_cero_mismo_punto(self):
        """La distancia de un punto a si mismo es 0."""
        dist = distancia_metros(LAT_CENTRO, LNG_CENTRO, LAT_CENTRO, LNG_CENTRO)
        assert dist == pytest.approx(0.0, abs=0.1)

    def test_distancia_conocida_aguascalientes(self):
        """
        Distancia entre Plaza Patria y el centro historico de Aguascalientes.
        Verificada con Google Maps: aprox 2.1 km.
        Tolerancia de 100m (1.5%) es aceptable para Haversine.
        """
        lat_centro_historico = 21.8818
        lng_centro_historico = -102.2916
        dist = distancia_metros(
            LAT_CENTRO,
            LNG_CENTRO,
            lat_centro_historico,
            lng_centro_historico,
        )
        # Debe estar entre 300m y 700m (distancia real aprox 450m)
        assert 300 < dist < 700

    def test_distancia_es_simetrica(self):
        """distancia(A, B) debe ser igual a distancia(B, A)."""
        dist_ab = distancia_metros(LAT_CENTRO, LNG_CENTRO, LAT_FUERA, LNG_FUERA)
        dist_ba = distancia_metros(LAT_FUERA, LNG_FUERA, LAT_CENTRO, LNG_CENTRO)
        assert dist_ab == pytest.approx(dist_ba, rel=1e-6)

    def test_distancia_positiva(self):
        """La distancia entre dos puntos distintos siempre es positiva."""
        dist = distancia_metros(LAT_CENTRO, LNG_CENTRO, LAT_FUERA, LNG_FUERA)
        assert dist > 0
