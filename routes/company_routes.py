from flask import Blueprint, jsonify, request
from services.company_service import get_user_companies
from utils.auth_guard import jwt_required

company_bp = Blueprint("company", __name__)


@company_bp.route("/companies", methods=["GET"])
@jwt_required
def list_companies():
    try:
        # Obtener id_usuario del token (request.user['sub'])

        user_id = request.user.get("sub")
        companies = get_user_companies(user_id)
        return jsonify(companies), 200
    except Exception as e:
        print("ERROR EN /companies:", repr(e))
        return jsonify({"error": "Error interno del servidor"}), 500
