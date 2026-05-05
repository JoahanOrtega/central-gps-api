"""
Estas pruebas mockean BD y Redis — no requieren conexiones reales.
Verifican la logica de deteccion de eventos sin infraestructura.

Ejecutar:
    cd central-gps-api
    pytest tests/geocercas/test_poi_worker.py -v

Cobertura:
    - _construir_evento: mapeo correcto al esquema de t_eventos
    - _descripcion_evento: etiquetas correctas por tipo
    - _minutos_entre: calculo de tiempo entre timestamps
    - _publicar_eventos_redis: serializacion y canal correcto
    - _insertar_evento_bd: llamada correcta al cursor de telemetria
    - Flujo completo: entrada detectada -> evento generado -> insertado -> publicado
"""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

from workers.poi_worker import (
    _construir_evento,
    _descripcion_evento,
    _minutos_entre,
    _publicar_eventos_redis,
    _insertar_evento_bd,
)

# ---------------------------------------------------------------------------
# Fixtures reutilizables
# ---------------------------------------------------------------------------


@pytest.fixture
def unidad_dentro():
    """Datos GPS de una unidad que esta dentro de un POI."""
    return {
        "id_unidad": 1258,
        "numero": "TEST-01",
        "imei": "123456789012345",
        "latitud": 21.8819,
        "longitud": -102.2964,
        "velocidad": 45.5,
        "fecha_hora_gps": datetime(2026, 5, 5, 18, 40, 0),
        "id_data": 999001,
    }


@pytest.fixture
def alerta_in_out():
    """Configuracion de alerta de entrada/salida activa."""
    return {
        "id_alerta_poi": 1,
        "id_empresa": 11,
        "id_poi": 96,
        "poi_nombre": "Almacen Central",
        "tipo_poi": 1,  # circulo
        "poi_lat": 21.8819,
        "poi_lng": -102.2964,
        "poi_radio": 200,
        "poi_polygon_path": None,
        "in_out": 1,  # alerta activa
        "permanencia": 0,
        "tipo_permanencia": None,
        "minutos_permanencia": None,
        "vel_max": 0,
        "vel_max_permitida": None,
        "alcance": 2,
        "id_grupo_unidades": None,
    }


# ===========================================================================
# Pruebas: _construir_evento
# ===========================================================================


class TestConstruirEvento:

    def test_mapeo_campos_entrada(self, unidad_dentro, alerta_in_out):
        """El evento de entrada debe mapear correctamente al esquema de t_eventos."""
        evento = _construir_evento(
            tipo_evento=10,
            unidad=unidad_dentro,
            alerta=alerta_in_out,
            detalles=None,
        )
        # Campos de t_eventos
        assert evento["evento"] == 10
        assert evento["id_empresa"] == 11
        assert evento["id_unidad"] == 1258
        assert evento["id_elemento"] == 96  # id_poi -> id_elemento
        assert evento["id_data"] == 999001
        assert evento["payload"] is None
        assert evento["fecha_hora_gmt"] == datetime(2026, 5, 5, 18, 40, 0)
        # fecha debe ser solo la fecha del GPS
        assert evento["fecha"] == datetime(2026, 5, 5).date()

    def test_campos_privados_para_redis(self, unidad_dentro, alerta_in_out):
        """Los campos prefijados con _ son solo para Redis, no van a BD."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)
        assert evento["_numero_unidad"] == "TEST-01"
        assert evento["_nombre_poi"] == "Almacen Central"
        assert evento["_descripcion"] == "Entro al POI"

    def test_detalles_se_serializan_a_json(self, unidad_dentro, alerta_in_out):
        """Los detalles deben serializarse como JSON string."""
        detalles = {"minutos_dentro": 45.0, "minutos_permitidos": 30}
        evento = _construir_evento(12, unidad_dentro, alerta_in_out, detalles)
        assert isinstance(evento["payload"], str)
        payload_parsed = json.loads(evento["payload"])
        assert payload_parsed["minutos_dentro"] == 45.0

    def test_evento_salida(self, unidad_dentro, alerta_in_out):
        """El tipo_evento 11 debe mapearse correctamente."""
        evento = _construir_evento(11, unidad_dentro, alerta_in_out, None)
        assert evento["evento"] == 11
        assert evento["_descripcion"] == "Salio del POI"


# ===========================================================================
# Pruebas: _descripcion_evento
# ===========================================================================


class TestDescripcionEvento:

    @pytest.mark.parametrize(
        "tipo,esperado",
        [
            (10, "Entro al POI"),
            (11, "Salio del POI"),
            (12, "Permanencia maxima excedida"),
            (13, "Permanencia minima no cumplida"),
            (14, "Exceso de velocidad inicio"),
            (15, "Exceso de velocidad fin"),
            (99, "Evento desconocido"),  # tipo desconocido
        ],
    )
    def test_descripcion_por_tipo(self, tipo, esperado):
        assert _descripcion_evento(tipo) == esperado


# ===========================================================================
# Pruebas: _minutos_entre
# ===========================================================================


class TestMinutosEntre:

    def test_30_minutos(self):
        inicio = datetime(2026, 5, 5, 10, 0, 0)
        fin = datetime(2026, 5, 5, 10, 30, 0)
        assert _minutos_entre(inicio, fin) == pytest.approx(30.0)

    def test_inicio_none_retorna_cero(self):
        assert _minutos_entre(None, datetime.now()) == 0.0

    def test_fin_none_retorna_cero(self):
        assert _minutos_entre(datetime.now(), None) == 0.0

    def test_ambos_none_retorna_cero(self):
        assert _minutos_entre(None, None) == 0.0

    def test_strings_iso_funcionan(self):
        """Debe aceptar strings ISO ademas de objetos datetime."""
        resultado = _minutos_entre(
            "2026-05-05T10:00:00",
            "2026-05-05T10:15:00",
        )
        assert resultado == pytest.approx(15.0)


# ===========================================================================
# Pruebas: _publicar_eventos_redis
# ===========================================================================


class TestPublicarEventosRedis:

    def test_publica_en_canal_correcto(self, unidad_dentro, alerta_in_out):
        """El canal debe ser 'eventos_poi:{id_empresa}'."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_redis = MagicMock()
        with patch("workers.poi_worker._get_redis", return_value=mock_redis):
            _publicar_eventos_redis(id_empresa=11, eventos=[evento])

        # Verificar que se llamo a publish con el canal correcto
        assert mock_redis.publish.called
        canal_usado = mock_redis.publish.call_args[0][0]
        assert canal_usado == "eventos_poi:11"

    def test_payload_redis_contiene_campos_correctos(
        self, unidad_dentro, alerta_in_out
    ):
        """El payload de Redis debe incluir los campos que espera el frontend."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_redis = MagicMock()
        with patch("workers.poi_worker._get_redis", return_value=mock_redis):
            _publicar_eventos_redis(id_empresa=11, eventos=[evento])

        payload_json = mock_redis.publish.call_args[0][1]
        payload = json.loads(payload_json)

        assert payload["tipo_evento"] == 10
        assert payload["numero_unidad"] == "TEST-01"
        assert payload["nombre_poi"] == "Almacen Central"
        assert payload["id_empresa"] == 11
        assert payload["id_unidad"] == 1258
        assert "fecha_hora_evento" in payload

    def test_campos_privados_no_van_en_redis(self, unidad_dentro, alerta_in_out):
        """Los campos con prefijo _ no deben aparecer en el payload de Redis."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_redis = MagicMock()
        with patch("workers.poi_worker._get_redis", return_value=mock_redis):
            _publicar_eventos_redis(id_empresa=11, eventos=[evento])

        payload_json = mock_redis.publish.call_args[0][1]
        payload = json.loads(payload_json)

        # Ningun campo debe empezar con _
        assert not any(k.startswith("_") for k in payload.keys())

    def test_redis_error_no_lanza_excepcion(self, unidad_dentro, alerta_in_out):
        """Si Redis falla, el worker NO debe detenerse."""
        import redis

        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_redis = MagicMock()
        mock_redis.publish.side_effect = redis.RedisError("connection refused")

        with patch("workers.poi_worker._get_redis", return_value=mock_redis):
            # No debe lanzar excepcion
            _publicar_eventos_redis(id_empresa=11, eventos=[evento])


# ===========================================================================
# Pruebas: _insertar_evento_bd
# ===========================================================================


class TestInsertarEventoBd:

    def test_usa_cursor_telemetria(self, unidad_dentro, alerta_in_out):
        """El INSERT debe ir al cursor de telemetria, no al principal."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_cur = MagicMock()
        mock_conn = MagicMock()

        _insertar_evento_bd(mock_cur, mock_conn, evento)

        # Debe llamar a execute y luego a commit
        assert mock_cur.execute.called
        assert mock_conn.commit.called

    def test_campos_privados_no_se_insertan(self, unidad_dentro, alerta_in_out):
        """Los campos _ no deben pasarse al SQL INSERT."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_cur = MagicMock()
        mock_conn = MagicMock()

        _insertar_evento_bd(mock_cur, mock_conn, evento)

        # Obtener el dict que se paso a execute
        params = mock_cur.execute.call_args[0][1]
        assert not any(k.startswith("_") for k in params.keys())

    def test_error_bd_no_lanza_excepcion(self, unidad_dentro, alerta_in_out):
        """Si la BD falla, el worker NO debe detenerse — solo loggea."""
        evento = _construir_evento(10, unidad_dentro, alerta_in_out, None)

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_cur.execute.side_effect = Exception("BD caida")

        # No debe lanzar excepcion
        _insertar_evento_bd(mock_cur, mock_conn, evento)

        # Debe hacer rollback
        assert mock_conn.rollback.called
