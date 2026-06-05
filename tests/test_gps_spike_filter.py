"""
test_gps_spike_filter.py — Pruebas unitarias del filtro de saltos GPS

Ubicación en el proyecto:
    central-gps-api/tests/test_gps_spike_filter.py

Cómo correr:
    # Desde la raíz de central-gps-api/
    python tests/test_gps_spike_filter.py

No requiere pytest ni Docker. Solo stdlib de Python.

Casos cubiertos:
    1. Lista vacía              → retorna lista vacía sin errores
    2. Un solo punto            → retorna ese mismo punto sin procesarlo
    3. Recorrido limpio         → ningún punto eliminado
    4. Salto extremo de odo     → punto con delta 500 km en 30 seg eliminado
    5. Salto doble velocidad    → dos ticks con vel. estimada > 130 km/h eliminados
    6. Coordenadas None         → no lanza excepción, punto conservado por precaución
"""

import sys
import os
from datetime import datetime, timedelta

# Apuntar al raíz del proyecto (un nivel arriba de tests/)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.gps_spike_filter import filter_gps_spikes

# ── Helper para construir tuplas fake con la misma estructura que _ROUTE_QUERY ──


def _punto(
    lat: float,
    lng: float,
    velocidad: float = 60.0,
    odometro: float = 0.0,
    offset_segundos: int = 0,
) -> tuple:
    """
    Construye una tupla de prueba con la estructura de _ROUTE_QUERY:
        0 → fecha_hora_gps
        1 → latitud
        2 → longitud
        3 → velocidad
        4 → grados
        5 → status
        6 → tipo_alerta
        7 → odometro
    """
    base = datetime(2024, 6, 1, 8, 0, 0)
    return (
        base + timedelta(seconds=offset_segundos),
        lat,
        lng,
        velocidad,
        0.0,
        "1000000000",
        33,
        odometro,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Casos de prueba
# ══════════════════════════════════════════════════════════════════════════════


def test_lista_vacia():
    """Lista vacía → retorna lista vacía sin errores."""
    r = filter_gps_spikes([])
    assert r == [], f"Esperaba [], obtuve {r}"
    print("✅ test_lista_vacia")


def test_un_solo_punto():
    """Un único punto → retorna ese mismo punto sin procesarlo."""
    p = _punto(21.881, -102.291, odometro=1000.0)
    r = filter_gps_spikes([p])
    assert len(r) == 1 and r[0] == p
    print("✅ test_un_solo_punto")


def test_recorrido_limpio():
    """
    Recorrido normal con coordenadas coherentes.
    Ningún punto debe eliminarse.

    Simula una unidad moviéndose ~111m cada 30 seg (~13 km/h).
    El odómetro avanza al mismo ritmo que la distancia real.
    """
    puntos = [
        _punto(
            21.8800 + i * 0.001,
            -102.291,
            velocidad=12.0,
            odometro=1000.0 + i * 100,
            offset_segundos=i * 30,
        )
        for i in range(5)
    ]
    r = filter_gps_spikes(puntos)
    assert len(r) == 5, f"Recorrido limpio: esperaba 5 puntos, obtuve {len(r)}"
    print("✅ test_recorrido_limpio")


def test_salto_extremo_odometro():
    """
    El odómetro salta 500 km en 30 segundos pero la coordenada
    apenas cambia ~11 metros → pm > 800 → punto eliminado.

    Escenario real: dispositivo GPS con bug de firmware que reporta
    un odómetro acumulado absurdo durante un tick.
    """
    puntos = [
        _punto(21.8800, -102.2910, velocidad=12.0, odometro=1_000.0, offset_segundos=0),
        _punto(
            21.8801, -102.2910, velocidad=12.0, odometro=501_000.0, offset_segundos=30
        ),  # ← basura
        _punto(
            21.8802, -102.2910, velocidad=12.0, odometro=501_100.0, offset_segundos=60
        ),
    ]
    r = filter_gps_spikes(puntos)
    assert len(r) == 2, f"Se esperaban 2 puntos (1 eliminado), obtuve {len(r)}"
    assert r[0] == puntos[0], "El primer punto debe conservarse"
    assert r[1] == puntos[2], "El tercer punto (válido) debe conservarse"
    print("✅ test_salto_extremo_odometro")


def test_salto_doble_velocidad_estimada():
    """
    Dos puntos consecutivos donde las coordenadas implican > 130 km/h
    (teletransporte ~50 km en 30 segundos) → ambos eliminados.

    Escenario real: señal GPS rebota en edificios y registra
    la posición de una antena lejana durante dos ticks seguidos.
    """
    puntos = [
        _punto(21.8800, -102.2910, velocidad=60.0, odometro=1000.0, offset_segundos=0),
        _punto(
            22.3300, -102.2910, velocidad=60.0, odometro=1500.0, offset_segundos=30
        ),  # ← salto
        _punto(
            22.7800, -102.2910, velocidad=60.0, odometro=2000.0, offset_segundos=60
        ),  # ← salto
        _punto(21.8803, -102.2910, velocidad=60.0, odometro=2100.0, offset_segundos=90),
    ]
    r = filter_gps_spikes(puntos)
    assert r[0] == puntos[0], "El primer punto siempre debe conservarse"
    coords = [(row[1], row[2]) for row in r]
    assert (22.3300, -102.2910) not in coords, "Punto A del salto no fue eliminado"
    print("✅ test_salto_doble_velocidad_estimada")


def test_coordenadas_none():
    """
    Puntos con lat/lng None no deben causar excepción.
    Se conservan por precaución — no podemos calcular si son basura.
    """
    puntos = [
        _punto(21.8800, -102.2910, odometro=1000.0, offset_segundos=0),
        (
            datetime(2024, 6, 1, 8, 0, 30),
            None,
            None,
            60.0,
            0.0,
            "1000000000",
            33,
            1100.0,
        ),
        _punto(21.8802, -102.2910, odometro=1200.0, offset_segundos=60),
    ]
    r = filter_gps_spikes(puntos)
    assert len(r) >= 2, f"Con coords None: esperaba >= 2 puntos, obtuve {len(r)}"
    print("✅ test_coordenadas_none")


# ══════════════════════════════════════════════════════════════════════════════
# Runner (sin pytest)
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        test_lista_vacia,
        test_un_solo_punto,
        test_recorrido_limpio,
        test_salto_extremo_odometro,
        test_salto_doble_velocidad_estimada,
        test_coordenadas_none,
    ]

    fallidos = []
    print("\n── Pruebas de gps_spike_filter ──\n")

    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"❌ {test.__name__} — {e}")
            fallidos.append(test.__name__)
        except Exception as e:
            print(f"❌ {test.__name__} — excepción inesperada: {e}")
            fallidos.append(test.__name__)

    total = len(tests)
    pasaron = total - len(fallidos)
    print(
        f"\n── Resultado: {pasaron}/{total} pruebas pasaron {'✅' if not fallidos else ''} ──\n"
    )

    if fallidos:
        print("Fallaron:")
        for nombre in fallidos:
            print(f"  - {nombre}")
        sys.exit(1)
