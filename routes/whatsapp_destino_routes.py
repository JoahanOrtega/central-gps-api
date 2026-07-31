import logging

from flask import Blueprint, jsonify, request

from services.whatsapp_destino_service import (
    listar_destinos,
    crear_destino,
    editar_destino,
    cambiar_status_destino,
    eliminar_destino,
    TIPOS_VALIDOS,
)
from utils.auth_guard import jwt_required, sudo_erp_required

logger = logging.getLogger(__name__)

whatsapp_destino_bp = Blueprint("whatsapp_destinos", __name__)


@whatsapp_destino_bp.route("/destinos", methods=["GET"])
@jwt_required
@sudo_erp_required
def get_destinos():
    id_empresa = request.args.get("id_empresa", type=int)
    tipo = request.args.get("tipo")
    if tipo is not None and tipo not in TIPOS_VALIDOS:
        return jsonify({"error": f"tipo inválido: {tipo}"}), 400
    return jsonify(listar_destinos(id_empresa=id_empresa, tipo=tipo)), 200


@whatsapp_destino_bp.route("/destinos", methods=["POST"])
@jwt_required
@sudo_erp_required
def post_destino():
    body = request.get_json(silent=True) or {}
    id_empresa = body.get("id_empresa")
    tipo = body.get("tipo")
    nombre = (body.get("nombre") or "").strip()

    if not id_empresa or tipo not in TIPOS_VALIDOS or not nombre:
        return (
            jsonify({"error": "id_empresa, tipo válido y nombre son obligatorios"}),
            400,
        )

    try:
        destino = crear_destino(
            id_empresa=id_empresa,
            tipo=tipo,
            nombre=nombre,
            telefono=body.get("telefono"),
            participantes=body.get("participantes"),
        )
        return jsonify(destino), 201
    except ValueError as exc:
        # Incluye el caso "no se pudo crear el grupo en WhatsApp".
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error("Error creando destino: %s", repr(exc))
        return jsonify({"error": "No se pudo crear el destino"}), 500


@whatsapp_destino_bp.route("/destinos/<int:id_destino>", methods=["PUT"])
@jwt_required
@sudo_erp_required
def put_destino(id_destino):
    body = request.get_json(silent=True) or {}
    id_empresa = body.get("id_empresa")
    nombre = (body.get("nombre") or "").strip()
    if not id_empresa or not nombre:
        return jsonify({"error": "id_empresa y nombre son obligatorios"}), 400

    try:
        destino = editar_destino(
            id_destino,
            id_empresa,
            nombre,
            telefono=body.get("telefono"),
        )
        if destino is None:
            return jsonify({"error": "Destino no encontrado"}), 404
        return jsonify(destino), 200
    except Exception as exc:
        logger.error("Error editando destino %s: %s", id_destino, repr(exc))
        return jsonify({"error": "No se pudo editar el destino"}), 500


@whatsapp_destino_bp.route("/destinos/<int:id_destino>", methods=["PATCH"])
@jwt_required
@sudo_erp_required
def patch_destino(id_destino):
    body = request.get_json(silent=True) or {}
    id_empresa = body.get("id_empresa")
    status = body.get("status")
    if id_empresa is None or status not in (0, 1):
        return jsonify({"error": "id_empresa y status (0|1) son obligatorios"}), 400

    try:
        if not cambiar_status_destino(id_destino, id_empresa, status):
            return jsonify({"error": "Destino no encontrado"}), 404
        return jsonify({"id_destino_whatsapp": id_destino, "status": status}), 200
    except Exception as exc:
        logger.error("Error cambiando status %s: %s", id_destino, repr(exc))
        return jsonify({"error": "No se pudo actualizar el destino"}), 500


@whatsapp_destino_bp.route("/destinos/<int:id_destino>", methods=["DELETE"])
@jwt_required
@sudo_erp_required
def delete_destino(id_destino):
    body = request.get_json(silent=True) or {}
    id_empresa = body.get("id_empresa")
    if id_empresa is None:
        return jsonify({"error": "id_empresa es obligatorio"}), 400

    try:
        if not eliminar_destino(id_destino, id_empresa):
            return jsonify({"error": "Destino no encontrado"}), 404
        return jsonify({"eliminado": True, "id_destino_whatsapp": id_destino}), 200
    except Exception as exc:
        logger.error("Error eliminando destino %s: %s", id_destino, repr(exc))
        return jsonify({"error": "No se pudo eliminar el destino"}), 500
