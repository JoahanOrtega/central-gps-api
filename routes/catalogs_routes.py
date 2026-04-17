from flask import Blueprint, jsonify, request
import logging
from services.catalog_service import get_operators, get_unit_groups, get_avl_models
from utils.auth_guard import jwt_required

catalogs_bp = Blueprint("catalogs", __name__)

logger = logging.getLogger(__name__)


@catalogs_bp.route("/catalogs/operators", methods=["GET"])
@jwt_required
def list_operators():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        operators = get_operators(id_empresa, search if search else None)
        return jsonify(operators), 200
    except Exception as error:
        logger.error("Error en /catalogs/operators: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@catalogs_bp.route("/catalogs/unit-groups", methods=["GET"])
@jwt_required
def list_unit_groups():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        groups = get_unit_groups(id_empresa, search if search else None)
        return jsonify(groups), 200
    except Exception as error:
        logger.error("Error en /catalogs/unit-groups: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@catalogs_bp.route("/catalogs/avl-models", methods=["GET"])
@jwt_required
def list_avl_models():
    try:
        models = get_avl_models()
        return jsonify(models), 200
    except Exception as error:
        logger.error("Error en /catalogs/avl-models: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
