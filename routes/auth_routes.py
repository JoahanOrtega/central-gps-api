from flask import Blueprint, jsonify, request
from services.auth_service import authenticate_user
from utils.auth_guard import jwt_required

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
# @jwt_required
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

        if error == "Usuario no encontrado":
            return jsonify({"error": error}), 404

        if error:
            return jsonify({"error": error}), 401

        return jsonify({"message": "Login correcto", "token": token, "user": user}), 200

    except Exception as error:
        print("ERROR EN /login:", error)
        return jsonify({"error": "Error interno del servidor"}), 500
