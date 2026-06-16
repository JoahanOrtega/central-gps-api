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

from services.operator_group_service import (
    get_operator_groups,
    get_operator_group_by_id,
    create_operator_group,
    update_operator_group,
    delete_operator_group,
)
from services.operator_assignment_service import (
    assign_operator_to_unit,
    unassign_operator,
)
from validators.operator_validators import (
    CreateOperatorGroupSchema,
    UpdateOperatorGroupSchema,
)
from services.operator_service import (
    get_operators,
    get_operator,
    create_operator,
    update_operator,
    delete_operator,
)
from utils.auth_guard import jwt_required, validate_empresa_access
from utils.validation import validate_payload

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
    logger.warning(
        "DEBUG ctx: id_empresa=%r id_usuario=%r body=%r user_keys=%r",
        id_empresa,
        id_usuario,
        body,
        list(request.user.keys()),
    )

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


# ─── Grupos de operadores ─────────────────────────────────────────────────────


@operator_bp.route("/operador-grupos", methods=["GET"])
@jwt_required
def list_operator_groups():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_operator_groups(id_empresa, search)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operador-grupos/<int:id_grupo>", methods=["GET"])
@jwt_required
def get_one_operator_group(id_grupo: int):
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        grupo = get_operator_group_by_id(id_grupo, id_empresa)
        if grupo is None:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify(grupo), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operador-grupos", methods=["POST"])
@jwt_required
def save_operator_group():
    data = request.get_json(silent=True)
    data, validation_error = validate_payload(CreateOperatorGroupSchema(), data)
    if validation_error:
        return validation_error
    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp
        id_grupo = create_operator_group(data, id_empresa, id_usuario)
        return (
            jsonify({"message": "Grupo creado", "id_grupo_operadores": id_grupo}),
            201,
        )
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operador-grupos/<int:id_grupo>", methods=["PUT"])
@jwt_required
def edit_operator_group(id_grupo: int):
    data = request.get_json(silent=True)
    data, validation_error = validate_payload(UpdateOperatorGroupSchema(), data)
    if validation_error:
        return validation_error
    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp
        ok = update_operator_group(id_grupo, data, id_empresa, id_usuario)
        if not ok:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify({"message": "Grupo actualizado"}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operador-grupos/<int:id_grupo>", methods=["DELETE"])
@jwt_required
def remove_operator_group(id_grupo: int):
    try:
        id_empresa, _id_usuario, error_resp = _resolve_empresa_context()
        if error_resp:
            return error_resp
        ok = delete_operator_group(id_grupo, id_empresa)
        if not ok:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify({"message": "Grupo eliminado"}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── Asignación operador : unidad ─────────────────────────────────────────────


@operator_bp.route("/operadores/<int:id_operador>/asignar", methods=["POST"])
@jwt_required
def assign_operator(id_operador: int):
    """
    Asigna (o reasigna) un operador a una unidad de forma exclusiva.

    Body: { "id_unidad": <int>, "fecha_asignacion": "YYYY-MM-DD" (opcional) }
    Pasar id_unidad=0 desasigna al operador de su unidad actual.
    """
    data = request.get_json(silent=True) or {}
    try:
        _id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp

        id_unidad = data.get("id_unidad", 0)
        fecha = data.get("fecha_asignacion")
        assign_operator_to_unit(id_operador, id_unidad, id_usuario, fecha)
        return jsonify({"message": "Asignación actualizada"}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@operator_bp.route("/operadores/<int:id_operador>/desasignar", methods=["POST"])
@jwt_required
def unassign_operator_route(id_operador: int):
    """Rompe el vínculo del operador con su unidad actual."""
    try:
        _id_empresa, id_usuario, error_resp = _resolve_empresa_context()
        if error_resp:
            return error_resp
        unassign_operator(id_operador, 0, id_usuario)
        return jsonify({"message": "Operador desasignado"}), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
