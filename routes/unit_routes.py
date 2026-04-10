from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        id_empresa = request.user.get("id_empresa")
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        units = get_units(id_empresa, search if search else None)
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@units_bp.route("/units", methods=["POST"])
@jwt_required
def create_new_unit():
    try:
        user_payload = request.user
        id_usuario = user_payload.get("sub")
        id_empresa = user_payload.get("id_empresa")
        if not id_usuario or not id_empresa:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        data = request.get_json()
        # validaciones...
        result = create_unit(data, id_usuario, id_empresa)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500
