"""
operator_routes.py — Endpoints del catálogo de operadores (conductores).

Registrar en app.py:
    from routes.operator_routes import operator_bp
    app.register_blueprint(operator_bp)

Endpoints:
    GET    /operadores          Lista operadores activos (con búsqueda)
    GET    /operadores/<id>     Obtiene un operador individual
    POST   /operadores          Crea un operador
    PATCH  /operadores/<id>     Actualiza campos de un operador
    DELETE /operadores/<id>     Soft-delete (status=0)
"""

import logging
from flask import Blueprint, jsonify, request

from services.operator_service import (
    get_operators,
    get_operator,
    create_operator,
    update_operator,
    delete_operator,
)
from utils.auth_guard import jwt_required, validate_empresa_access
from utils.validation import validate_payload
from validators.operator_validators import (
    CreateOperatorSchema,
    UpdateOperatorSchema,
)

operator_bp = Blueprint("operator", __name__)

logger = logging.getLogger(__name__)


# ─── Helper: resolver id_empresa de contexto ──────────────────────────────────
# Mismo patrón que poi_routes: query param → body → JWT. Retorna
# (id_empresa, id_usuario, error_response). Si error_response no es None, el
# caller lo devuelve directo.
def _resolve_empresa_context(body=None):
    id_empresa = (
        request.args.get("id_empresa", type=int)
        or (body or {}).get("id_empresa")
        or request.user.get("id_empresa")
    )
    id_usuario = request.user.get("sub")

    if not id_empresa or not id_usuario:
        return (
            None,
            None,
            (jsonify({"error": "Datos de autenticación incompletos"}), 400),
        )

    if not validate_empresa_access(id_empresa, request.user):
        return (
            None,
            None,
            (jsonify({"error": "Acceso no autorizado a esta empresa"}), 403),
        )

    return id_empresa, id_usuario, None


@operator_bp.route("/operadores", methods=["GET"])
@jwt_required
def list_operators():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_operators(id_empresa, search if search else None)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operadores/<int:id_operador>", methods=["GET"])
@jwt_required
def get_one_operator(id_operador: int):
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        operador = get_operator(id_operador, id_empresa)
        if operador is None:
            return jsonify({"error": "Operador no encontrado"}), 404
        return jsonify(operador), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operadores", methods=["POST"])
@jwt_required
def save_operator():
    """
    Crea un nuevo operador.

    Validación (marshmallow):
      - nombre: obligatorio, max 200 chars
      - rfid_tag: opcional, max 50 chars
      - id_grupo_operadores: lista de IDs de grupo (opcional)

    Respuesta en error:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)
    data, validation_error = validate_payload(CreateOperatorSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp

        result = create_operator(data, id_empresa, id_usuario)
        return (
            jsonify({"message": "Operador creado correctamente", "operador": result}),
            201,
        )
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operadores/<int:id_operador>", methods=["PATCH"])
@jwt_required
def patch_operator(id_operador: int):
    """Actualiza campos de un operador. Solo aplica los campos enviados."""
    data = request.get_json(silent=True)
    data, validation_error = validate_payload(UpdateOperatorSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp

        result = update_operator(id_operador, id_empresa, data, id_usuario)
        if result is None:
            return jsonify({"error": "Operador no encontrado"}), 404
        return jsonify({"message": "Operador actualizado", "operador": result}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operadores/<int:id_operador>", methods=["DELETE"])
@jwt_required
def remove_operator(id_operador: int):
    """Soft-delete del operador (status=0)."""
    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context()
        if error_resp:
            return error_resp

        ok = delete_operator(id_operador, id_empresa, id_usuario)
        if not ok:
            return jsonify({"error": "Operador no encontrado"}), 404
        return jsonify({"message": "Operador eliminado"}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
