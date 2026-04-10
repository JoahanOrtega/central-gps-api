from flask import Blueprint, jsonify, request
from services.auth_service import authenticate_user
from services.company_service import get_user_companies
from utils.auth_guard import jwt_required
from utils.jwt_handler import generate_jwt

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400
        username = data.get("username")
        password = data.get("password")
        if not username or not password:
            return jsonify({"error": "Faltan credenciales"}), 400

        user, token, error = authenticate_user(username, password)
        if error:
            return jsonify({"error": error}), (
                401 if error != "Usuario no encontrado" else 404
            )

        return jsonify({"message": "Login correcto", "token": token, "user": user}), 200
    except Exception as error:
        print("ERROR EN /login:", repr(error))  # <-- ESTO ES CLAVE
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/switch-company", methods=["POST"])
@jwt_required
def switch_company():
    try:
        data = request.get_json()
        new_company_id = data.get("id_empresa")
        if not new_company_id:
            return jsonify({"error": "id_empresa requerido"}), 400

        # Obtener usuario del token actual
        user_payload = request.user
        user_id = user_payload.get("sub")
        username = user_payload.get("username")
        perfil = user_payload.get("perfil")

        # Verificar que el usuario tenga acceso a la empresa solicitada
        companies = get_user_companies(user_id)
        target_company = next(
            (c for c in companies if c["id_empresa"] == new_company_id), None
        )

        if not target_company:
            return jsonify({"error": "No tienes acceso a esta empresa"}), 403

        # Generar nuevo token con id_empresa actualizado
        new_user = {
            "id": user_id,
            "username": username,
            "perfil": perfil,
            "id_empresa": new_company_id,
        }
        new_token = generate_jwt(new_user)

        return (
            jsonify(
                {
                    "token": new_token,
                    "id_empresa": new_company_id,
                    "nombre_empresa": target_company["nombre"],
                }
            ),
            200,
        )

    except Exception as e:
        print("ERROR EN /switch-company:", repr(e))
        return jsonify({"error": "Error interno del servidor"}), 500
