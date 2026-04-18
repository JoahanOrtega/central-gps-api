import logging
from flask import Blueprint, jsonify, request
from services.poi_service import (
    get_pois,
    create_poi,
    get_poi_groups,
    create_poi_group,
    get_clients,
)
from utils.auth_guard import jwt_required, validate_empresa_access
from utils.validation import validate_payload
from validators import CreatePoiSchema, CreatePoiGroupSchema

poi_bp = Blueprint("poi", __name__)

logger = logging.getLogger(__name__)


@poi_bp.route("/pois", methods=["GET"])
@jwt_required
def list_pois():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_pois(id_empresa, search if search else None)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/pois", methods=["POST"])
@jwt_required
def save_poi():
    """
    Crea un nuevo POI.

    Validación (marshmallow):
      - nombre: obligatorio, max 100 chars
      - tipo_poi: 1 (marcador), 2 (círculo) o 3 (polígono)
      - lat/lng: obligatorios para tipo 1 y 2, rango [-90,90] y [-180,180]
      - radio: obligatorio para tipo 2, > 0
      - polygon_path: obligatorio para tipo 3
      - colores: formato hex válido (#RRGGBB)

    Respuesta en error:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    validation_error = validate_payload(CreatePoiSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_poi(data, id_empresa, id_usuario)
        return jsonify({"message": "POI creado correctamente", "poi": result}), 201

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/poi-groups", methods=["GET"])
@jwt_required
def list_poi_groups():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_poi_groups(id_empresa, search if search else None)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/poi-groups", methods=["POST"])
@jwt_required
def save_poi_group():
    """
    Crea un nuevo grupo de POIs.

    Validación (marshmallow):
      - nombre: obligatorio, max 100 chars
      - is_default: booleano (default false)
      - id_cliente: entero opcional

    Respuesta en error:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    validation_error = validate_payload(CreatePoiGroupSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_poi_group(data, id_empresa, id_usuario)
        return jsonify({"message": "Grupo creado correctamente", "group": result}), 201

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/clients", methods=["GET"])
@jwt_required
def list_clients():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        return jsonify(get_clients(id_empresa)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
