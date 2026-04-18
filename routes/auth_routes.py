import logging
from flask import Blueprint, jsonify, request
from services.auth_service import authenticate_user
from services.company_service import get_user_companies
from services.refresh_token_service import (
    save_refresh_token,
    validate_and_rotate_refresh_token,
    revoke_refresh_token,
)
from utils.auth_guard import jwt_required
from utils.jwt_handler import (
    generate_access_token,
    generate_refresh_token,
    generate_jwt,
)
from utils.limiter import limiter
from utils.validation import validate_payload
from validators import LoginSchema, SwitchCompanySchema
from db.connection import get_db_connection, release_db_connection
from config import Config

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

_COOKIE_NAME = "refresh_token"
_COOKIE_MAX_AGE = Config.REFRESH_TOKEN_EXPIRATION_DAYS * 24 * 60 * 60
_COOKIE_SECURE = not Config.FRONTEND_URL.startswith("http://localhost")


def _set_refresh_cookie(response, refresh_token_crudo: str):
    response.set_cookie(
        _COOKIE_NAME,
        refresh_token_crudo,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="Lax",
        path="/auth",
    )
    return response


def _clear_refresh_cookie(response):
    response.set_cookie(
        _COOKIE_NAME,
        "",
        max_age=0,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite="Lax",
        path="/auth",
    )
    return response


@auth_bp.route("/login", methods=["POST"])
@limiter.limit("10 per minute; 50 per hour")
def login():
    """
    Autentica al usuario y retorna access token + cookie de refresh token.

    Validación:
      - username: string no vacío, max 100 chars
      - password: string no vacío, max 128 chars (límite bcrypt)

    Seguridad:
      - Rate limiting: 10 intentos/min, 50/hora por IP
      - Mismo mensaje de error para usuario inexistente y contraseña incorrecta
    """
    data = request.get_json(silent=True)

    # Validar payload con marshmallow antes de tocar la BD
    validation_error = validate_payload(LoginSchema(), data)
    if validation_error:
        return validation_error

    try:
        user, access_token, error = authenticate_user(
            data["username"].strip(),
            data["password"],
        )

        if error:
            return jsonify({"error": error}), 401

        refresh_token_crudo, _ = generate_refresh_token()
        save_refresh_token(
            id_usuario=user["id"],
            token_crudo=refresh_token_crudo,
            ip_origen=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        response = jsonify(
            {
                "message": "Login correcto",
                "token": access_token,
                "user": user,
            }
        )
        return _set_refresh_cookie(response, refresh_token_crudo)

    except Exception as exc:
        logger.error("Error en POST /auth/login: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    """Renueva el access token usando el refresh token de la cookie HttpOnly."""
    try:
        refresh_token_crudo = request.cookies.get(_COOKIE_NAME)

        if not refresh_token_crudo:
            response = jsonify({"error": "No hay sesión activa"})
            return _clear_refresh_cookie(response), 401

        result = validate_and_rotate_refresh_token(refresh_token_crudo)

        if not result:
            response = jsonify({"error": "Sesión expirada. Inicia sesión nuevamente."})
            return _clear_refresh_cookie(response), 401

        id_usuario = result["id_usuario"]

        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT u.id, u.usuario, u.nombre, u.perfil, r.clave AS rol
                FROM t_usuarios u
                LEFT JOIN t_roles r ON r.id_rol = u.id_rol
                WHERE u.id = %s AND u.status = 1
                """,
                (id_usuario,),
            )
            user_row = cursor.fetchone()

            if not user_row:
                response = jsonify({"error": "Usuario no encontrado o inactivo"})
                return _clear_refresh_cookie(response), 401

            user_id, username, nombre, perfil, rol = user_row
            id_empresa = None
            nombre_empresa = None
            es_admin_empresa = False

            if rol != "sudo_erp":
                cursor.execute(
                    """
                    SELECT e.id_empresa, e.nombre, reu.es_admin_empresa
                    FROM t_empresas e
                    INNER JOIN r_empresa_usuarios reu ON reu.id_empresa = e.id_empresa
                    WHERE reu.id_usuario = %s AND reu.status = 1 AND e.status = 1
                    ORDER BY reu.es_admin_empresa DESC, e.nombre
                    LIMIT 1
                    """,
                    (user_id,),
                )
                company_row = cursor.fetchone()
                if company_row:
                    id_empresa = company_row[0]
                    nombre_empresa = company_row[1]
                    es_admin_empresa = bool(company_row[2])
        finally:
            cursor.close()
            release_db_connection(connection)

        user = {
            "id": user_id,
            "username": username,
            "nombre": nombre,
            "perfil": perfil,
            "rol": rol,
            "id_empresa": id_empresa,
            "nombre_empresa": nombre_empresa,
            "es_admin_empresa": es_admin_empresa,
        }

        new_access_token = generate_access_token(user)
        new_refresh_crudo, _ = generate_refresh_token()
        save_refresh_token(
            id_usuario=user_id,
            token_crudo=new_refresh_crudo,
            ip_origen=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

        response = jsonify({"token": new_access_token, "user": user})
        return _set_refresh_cookie(response, new_refresh_crudo)

    except Exception as exc:
        logger.error("Error en POST /auth/refresh: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Cierra la sesión revocando el refresh token del dispositivo actual."""
    try:
        refresh_token_crudo = request.cookies.get(_COOKIE_NAME)
        if refresh_token_crudo:
            revoke_refresh_token(refresh_token_crudo)
        response = jsonify({"message": "Sesión cerrada correctamente"})
        return _clear_refresh_cookie(response)
    except Exception as exc:
        logger.error("Error en POST /auth/logout: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@auth_bp.route("/switch-company", methods=["POST"])
@jwt_required
def switch_company():
    """
    Cambia la empresa activa del usuario generando un nuevo access token.

    Validación:
      - id_empresa: entero positivo (>= 1)
    """
    data = request.get_json(silent=True)

    validation_error = validate_payload(SwitchCompanySchema(), data)
    if validation_error:
        return validation_error

    try:
        user_payload = request.user
        user_id = user_payload.get("sub")
        new_company_id = data["id_empresa"]

        companies = get_user_companies(user_id)
        target = next((c for c in companies if c["id_empresa"] == new_company_id), None)

        if not target:
            return jsonify({"error": "No tienes acceso a esta empresa"}), 403

        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT es_admin_empresa FROM r_empresa_usuarios
                WHERE id_usuario = %s AND id_empresa = %s AND status = 1
                """,
                (user_id, new_company_id),
            )
            rel_row = cursor.fetchone()
            es_admin_empresa = bool(rel_row[0]) if rel_row else False
        finally:
            cursor.close()
            release_db_connection(connection)

        new_user = {
            "id": user_id,
            "username": user_payload.get("username"),
            "nombre": user_payload.get("nombre"),
            "perfil": user_payload.get("perfil"),
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
