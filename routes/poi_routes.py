from flask import Blueprint, jsonify, request
from services.poi_service import (
    get_pois,
    create_poi,
    get_poi_groups,
    create_poi_group,
    get_clients,
)
from utils.auth_guard import jwt_required

poi_bp = Blueprint("poi", __name__)


@poi_bp.route("/pois", methods=["GET"])
@jwt_required
def list_pois():
    try:
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_pois(id_empresa, search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/pois", methods=["POST"])
@jwt_required
def save_poi():
    try:
        id_empresa = request.user.get("id_empresa")
        id_usuario = request.user.get("sub")
        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        data = request.get_json()
        # validaciones...
        result = create_poi(data, id_empresa, id_usuario)
        return jsonify({"message": "POI creado correctamente", "poi": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["GET"])
@jwt_required
def list_poi_groups():
    try:
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_poi_groups(id_empresa, search if search else None)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@poi_bp.route("/poi-groups", methods=["POST"])
@jwt_required
def save_poi_group():
    try:
        id_empresa = request.user.get("id_empresa")
        id_usuario = request.user.get("sub")
        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        data = request.get_json()
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
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        return jsonify(get_clients(id_empresa)), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500
