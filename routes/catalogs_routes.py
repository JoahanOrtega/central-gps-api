from flask import Blueprint, jsonify, request
from services.catalog_service import (
    get_operators,
    get_unit_groups,
    get_avl_models,
    get_protocols,
)
from utils.auth_guard import jwt_required

catalogs_bp = Blueprint("catalogs", __name__)


@catalogs_bp.route("/catalogs/operators", methods=["GET"])
@jwt_required
def list_operators():
    try:
        search = request.args.get("search", "").strip()
        operators = get_operators(search if search else None)
        return jsonify(operators), 200
    except Exception as error:
        print("ERROR EN /catalogs/operators:", repr(error))
        return jsonify({"error": "Error interno del servidor"}), 500


@catalogs_bp.route("/catalogs/unit-groups", methods=["GET"])
@jwt_required
def list_unit_groups():
    try:
        search = request.args.get("search", "").strip()
        groups = get_unit_groups(search if search else None)
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


@catalogs_bp.route("/catalogs/protocols", methods=["GET"])
@jwt_required
def list_protocols():
    try:
        tipo = request.args.get("tipo", "").strip()
        if tipo not in ("in", "out", "rs232"):
            return jsonify({"error": "El parámetro 'tipo' debe ser 'in', 'out' o 'rs232'"}), 400
        protocols = get_protocols(tipo)
        return jsonify(protocols), 200
    except Exception as error:
        print("ERROR EN /catalogs/protocols:", repr(error))
        return jsonify({"error": "Error interno del servidor"}), 500