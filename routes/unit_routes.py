from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required, validate_empresa_access

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        search = request.args.get("search", "").strip()
        units = get_units(id_empresa, search if search else None)
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@units_bp.route("/units", methods=["POST"])
@jwt_required
def create_new_unit():
    try:
        data = request.get_json()
        id_usuario = request.user.get("sub")
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")

        if not id_usuario or not id_empresa:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_unit(data, id_usuario, id_empresa)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500
