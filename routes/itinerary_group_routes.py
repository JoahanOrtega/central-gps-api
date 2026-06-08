"""
itinerary_group_routes.py — Endpoints de Grupos y Roles de Itinerarios.

Grupos:
  GET    /operation/itinerary-groups          → listado
  GET    /operation/itinerary-groups/<id>     → detalle
  POST   /operation/itinerary-groups          → crear
  PUT    /operation/itinerary-groups/<id>     → actualizar
  DELETE /operation/itinerary-groups/<id>     → eliminar

Roles:
  GET    /operation/itinerary-roles           → listado
  GET    /operation/itinerary-roles/<id>      → detalle con secuencia de días
  POST   /operation/itinerary-roles           → crear
  PUT    /operation/itinerary-roles/<id>      → actualizar
  DELETE /operation/itinerary-roles/<id>      → eliminar

Permisos requeridos:
  itinerarios.grupos  → todos los endpoints de grupos y roles
"""

import logging
from flask import Blueprint, jsonify, request

from services.itinerary_group_service import (
    get_groups,
    get_group_by_id,
    create_group,
    update_group,
    delete_group,
    get_roles,
    get_role_by_id,
    create_role,
    update_role,
    delete_role,
)
from validators.itinerary_group_validators import (
    CreateGroupSchema,
    UpdateGroupSchema,
    CreateRoleSchema,
    UpdateRoleSchema,
)
from utils.auth_guard import jwt_required, permiso_required
from utils.validation import validate_payload

itinerary_group_bp = Blueprint("itinerary_groups", __name__)
logger = logging.getLogger(__name__)


# ── Helper empresa ─────────────────────────────────────────────────────────────


def _get_empresa() -> int | None:
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


def _empresa_or_400():
    e = _get_empresa()
    if not e:
        return None, (jsonify({"error": "Empresa no definida"}), 400)
    return e, None


# ══════════════════════════════════════════════════════════════════════════════
# GRUPOS
# ══════════════════════════════════════════════════════════════════════════════


@itinerary_group_bp.route("/operation/itinerary-groups", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.grupos")
def list_groups():
    """Listado de grupos con conteo de itinerarios."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        search = request.args.get("search", "").strip()
        return jsonify(get_groups(empresa, search)), 200
    except Exception:
        logger.exception("GET /operation/itinerary-groups")
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-groups/<int:id_grupo>", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.grupos")
def get_group(id_grupo: int):
    """Detalle de un grupo con ids de itinerarios miembros."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        result = get_group_by_id(id_grupo, empresa)
        if not result:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify(result), 200
    except Exception:
        logger.exception("GET /operation/itinerary-groups/%d", id_grupo)
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-groups", methods=["POST"])
@jwt_required
@permiso_required("itinerarios.grupos")
def create_group_endpoint():
    """Crea un grupo, opcionalmente con itinerarios iniciales."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        data, verr = validate_payload(
            CreateGroupSchema(), request.get_json(silent=True)
        )
        if verr:
            return verr
        id_usuario = int(request.user.get("sub"))
        id_grupo = create_group(data, empresa, id_usuario)
        return (
            jsonify(
                {
                    "id_grupo_itinerarios": id_grupo,
                    "message": "Grupo creado correctamente",
                }
            ),
            201,
        )
    except Exception:
        logger.exception("POST /operation/itinerary-groups")
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-groups/<int:id_grupo>", methods=["PUT"])
@jwt_required
@permiso_required("itinerarios.grupos")
def update_group_endpoint(id_grupo: int):
    """Actualiza un grupo y reemplaza sus miembros si se envían."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        data, verr = validate_payload(
            UpdateGroupSchema(), request.get_json(silent=True)
        )
        if verr:
            return verr
        id_usuario = int(request.user.get("sub"))
        updated = update_group(id_grupo, data, empresa, id_usuario)
        if not updated:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify({"message": "Grupo actualizado correctamente"}), 200
    except Exception:
        logger.exception("PUT /operation/itinerary-groups/%d", id_grupo)
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route(
    "/operation/itinerary-groups/<int:id_grupo>", methods=["DELETE"]
)
@jwt_required
@permiso_required("itinerarios.grupos")
def delete_group_endpoint(id_grupo: int):
    """Soft-delete de un grupo."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        deleted = delete_group(id_grupo, empresa)
        if not deleted:
            return jsonify({"error": "Grupo no encontrado"}), 404
        return jsonify({"message": "Grupo eliminado correctamente"}), 200
    except Exception:
        logger.exception("DELETE /operation/itinerary-groups/%d", id_grupo)
        return jsonify({"error": "Error interno del servidor"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ROLES
# ══════════════════════════════════════════════════════════════════════════════


@itinerary_group_bp.route("/operation/itinerary-roles", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.grupos")
def list_roles():
    """Listado de roles con conteo de itinerarios y asignaciones activas."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        search = request.args.get("search", "").strip()
        return jsonify(get_roles(empresa, search)), 200
    except Exception:
        logger.exception("GET /operation/itinerary-roles")
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-roles/<int:id_rol>", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.grupos")
def get_role(id_rol: int):
    """Detalle de un rol con su secuencia de días completa."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        result = get_role_by_id(id_rol, empresa)
        if not result:
            return jsonify({"error": "Rol no encontrado"}), 404
        return jsonify(result), 200
    except Exception:
        logger.exception("GET /operation/itinerary-roles/%d", id_rol)
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-roles", methods=["POST"])
@jwt_required
@permiso_required("itinerarios.grupos")
def create_role_endpoint():
    """Crea un rol con su secuencia de días."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        data, verr = validate_payload(CreateRoleSchema(), request.get_json(silent=True))
        if verr:
            return verr
        id_usuario = int(request.user.get("sub"))
        id_rol = create_role(data, empresa, id_usuario)
        return (
            jsonify(
                {"id_rol_itinerarios": id_rol, "message": "Rol creado correctamente"}
            ),
            201,
        )
    except Exception:
        logger.exception("POST /operation/itinerary-roles")
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-roles/<int:id_rol>", methods=["PUT"])
@jwt_required
@permiso_required("itinerarios.grupos")
def update_role_endpoint(id_rol: int):
    """Actualiza un rol y reemplaza su secuencia si se envía."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        data, verr = validate_payload(UpdateRoleSchema(), request.get_json(silent=True))
        if verr:
            return verr
        id_usuario = int(request.user.get("sub"))
        updated = update_role(id_rol, data, empresa, id_usuario)
        if not updated:
            return jsonify({"error": "Rol no encontrado"}), 404
        return jsonify({"message": "Rol actualizado correctamente"}), 200
    except Exception:
        logger.exception("PUT /operation/itinerary-roles/%d", id_rol)
        return jsonify({"error": "Error interno del servidor"}), 500


@itinerary_group_bp.route("/operation/itinerary-roles/<int:id_rol>", methods=["DELETE"])
@jwt_required
@permiso_required("itinerarios.grupos")
def delete_role_endpoint(id_rol: int):
    """Soft-delete de un rol."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err
        deleted = delete_role(id_rol, empresa)
        if not deleted:
            return jsonify({"error": "Rol no encontrado"}), 404
        return jsonify({"message": "Rol eliminado correctamente"}), 200
    except Exception:
        logger.exception("DELETE /operation/itinerary-roles/%d", id_rol)
        return jsonify({"error": "Error interno del servidor"}), 500
