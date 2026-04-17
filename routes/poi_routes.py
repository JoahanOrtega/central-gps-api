from flask import Blueprint, jsonify, request
from services.poi_service import (
    get_pois,
    create_poi,
    get_poi_groups,
    create_poi_group,
    get_clients,
)
from utils.auth_guard import jwt_required, validate_empresa_access

poi_bp = Blueprint("poi", __name__)


@poi_bp.route("/pois", methods=["GET"])
@jwt_required
def list_pois():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403
        search = request.args.get("search", "").strip()
        return jsonify(get_pois(id_empresa, search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/pois", methods=["POST"])
@jwt_required
def save_poi():
    try:
        data = request.get_json()
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_poi(data, id_empresa, id_usuario)
        return jsonify({"message": "POI creado correctamente", "poi": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["GET"])
@jwt_required
def list_poi_groups():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        search = request.args.get("search", "").strip()
        return jsonify(get_poi_groups(id_empresa, search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["POST"])
@jwt_required
def save_poi_group():
    try:
        data = request.get_json()
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        if not data.get("nombre"):
            return jsonify({"error": "El nombre es requerido"}), 400

        result = create_poi_group(data, id_empresa, id_usuario)
        return jsonify({"message": "Grupo creado correctamente", "group": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/clients", methods=["GET"])
@jwt_required
def list_clients():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        return jsonify(get_clients(id_empresa)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500
