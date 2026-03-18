from flask import Blueprint, jsonify, request
from services.auth_service import authenticate_user

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400

        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify({"error": "Faltan credenciales"}), 400

        user, error = authenticate_user(username, password)

        if error == "Usuario no encontrado":
            return jsonify({"error": error}), 404

        if error:
            return jsonify({"error": error}), 401

        return jsonify({
            "message": "Login correcto",
            "user": user
        }), 200

    except Exception as error:
        return jsonify({"error": str(error)}), 500