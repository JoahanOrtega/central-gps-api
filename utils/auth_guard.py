from functools import wraps
from flask import request, jsonify
from utils.jwt_handler import decode_jwt


def jwt_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token requerido"}), 401
        try:
            payload = decode_jwt(token)
            request.user = payload  # aquí queda disponible en la ruta
        except Exception as e:
            return jsonify({"error": "Token inválido"}), 401
        return f(*args, **kwargs)

    return decorated
