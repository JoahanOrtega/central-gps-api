"""
Routes del módulo Catálogos > Usuarios.

NOTA SOBRE EL NOMBRE DEL ARCHIVO:
  Se llama 'catalog_user_routes.py' (no 'user_routes.py') para evitar
  colisión con el archivo existente 'user_routes.py' que ya define un
  blueprint 'users_bp' montado en /users (ese existente lista todos los
  usuarios del sistema y solo el sudo_erp lo consume).

  El blueprint que aquí se define se llama 'catalog_users_bp' por la
  misma razón.

Endpoints (todos prefijados con /catalogs):
  GET    /catalogs/users                       Listar usuarios de mi empresa
  POST   /catalogs/users                       Crear usuario
  GET    /catalogs/users/<id>                  Detalle de un usuario
  PATCH  /catalogs/users/<id>                  Editar usuario
  PATCH  /catalogs/users/<id>/status           Inhabilitar usuario

Autorización (jerárquica):
  - sudo_erp:      bypass de permisos, pero limitado a su empresa actual
                   por validate_empresa_access. Para operar en otras
                   empresas debe usar /admin-erp/empresas/<id>/...
  - admin_empresa: tiene los permisos del módulo por defecto.
  - usuario:       solo si se le asignaron explícitamente.

Diferencia con /admin-erp/empresas/<id>/usuarios-completo:
  Ese endpoint sigue existiendo para que el sudo_erp pueda operar sobre
  cualquier empresa pasando id_empresa explícitamente. Los endpoints de
  aquí asumen que el id_empresa viene del JWT (la empresa del que llama).
"""

import logging
from flask import Blueprint, jsonify, request

from services.user_service import (
    list_users_by_empresa,
    get_user_detail,
    create_user,
    update_user,
    inhabilitar_user,
)
from utils.auth_guard import permiso_required
from utils.validation import validate_payload
from validators import (
    CreateUserSchema,
    UpdateUserSchema,
    StatusUserSchema,
)

logger = logging.getLogger(__name__)

# El nombre del blueprint es 'catalog_users' para evitar conflicto con
# el blueprint existente llamado 'users' (montado en /users por
# routes/user_routes.py — el archivo legacy del sudo_erp).
catalog_users_bp = Blueprint("catalog_users", __name__, url_prefix="/catalogs")


# ─── Mapeo de errores del service a códigos HTTP ────────────────────────────
# Centralizado: si en el futuro se agrega un nuevo "code" en el service,
# basta con añadir la línea aquí. Códigos NO listados caen al default 500.
_USER_ERROR_STATUS = {
    "USER_NOT_FOUND": 404,
    "EMPRESA_NOT_FOUND": 404,
    "USERNAME_TAKEN": 409,
    "INVALID_PERMISSIONS": 422,
    "ROL_NOT_CONFIGURED": 500,
    "CANNOT_INHABILITAR_SELF": 403,
    "CANNOT_INHABILITAR_SUDO": 403,
    "DATABASE_ERROR": 500,
}


def _resolve_id_empresa() -> tuple[int | None, tuple | None]:
    """
    Resuelve el id_empresa para esta request.

    Reglas:
      1. Para admin_empresa / usuario:
         - El id_empresa SIEMPRE se toma del JWT (campo id_empresa).
         - Si el cliente pasa ?id_empresa=N en query string, se IGNORA
           si NO coincide con el del JWT (defensa en profundidad).
      2. Para sudo_erp:
         - Si pasa ?id_empresa=N en query string, se usa ese.
         - Si no pasa nada, se intenta tomar del JWT (raro pero posible).
         - Si tampoco trae JWT, retorna error: el sudo debe seleccionar
           empresa antes de operar en catálogos.

    Por qué este patrón:
      Espeja /catalogs/operators y /catalogs/unit-groups (catalogs_routes.py)
      donde el frontend pasa ?id_empresa=N tomado del selector visible
      del navbar (useEmpresaActiva). Esto permite al sudo_erp operar en
      cualquier empresa sin "switch company" forzado.

    Returns:
        Tupla (id_empresa, error_response). Si error_response no es None,
        el caller debe retornarla directamente al cliente.
    """
    rol = request.user.get("rol")
    jwt_id_empresa = request.user.get("id_empresa")
    qs_id_empresa = request.args.get("id_empresa", type=int)

    if rol == "sudo_erp":
        # sudo_erp: query param tiene prioridad sobre JWT.
        # Si no manda nada, intentamos JWT (caso raro de sudo con empresa fija).
        id_empresa = qs_id_empresa or jwt_id_empresa
    else:
        # admin_empresa o usuario: el JWT manda. Si manda query param
        # distinto al JWT, lo ignoramos silenciosamente — no avisamos
        # al cliente para no facilitar enumeración de empresas ajenas.
        id_empresa = jwt_id_empresa

    if not id_empresa:
        # sudo_erp sin empresa seleccionada: avisar con mensaje claro
        # que dirige al lugar correcto (el selector del navbar).
        return None, (
            jsonify(
                {
                    "error": (
                        "Sin empresa activa. Selecciona una empresa desde el "
                        "selector del navbar para operar en este catálogo."
                    ),
                }
            ),
            400,
        )

    return id_empresa, None


def _handle_service_error(error: dict, endpoint_name: str, **logging_extras):
    """
    Convierte un error del service en una respuesta HTTP apropiada.

    Centraliza el patrón de:
      - Mapear error code → status HTTP
      - Loguear los 5xx con detalles
      - Devolver mensaje genérico al cliente en 5xx (no filtrar internos)
      - Devolver mensaje original en 4xx (es info útil para el usuario)
    """
    code = error.get("code", "DATABASE_ERROR")
    status = _USER_ERROR_STATUS.get(code, 500)

    if status >= 500:
        logger.error(
            "Error %d en %s (%s): %s | %s",
            status,
            endpoint_name,
            code,
            error.get("message"),
            logging_extras,
        )
        return (
            jsonify({"error": "Ocurrió un error interno. Intenta más tarde."}),
            status,
        )

    return jsonify({"error": error["message"], "code": code}), status


# ─── GET /catalogs/users ──────────────────────────────────────────────────────


@catalog_users_bp.route("/users", methods=["GET"])
@permiso_required("usuarios.ver")
def list_users():
    """Lista los usuarios activos de la empresa del usuario logueado."""
    id_empresa, error_resp = _resolve_id_empresa()
    if error_resp:
        return error_resp

    try:
        data, error = list_users_by_empresa(id_empresa)
        if error:
            return _handle_service_error(
                error, "GET /catalogs/users", id_empresa=id_empresa
            )
        return jsonify(data), 200

    except Exception as exc:
        logger.error("Error en GET /catalogs/users: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── POST /catalogs/users ─────────────────────────────────────────────────────


@catalog_users_bp.route("/users", methods=["POST"])
@permiso_required("usuarios.editar")
def create_user_endpoint():
    """Crea un usuario nuevo en la empresa del usuario logueado."""
    id_empresa, error_resp = _resolve_id_empresa()
    if error_resp:
        return error_resp

    data = request.get_json(silent=True)

    data, validation_error = validate_payload(CreateUserSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_usuario_registro = int(request.user["sub"])

        result, error = create_user(
            id_empresa=id_empresa,
            payload=data,
            id_usuario_registro=id_usuario_registro,
        )

        if error:
            return _handle_service_error(
                error, "POST /catalogs/users", id_empresa=id_empresa
            )

        return (
            jsonify({"message": "Usuario creado correctamente", "usuario": result}),
            201,
        )

    except Exception as exc:
        logger.error("Error en POST /catalogs/users: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── GET /catalogs/users/<id> ────────────────────────────────────────────────


@catalog_users_bp.route("/users/<int:id_usuario>", methods=["GET"])
@permiso_required("usuarios.ver")
def get_user_endpoint(id_usuario: int):
    """Retorna el detalle de un usuario para edición."""
    id_empresa, error_resp = _resolve_id_empresa()
    if error_resp:
        return error_resp

    try:
        data, error = get_user_detail(id_usuario, id_empresa)
        if error:
            return _handle_service_error(
                error,
                f"GET /catalogs/users/{id_usuario}",
                id_empresa=id_empresa,
            )
        return jsonify(data), 200

    except Exception as exc:
        logger.error(
            "Error en GET /catalogs/users/%s: %s", id_usuario, repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── PATCH /catalogs/users/<id> ──────────────────────────────────────────────


@catalog_users_bp.route("/users/<int:id_usuario>", methods=["PATCH"])
@permiso_required("usuarios.editar")
def update_user_endpoint(id_usuario: int):
    """Actualiza parcialmente un usuario."""
    id_empresa, error_resp = _resolve_id_empresa()
    if error_resp:
        return error_resp

    data = request.get_json(silent=True)

    data, validation_error = validate_payload(UpdateUserSchema(), data)
    if validation_error:
        return validation_error

    if not data:
        return jsonify({"error": "No hay campos para actualizar"}), 400

    try:
        id_usuario_cambio = int(request.user["sub"])

        result, error = update_user(
            id_usuario=id_usuario,
            id_empresa=id_empresa,
            payload=data,
            id_usuario_cambio=id_usuario_cambio,
        )

        if error:
            return _handle_service_error(
                error,
                f"PATCH /catalogs/users/{id_usuario}",
                id_empresa=id_empresa,
            )

        return jsonify({"message": "Usuario actualizado correctamente", **result}), 200

    except Exception as exc:
        logger.error(
            "Error en PATCH /catalogs/users/%s: %s",
            id_usuario,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── PATCH /catalogs/users/<id>/status ───────────────────────────────────────


@catalog_users_bp.route("/users/<int:id_usuario>/status", methods=["PATCH"])
@permiso_required("usuarios.inhabilitar")
def update_user_status(id_usuario: int):
    """
    Cambia el status del usuario (solo inhabilitar desde el catálogo).

    Reactivar (status 0 → 1) NO se hace desde aquí — es exclusivo del
    Panel ERP porque el catálogo no muestra usuarios inhabilitados.
    """
    id_empresa, error_resp = _resolve_id_empresa()
    if error_resp:
        return error_resp

    data = request.get_json(silent=True)

    data, validation_error = validate_payload(StatusUserSchema(), data)
    if validation_error:
        return validation_error

    # El catálogo SOLO acepta 0 (inhabilitar). 1 (reactivar) se rechaza
    # con un mensaje claro que dirige al usuario al lugar correcto.
    if data["status"] != 0:
        return (
            jsonify(
                {
                    "error": (
                        "Para reactivar un usuario inhabilitado contacta al "
                        "administrador del sistema (Panel ERP)."
                    ),
                    "code": "REACTIVATE_NOT_ALLOWED",
                }
            ),
            403,
        )

    try:
        id_usuario_cambio = int(request.user["sub"])

        result, error = inhabilitar_user(
            id_usuario=id_usuario,
            id_empresa=id_empresa,
            id_usuario_cambio=id_usuario_cambio,
        )

        if error:
            return _handle_service_error(
                error,
                f"PATCH /catalogs/users/{id_usuario}/status",
                id_empresa=id_empresa,
            )

        return jsonify({"message": "Usuario inhabilitado correctamente", **result}), 200

    except Exception as exc:
        logger.error(
            "Error en PATCH /catalogs/users/%s/status: %s",
            id_usuario,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500
