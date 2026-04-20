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

# ── Configuración de la cookie del refresh token ──────────────────────────────
#
# Detectamos si estamos en desarrollo local (HTTP + localhost/127.0.0.1) o
# en producción (cualquier otra cosa, típicamente HTTPS cross-domain).
#
# Reglas de cookies del navegador que nos importan:
#
#   DESARROLLO (FRONTEND y BACKEND en localhost):
#     - HTTP permitido (el navegador hace excepción para localhost)
#     - SameSite=Lax: la cookie se envía en navegaciones y en requests del
#       MISMO sitio. Suficiente para dev cuando frontend y backend comparten
#       hostname "localhost".
#     - Secure=False: obligatorio en HTTP o el navegador rechaza la cookie.
#
#   PRODUCCIÓN (HTTPS, frontend y backend posiblemente en subdominios distintos):
#     - SameSite=None: necesario para cross-site (p.ej. frontend en
#       app.ejemplo.com llamando al backend en api.ejemplo.com).
#     - Secure=True: OBLIGATORIO cuando SameSite=None. El navegador rechaza
#       la cookie si se intenta setear SameSite=None sin Secure.
#
# Una mala configuración acá causa 401 silencioso en /auth/refresh: la cookie
# está en el navegador pero no se envía porque SameSite la bloqueó.
_IS_LOCAL_DEV = Config.FRONTEND_URL.startswith(
    "http://localhost"
) or Config.FRONTEND_URL.startswith("http://127.0.0.1")

# SameSite=None (requerido para cross-site HTTPS) implica Secure=True.
# Lax es suficiente para dev donde todo corre en localhost same-site.
_COOKIE_SAMESITE = "Lax" if _IS_LOCAL_DEV else "None"
_COOKIE_SECURE = not _IS_LOCAL_DEV


def _set_refresh_cookie(response, refresh_token_crudo: str):
    response.set_cookie(
        _COOKIE_NAME,
        refresh_token_crudo,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAMESITE,
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
        samesite=_COOKIE_SAMESITE,
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
    """
    Renueva el access token usando el refresh token de la cookie HttpOnly.

    El JWT renovado mantiene el rol y la empresa del usuario. También incluye
    los permisos efectivos (rol + específicos) — mismo que al hacer login —
    para que la sesión renovada respete la autorización del decorador.

    Cambios del refactor 1:N:
      - id_empresa se lee directamente de t_usuarios (un solo query).
      - Se eliminó es_admin_empresa: para saber si es admin usar rol.
      - Se carga 'permisos' en el nuevo JWT (no se hacía antes — bug).
    """
    try:
        refresh_token_crudo = request.cookies.get(_COOKIE_NAME)

        if not refresh_token_crudo:
            # Sin cookie no hay nada que limpiar — solo responder 401.
            return jsonify({"error": "No hay sesión activa"}), 401

        result = validate_and_rotate_refresh_token(refresh_token_crudo)

        if not result:
            # NO borrar la cookie aquí. Razón: race condition.
            # Si dos requests paralelos entran con la misma cookie (ej.
            # React StrictMode en dev duplicando efectos, o navegación
            # rápida disparando múltiples refresh), el primer request
            # rota el token y deja la cookie antigua inválida. El segundo
            # request llega con esa cookie vieja y SI BORRAMOS AQUÍ
            # machacamos la cookie nueva que el primero acaba de emitir,
            # terminando la sesión del usuario.
            # El frontend se encarga de limpiar su estado local cuando
            # /auth/refresh retorna 401 en condiciones normales.
            return jsonify({"error": "Sesión expirada. Inicia sesión nuevamente."}), 401

        id_usuario = result["id_usuario"]

        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            # Query única: datos del usuario + empresa asignada.
            # En el modelo 1:N, id_empresa vive en t_usuarios. El LEFT JOIN
            # a t_empresas filtra e.status=1: si la empresa del usuario fue
            # suspendida, nombre_empresa queda NULL pero el login continúa
            # (la política de bloqueo por empresa suspendida se maneja
            # idealmente antes, al suspender la empresa).
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.usuario,
                    u.nombre,
                    u.perfil,
                    u.id_rol,
                    r.clave       AS rol,
                    u.id_empresa,
                    e.nombre      AS nombre_empresa
                FROM t_usuarios u
                LEFT JOIN t_roles    r ON r.id_rol     = u.id_rol
                LEFT JOIN t_empresas e ON e.id_empresa = u.id_empresa
                                       AND e.status    = 1
                WHERE u.id     = %s
                  AND u.status = 1
                """,
                (id_usuario,),
            )
            user_row = cursor.fetchone()

            if not user_row:
                response = jsonify({"error": "Usuario no encontrado o inactivo"})
                return _clear_refresh_cookie(response), 401

            (
                user_id,
                username,
                nombre,
                perfil,
                id_rol,
                rol,
                id_empresa,
                nombre_empresa,
            ) = user_row

            # Validar invariante del modelo 1:N: cualquier rol que no sea
            # sudo_erp debe tener empresa. Si llega inconsistente, rechazar
            # el refresh (forzar re-login) — es más seguro que emitir un
            # token con estado ambiguo.
            if rol != "sudo_erp" and id_empresa is None:
                logger.error(
                    "Refresh rechazado: usuario id=%s rol=%s sin id_empresa",
                    user_id,
                    rol,
                )
                response = jsonify(
                    {"error": "Sesión inválida. Inicia sesión nuevamente."}
                )
                return _clear_refresh_cookie(response), 401

            # Cargar permisos efectivos (rol + específicos). Importado de
            # auth_service para respetar la lógica canónica de un solo lugar.
            from services.auth_service import _load_user_permissions

            permisos: list[str] = []
            if id_rol is not None:
                permisos = _load_user_permissions(cursor, user_id, id_rol, id_empresa)
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
            "permisos": permisos,
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

    Restricción (modelo 1:N):
      Solo el rol sudo_erp puede cambiar de empresa. Los clientes
      (admin_empresa, usuario) pertenecen a UNA empresa — su id_empresa
      vive directamente en t_usuarios y no se cambia por este endpoint.

    Validación:
      - id_empresa: entero positivo (>= 1)
      - El sudo_erp debe tener acceso a la empresa (validado por
        get_user_companies que retorna todas las empresas activas para
        este rol).

    El nuevo JWT incluye los permisos recargados. Aunque el sudo_erp
    tiene bypass total en el decorador permiso_required, mantener el
    campo 'permisos' en el payload es coherente con los demás endpoints
    que emiten tokens (login, refresh).
    """
    # Bloquear a todos los roles que no sean sudo_erp. Esta es la
    # única barrera real: el frontend oculta el selector para no-sudo,
    # pero el backend debe rechazar también, para defensa en profundidad.
    if request.user.get("rol") != "sudo_erp":
        return (
            jsonify(
                {
                    "error": (
                        "Los usuarios cliente pertenecen a una sola empresa. "
                        "Esta operación está reservada al administrador del sistema."
                    )
                }
            ),
            403,
        )

    data = request.get_json(silent=True)

    validation_error = validate_payload(SwitchCompanySchema(), data)
    if validation_error:
        return validation_error

    try:
        user_payload = request.user
        user_id = user_payload.get("sub")
        new_company_id = data["id_empresa"]

        # Validar que el sudo_erp tiene acceso. get_user_companies() para
        # sudo_erp retorna todas las empresas activas (company_service.py).
        companies = get_user_companies(user_id)
        target = next((c for c in companies if c["id_empresa"] == new_company_id), None)

        if not target:
            return jsonify({"error": "La empresa no existe o no está activa"}), 404

        # Recargar permisos para la nueva empresa. El sudo_erp tiene bypass
        # en el decorador, pero dejar el campo coherente evita sorpresas
        # si el modelo de permisos cambia en el futuro.
        connection = get_db_connection()
        cursor = connection.cursor()
        try:
            from services.auth_service import _load_user_permissions

            cursor.execute(
                "SELECT id_rol FROM t_usuarios WHERE id = %s AND status = 1",
                (user_id,),
            )
            rol_row = cursor.fetchone()
            if not rol_row:
                return jsonify({"error": "Usuario no encontrado"}), 401
            id_rol = rol_row[0]
            permisos = _load_user_permissions(
                cursor, int(user_id), id_rol, new_company_id
            )
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
            "permisos": permisos,
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
