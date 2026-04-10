from flask import Blueprint, jsonify, request
from services.catalog_service import get_operators, get_unit_groups, get_avl_models
from utils.auth_guard import jwt_required

catalogs_bp = Blueprint("catalogs", __name__)


@catalogs_bp.route("/catalogs/operators", methods=["GET"])
@jwt_required
def list_operators():
    try:
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        operators = get_operators(id_empresa, search if search else None)
        return jsonify(operators), 200
    except Exception as error:
        print("ERROR EN /catalogs/operators:", repr(error))
        return jsonify({"error": "Error interno del servidor"}), 500


@catalogs_bp.route("/catalogs/unit-groups", methods=["GET"])
@jwt_required
def list_unit_groups():
    try:
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        groups = get_unit_groups(id_empresa, search if search else None)
        return jsonify(groups), 200
    except Exception as error:
        print("ERROR EN /catalogs/unit-groups:", repr(error))
        return jsonify({"error": "Error interno del servidor"}), 500


@catalogs_bp.route("/catalogs/avl-models", methods=["GET"])
@jwt_required
def list_avl_models():
    try:
        models = get_avl_models()
        return jsonify(models), 200
    except Exception as error:
        print("ERROR EN /catalogs/avl-models:", repr(error))
        return jsonify({"error": "Error interno del servidor"}), 500
