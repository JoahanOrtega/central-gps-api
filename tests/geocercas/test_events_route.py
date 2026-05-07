"""
Estas pruebas usan el cliente de Flask (no requieren Redis ni BD reales).
Verifican que el endpoint SSE valida correctamente el JWT y resuelve
id_empresa para usuarios normales y para sudo_erp.

Ejecutar:
    cd central-gps-api
    pytest tests/geocercas/test_events_route.py -v

Cobertura:
    - GET /events/stream sin token -> 401
    - GET /events/stream con token invalido -> 401
    - GET /events/stream con token de usuario normal (id_empresa en JWT) -> 200 SSE
    - GET /events/stream con token sudo_erp sin ?id_empresa -> 403
    - GET /events/stream con token sudo_erp con ?id_empresa -> 200 SSE
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from app import create_app
from utils.jwt_handler import generate_jwt

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app():
    """App Flask en modo testing — sin worker ni BD."""
    import os

    os.environ["FLASK_TESTING"] = "true"
    os.environ["WORKER_ENABLED"] = "false"
    application = create_app()
    application.config["TESTING"] = True
    yield application


@pytest.fixture(scope="module")
def client(app):
    return app.test_client()


def _generar_token(id_empresa=None, rol="usuario"):
    """Helper: genera un JWT valido para los tests."""
    return generate_jwt(
        {
            "id": 1,
            "username": "test_user",
            "nombre": "Test",
            "perfil": 0,
            "rol": rol,
            "id_empresa": id_empresa,
            "nombre_empresa": "Empresa Test" if id_empresa else None,
            "permisos": ["mapa.ver"],
        }
    )


# ---------------------------------------------------------------------------
# Pruebas de autenticacion
# ---------------------------------------------------------------------------


class TestEventosSSEAutenticacion:

    def test_sin_token_retorna_401(self, client):
        """Sin token debe rechazar la conexion."""
        resp = client.get("/events/stream")
        assert resp.status_code == 401
        data = json.loads(resp.data)
        assert "Token requerido" in data["error"]

    def test_token_invalido_retorna_401(self, client):
        """Un JWT corrupto debe rechazar la conexion."""
        resp = client.get("/events/stream?token=esto.no.es.un.jwt.valido")
        assert resp.status_code == 401
        data = json.loads(resp.data)
        assert "Token" in data["error"]

    def test_usuario_normal_con_empresa_en_jwt(self, client):
        """
        Usuario normal con id_empresa en el JWT debe conectarse exitosamente.
        Mockeamos el generador SSE para no necesitar Redis real.
        """
        token = _generar_token(id_empresa=11, rol="usuario")

        def mock_generador(id_empresa):
            yield "event: connected\ndata: {}\n\n"

        with patch(
            "routes.events_routes._generar_eventos_sse", side_effect=mock_generador
        ):
            resp = client.get(f"/events/stream?token={token}")
            assert resp.status_code == 200
            assert resp.content_type.startswith("text/event-stream")

    def test_sudo_erp_sin_id_empresa_retorna_403(self, client):
        """sudo_erp sin ?id_empresa en la URL debe recibir 403."""
        token = _generar_token(id_empresa=None, rol="sudo_erp")
        resp = client.get(f"/events/stream?token={token}")
        assert resp.status_code == 403
        data = json.loads(resp.data)
        assert "empresa" in data["error"].lower()

    def test_sudo_erp_con_id_empresa_en_url(self, client):
        """sudo_erp con ?id_empresa en la URL debe conectarse exitosamente."""
        token = _generar_token(id_empresa=None, rol="sudo_erp")

        def mock_generador(id_empresa):
            yield "event: connected\ndata: {}\n\n"

        with patch(
            "routes.events_routes._generar_eventos_sse", side_effect=mock_generador
        ):
            resp = client.get(f"/events/stream?token={token}&id_empresa=11")
            assert resp.status_code == 200

    def test_id_empresa_en_jwt_tiene_prioridad_sobre_url(self, client):
        """
        Para usuario normal, el id_empresa del JWT debe usarse aunque
        tambien venga en la URL — evita que el cliente se suscriba
        a eventos de otra empresa.
        """
        token = _generar_token(id_empresa=11, rol="usuario")
        capturado = {}

        def mock_generador(id_empresa):
            capturado["id_empresa"] = id_empresa
            yield "event: connected\ndata: {}\n\n"

        with patch(
            "routes.events_routes._generar_eventos_sse", side_effect=mock_generador
        ):
            # Intenta suscribirse a empresa 99 pero su JWT dice empresa 11
            client.get(f"/events/stream?token={token}&id_empresa=99")
            assert capturado.get("id_empresa") == 11
