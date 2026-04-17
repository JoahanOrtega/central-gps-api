from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        # request.user lo inyecta tu decorador jwt_required
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )

        if not id_empresa:
            return {"error": "Empresa no definida"}, 400

        search = request.args.get("search", "").strip()
        units = get_units(id_empresa, search if search else None)
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500


@units_bp.route("/units", methods=["POST"])
@jwt_required
def create_new_unit():
    try:
        id_usuario = request.user.get("sub")
        data = request.get_json()

        # Leer id_empresa del body primero (para sudo_erp),
        # luego del JWT como fallback (para usuarios normales)
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")

        if not id_usuario or not id_empresa:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        result = create_unit(data, id_usuario, id_empresa)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201
    except Exception as error:
        return jsonify({"error": str(error)}), 500
