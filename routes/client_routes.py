import logging
from flask import Blueprint, jsonify, request
from services.client_service import (
    get_clients,
    get_client_by_id,
    create_client,
    update_client,
    delete_client,
)
from validators.client_validators import CreateClientSchema, UpdateClientSchema
from utils.auth_guard import jwt_required, validate_empresa_access
from utils.validation import validate_payload

client_bp = Blueprint("clients", __name__)

logger = logging.getLogger(__name__)


# Helper: resolver contexto de empresa y usuario desde el JWT
def _resolve_context(body=None):
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


# GET /catalogs/clients?search=texto  - Lista clientes con filtro opcional
@client_bp.route("/catalogs/clients", methods=["GET"])
@jwt_required
def list_clients():
    """
    Lista los clientes de la empresa autenticada.

    Query params opcionales:
      - search : Texto para filtrar por nombre, contacto, teléfono, email, etc.
    """
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error

        search = request.args.get("search", "").strip() or None
        clients = get_clients(id_empresa, search)
        return jsonify(clients), 200

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# GET /catalogs/clients/<id> - Detalle completo de un cliente
@client_bp.route("/catalogs/clients/<int:id_cliente>", methods=["GET"])
@jwt_required
def get_client(id_cliente):
    """
    Retorna el detalle completo de un cliente, incluyendo tokens y configuración.
    """
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error

        client = get_client_by_id(id_cliente, id_empresa)
        if not client:
            return jsonify({"error": "Cliente no encontrado"}), 404

        return jsonify(client), 200

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# POST /catalogs/clients - Crear un nuevo cliente
@client_bp.route("/catalogs/clients", methods=["POST"])
@jwt_required
def save_client():
    """
    Crea un nuevo cliente.

    El token y token_dashboard se generan automáticamente en el service —
    el cliente no necesita (ni puede) enviarlos.

    Responde 409 si la clave ya está en uso en la empresa.
    """
    try:
        body = request.get_json(silent=True)
        id_empresa, id_usuario, error = _resolve_context(body)
        if error:
            return error
        payload, validation_error = validate_payload(CreateClientSchema(), body)
        if validation_error:
            return validation_error

        client = create_client(payload, id_empresa, id_usuario)
        return jsonify(client), 201

    except ValueError as e:
        # ValueError viene de is_clave_taken — clave duplicada
        return jsonify({"error": str(e), "code": "CLAVE_TAKEN"}), 409

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# PUT /catalogs/clients/<id> - Actualizar un cliente existente
@client_bp.route("/catalogs/clients/<int:id_cliente>", methods=["PUT"])
@jwt_required
def edit_client(id_cliente):
    """
    Actualiza los datos de un cliente existente.

    Solo modifica los campos que vengan en el body — los demás se quedan igual.
    Responde 409 si la nueva clave ya está en uso por otro cliente.
    """
    try:
        id_empresa, id_usuario, error = _resolve_context()
        if error:
            return error

        body = request.get_json(silent=True)
        payload, validation_error = validate_payload(UpdateClientSchema(), body)
        if validation_error:
            return validation_error

        updated = update_client(id_cliente, payload, id_empresa, id_usuario)
        if not updated:
            return jsonify({"error": "Cliente no encontrado"}), 404

        return jsonify(updated), 200

    except ValueError as e:
        return jsonify({"error": str(e), "code": "CLAVE_TAKEN"}), 409

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# DELETE /catalogs/clients/<id> - Eliminar un cliente
@client_bp.route("/catalogs/clients/<int:id_cliente>", methods=["DELETE"])
@jwt_required
def remove_client(id_cliente):
    """
    Elimina un cliente de forma permanente.

    Solo el propietario de la empresa puede eliminar — el id_empresa del JWT
    garantiza que nadie puede borrar clientes de otra empresa.
    """
    try:
        id_empresa, _, error = _resolve_context()
        if error:
            return error

        deleted = delete_client(id_cliente, id_empresa)
        if not deleted:
            return jsonify({"error": "Cliente no encontrado"}), 404

        return jsonify({"message": "Cliente eliminado correctamente"}), 200

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
