"""Endpoints REST del catálogo de Rutas

Rutas expuestas bajo /operation/routes:
    GET    /operation/routes           lista
    GET    /operation/routes/<id>      detalle completo (para editar)
    POST   /operation/routes           crear
    PUT    /operation/routes/<id>      editar
    DELETE /operation/routes/<id>      eliminar (soft-delete)
"""

import logging
from flask import Blueprint, request, jsonify

from services.route_service import (
    get_routes,
    get_route_by_id,
    create_route,
    update_route,
    delete_route,
    is_clave_taken,
)
from validators.route_validators import CreateRouteSchema, UpdateRouteSchema
from utils.validation import validate_payload
from utils.auth_guard import jwt_required, validate_empresa_access

logger = logging.getLogger(__name__)

route_bp = Blueprint("routes", __name__)


def _resolve_context(body=None):
    """Resuelve id_empresa e id_usuario.

    El sudo_erp no tiene id_empresa fijo en el JWT, así que lo lee del body
    o del query string. Mismo patrón que el módulo de clientes.
    """
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


@route_bp.route("/operation/routes", methods=["GET"])
@jwt_required
def list_routes():
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error
        search = request.args.get("search", "").strip()
        routes = get_routes(id_empresa, search)
        return jsonify(routes), 200
    except Exception as exc:
        logger.error("Error en GET /operation/routes: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@route_bp.route("/operation/routes/<int:id_ruta>", methods=["GET"])
@jwt_required
def get_route(id_ruta: int):
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error
        route = get_route_by_id(id_ruta, id_empresa)
        if not route:
            return jsonify({"error": "Ruta no encontrada"}), 404
        return jsonify(route), 200
    except Exception as exc:
        logger.error(
            "Error en GET /operation/routes/%s: %s", id_ruta, repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@route_bp.route("/operation/routes", methods=["POST"])
@jwt_required
def save_route():
    try:
        body = request.get_json(silent=True)
        id_empresa, id_usuario, error = _resolve_context(body)
        if error:
            return error

        payload, validation_error = validate_payload(CreateRouteSchema(), body)
        if validation_error:
            return validation_error

        # Validar clave única dentro de la empresa
        if is_clave_taken(payload.get("clave", ""), id_empresa):
            return (
                jsonify(
                    {
                        "error": "Ya existe una ruta con esa clave en tu empresa",
                        "code": "CLAVE_TAKEN",
                    }
                ),
                409,
            )

        id_ruta = create_route(payload, id_empresa, int(id_usuario))
        return (
            jsonify({"message": "Ruta creada correctamente", "id_ruta": id_ruta}),
            201,
        )
    except Exception as exc:
        logger.error("Error en POST /operation/routes: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@route_bp.route("/operation/routes/<int:id_ruta>", methods=["PUT"])
@jwt_required
def edit_route(id_ruta: int):
    try:
        body = request.get_json(silent=True)
        id_empresa, id_usuario, error = _resolve_context(body)
        if error:
            return error

        payload, validation_error = validate_payload(UpdateRouteSchema(), body)
        if validation_error:
            return validation_error

        if payload.get("clave") and is_clave_taken(
            payload["clave"], id_empresa, exclude_id=id_ruta
        ):
            return (
                jsonify(
                    {
                        "error": "Ya existe una ruta con esa clave en tu empresa",
                        "code": "CLAVE_TAKEN",
                    }
                ),
                409,
            )

        ok = update_route(id_ruta, payload, id_empresa, int(id_usuario))
        if not ok:
            return jsonify({"error": "Ruta no encontrada"}), 404
        return jsonify({"message": "Ruta actualizada correctamente"}), 200
    except Exception as exc:
        logger.error(
            "Error en PUT /operation/routes/%s: %s", id_ruta, repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@route_bp.route("/operation/routes/<int:id_ruta>", methods=["DELETE"])
@jwt_required
def remove_route(id_ruta: int):
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error
        ok = delete_route(id_ruta, id_empresa)
        if not ok:
            return jsonify({"error": "Ruta no encontrada"}), 404
        return jsonify({"message": "Ruta eliminada correctamente"}), 200
    except Exception as exc:
        logger.error(
            "Error en DELETE /operation/routes/%s: %s",
            id_ruta,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500
