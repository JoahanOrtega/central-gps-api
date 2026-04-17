import logging
from flask import Blueprint, jsonify, request, current_app
from services.auth_service import authenticate_user
from services.company_service import get_user_companies
from utils.auth_guard import jwt_required
from utils.jwt_handler import generate_jwt
from db.connection import get_db_connection

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Autentica al usuario y retorna un JWT con su rol y empresa activa.

    Rate limiting:
      - 10 intentos por minuto por IP
      - 50 intentos por hora por IP
      Estos límites permiten uso legítimo normal mientras bloquean
      ataques de fuerza bruta y credential stuffing.

    Seguridad:
      - El mensaje y el status code son idénticos para usuario inexistente
        y contraseña incorrecta — previene user enumeration.
    """
    # Aplicar rate limiting específico para login — más estricto que el global.
    # get_remote_address() usa el header X-Forwarded-For si está disponible,
    # lo que funciona correctamente detrás de un proxy reverso (nginx).
    limiter = current_app.extensions.get("limiter")
    if limiter:
        limiter.limit("10 per minute; 50 per hour")(lambda: None)()

    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400

        username = data.get("username", "").strip()
        password = data.get("password", "")

        if not username or not password:
            return jsonify({"error": "Faltan credenciales"}), 400

        user, token, error = authenticate_user(username, password)

        if error:
            # Siempre 401, nunca 404 — no revelar si el usuario existe o no.
            # auth_service ya retorna "Credenciales inválidas" en ambos casos.
            return jsonify({"error": error}), 401

        return jsonify({"message": "Login correcto", "token": token, "user": user}), 200

    except Exception as exc:
        logger.error("Error en POST /auth/login: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/switch-company", methods=["POST"])
@jwt_required
def switch_company():
    """
    Cambia la empresa activa del usuario generando un nuevo JWT.

    El nuevo token conserva todos los campos del usuario original
    (rol, nombre, perfil) pero actualiza id_empresa y es_admin_empresa
    según la relación del usuario con la empresa destino.

    Validaciones:
      - El usuario debe tener acceso a la empresa solicitada.
      - La empresa debe estar activa.
      - es_admin_empresa se recalcula por empresa — un usuario puede ser
        admin en una empresa pero no en otra.
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400

        new_company_id = data.get("id_empresa")
        if not new_company_id:
            return jsonify({"error": "El campo id_empresa es requerido"}), 400

        # 1. Leer datos del usuario desde el token actual
        user_payload = request.user
        user_id = user_payload.get("sub")

        # 2. Verificar que el usuario tenga acceso a la empresa solicitada
        companies = get_user_companies(user_id)
        target = next(
            (c for c in companies if c["id_empresa"] == new_company_id),
            None,
        )

        if not target:
            return jsonify({"error": "No tienes acceso a esta empresa"}), 403

        # 3. Recalcular es_admin_empresa para la empresa destino.
        #    No reutilizar el valor del token anterior — el rol de admin
        #    puede diferir entre empresas del mismo usuario.
        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT es_admin_empresa
                FROM r_empresa_usuarios
                WHERE id_usuario = %s
                  AND id_empresa = %s
                  AND status     = 1
                """,
                (user_id, new_company_id),
            )
            rel_row = cursor.fetchone()
            es_admin_empresa = bool(rel_row[0]) if rel_row else False
        finally:
            cursor.close()
            connection.close()

        # 4. Generar nuevo JWT con empresa actualizada
        new_user = {
            "id": user_id,
            "username": user_payload.get("username"),
            "nombre": user_payload.get("nombre"),
            "perfil": user_payload.get("perfil"),  # Legacy — compatibilidad PHP
            "rol": user_payload.get("rol"),
            "id_empresa": new_company_id,
            "nombre_empresa": target["nombre"],
            "es_admin_empresa": es_admin_empresa,
        }
        new_token = generate_jwt(new_user)

        return (
            jsonify(
                {
                    "token": new_token,
                    "id_empresa": new_company_id,
                    "nombre_empresa": target["nombre"],
                }
            ),
            200,
        )

    except Exception as exc:
        logger.error("Error en POST /auth/switch-company: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
