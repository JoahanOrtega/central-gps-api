from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        search = request.args.get("search", "").strip()
        units = get_units(search=search if search else None)
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@units_bp.route("/units", methods=["POST"])
@jwt_required
def create_new_unit():
    try:
        # Obtener id_usuario del payload del token
        user_payload = getattr(request, "user", {})
        id_usuario = user_payload.get("sub")  # porque en jwt_handler.py usaste 'sub'
        if not id_usuario:
            return jsonify({"error": "No se pudo identificar al usuario"}), 401

        data = request.get_json()
        # validaciones...

        result = create_unit(data, id_usuario)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500
