import logging
from flask import Blueprint, jsonify, request, send_file
from services.unit_service import (
    get_units,
    create_unit,
    get_unit_detail,
    update_unit,
    delete_unit,
)
from services.unit_token_service import (
    get_unit_token_config,
    regenerate_tracking_token,
    revoke_tracking_token,
)
from services.unit_image_service import save_unit_image, get_unit_image_path
from utils.auth_guard import jwt_required, permiso_required, validate_empresa_access
from utils.validation import validate_payload
from validators import CreateUnitSchema, UpdateUnitSchema

units_bp = Blueprint("units", __name__)

logger = logging.getLogger(__name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        search = request.args.get("search", "").strip()
        units = get_units(id_empresa, search if search else None)
        return jsonify(units), 200
    except Exception as error:
        logger.error(
            "Error en GET /units id_empresa=%s: %s",
            request.args.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units", methods=["POST"])
@permiso_required("unidades.crear")
def create_new_unit():
    """Crea una unidad. admin_empresa no hereda unidades.crear: requiere
    asignación explícita. Valida con CreateUnitSchema antes de tocar la BD."""
    data = request.get_json(silent=True)

    # Si el payload es inválido, fallar rápido. validate_payload deja `data`
    # filtrado a los campos declarados en el schema.
    data, validation_error = validate_payload(CreateUnitSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_usuario = request.user.get("sub")
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")

        if not id_usuario or not id_empresa:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_unit(data, id_usuario, id_empresa)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201

    except Exception as error:
        logger.error(
            "Error en POST /units id_empresa=%s: %s",
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>", methods=["GET"])
@permiso_required("unidades.editar")
def get_unit_full_detail(id_unidad: int):
    """Detalle completo de una unidad para la pantalla de edición (comparte el
    permiso unidades.editar). Solo sudo_erp ve el equipo instalado."""
    try:
        # El query param ?id_empresa permite a sudo_erp (sin empresa fija en el
        # JWT) operar sobre una específica; para los demás roles,
        # validate_empresa_access confirma que coincida con su token.
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        rol = request.user.get("rol")

        if not id_empresa:
            return jsonify({"error": "Empresa no definida en la sesión"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        unit = get_unit_detail(id_unidad, id_empresa, rol)
        if unit is None:
            return jsonify({"error": "Unidad no encontrada"}), 404

        return jsonify(unit), 200

    except Exception as error:
        logger.error(
            "Error en GET /units/%s id_empresa=%s: %s",
            id_unidad,
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>", methods=["PATCH"])
@permiso_required("unidades.editar")
def patch_unit(id_unidad: int):
    """Actualiza parcialmente una unidad. El servicio rechaza con 403 si un rol
    sin permiso técnico intenta tocar el equipo instalado (IMEI, chip, etc.)."""
    data = request.get_json(silent=True)

    data, validation_error = validate_payload(UpdateUnitSchema(), data)
    if validation_error:
        return validation_error

    try:
        # id_empresa es contexto, no un campo a actualizar: lo sacamos del body
        # para que no termine en el UPDATE (cambiar la empresa de una unidad no
        # está permitido). Prioridad: query param > body > JWT.
        id_empresa_body = data.pop("id_empresa", None)
        id_empresa = (
            request.args.get("id_empresa", type=int)
            or id_empresa_body
            or request.user.get("id_empresa")
        )
        rol = request.user.get("rol")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        # Tras quitar id_empresa el body puede quedar vacío (solo cambio de
        # contexto): es un no-op para el UPDATE.
        if not data:
            return jsonify({"error": "No hay campos para actualizar"}), 400

        result, error = update_unit(
            id_unidad=id_unidad,
            id_empresa=id_empresa,
            payload=data,
            rol=rol,
            id_usuario=id_usuario,
        )

        if error:
            status = {
                "UNIT_NOT_FOUND": 404,
                "FIELDS_NOT_ALLOWED": 403,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "Unidad actualizada correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as error:
        logger.error(
            "Error en PATCH /units/%s id_empresa=%s: %s",
            id_unidad,
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>", methods=["DELETE"])
@permiso_required("unidades.eliminar")
def remove_unit(id_unidad: int):
    """Soft-delete de una unidad. Se conserva el registro para auditoría
    histórica y posible restauración sin re-ingresar IMEI, chip, etc."""
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result, error = delete_unit(
            id_unidad=id_unidad,
            id_empresa=id_empresa,
            id_usuario_cambio=int(id_usuario),
        )

        if error:
            status = {
                "UNIT_NOT_FOUND": 404,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "Unidad eliminada correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as exc:
        logger.error(
            "Error en DELETE /units/%s: %s",
            id_unidad,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/upload-image", methods=["POST"])
@permiso_required("unidades.editar")
def upload_unit_image():
    """Recibe una imagen por multipart/form-data, la guarda en el volumen y
    devuelve su ruta pública para guardarla en el campo imagen de la unidad."""
    try:
        file = request.files.get("file")
        result, error = save_unit_image(file)

        if error:
            status = {
                "NO_FILE": 400,
                "INVALID_TYPE": 400,
                "TOO_LARGE": 413,
            }.get(error["code"], 500)
            return jsonify(error), status

        return jsonify(result), 201

    except Exception as exc:
        logger.error("Error en POST /units/upload-image: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/images/<nombre>", methods=["GET"])
def serve_unit_image(nombre):
    """Sirve una imagen del volumen. Sin auth de empresa: una imagen de unidad
    no es dato sensible y simplifica el <img src>."""
    try:
        ruta = get_unit_image_path(nombre)
        if not ruta:
            return jsonify({"error": "Imagen no encontrada"}), 404

        return send_file(ruta)

    except Exception as exc:
        logger.error(
            "Error en GET /units/images/%s: %s", nombre, repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500

# ─── Token de rastreo de unidad ──────────────────────────────────────────────
# Mismo patrón que el token de cliente (client_routes.py). El enlace público
# generado aquí lo consume el blueprint público_track_routes SIN autenticación.


def _resolve_empresa_token() -> int | None:
    """Resuelve la empresa para los endpoints de token. Prioridad: query > JWT."""
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


@units_bp.route("/units/<int:id_unidad>/token", methods=["GET"])
@jwt_required
def get_unit_token(id_unidad: int):
    """Lee la configuración del token de rastreo de la unidad."""
    try:
        id_empresa = _resolve_empresa_token()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        config = get_unit_token_config(id_unidad, id_empresa)
        if config is None:
            return jsonify({"error": "Unidad no encontrada"}), 404
        return jsonify(config), 200
    except Exception as exc:
        logger.error(
            "Error en GET /units/%s/token: %s", id_unidad, repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>/token/regenerar", methods=["POST"])
@permiso_required("unidades.editar")
def regenerate_unit_token(id_unidad: int):
    """Genera (o regenera) el token de rastreo y activa el acceso público."""
    try:
        id_empresa = _resolve_empresa_token()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        result = regenerate_tracking_token(id_unidad, id_empresa)
        if result is None:
            return jsonify({"error": "Unidad no encontrada"}), 404
        return jsonify({"message": "Token generado", **result}), 200
    except Exception as exc:
        logger.error(
            "Error en POST /units/%s/token/regenerar: %s",
            id_unidad,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>/token", methods=["DELETE"])
@permiso_required("unidades.editar")
def delete_unit_token(id_unidad: int):
    """Revoca el token: el enlace público deja de funcionar de inmediato."""
    try:
        id_empresa = _resolve_empresa_token()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        ok = revoke_tracking_token(id_unidad, id_empresa)
        if not ok:
            return jsonify({"error": "Unidad no encontrada"}), 404
        return jsonify({"message": "Token revocado"}), 200
    except Exception as exc:
        logger.error(
            "Error en DELETE /units/%s/token: %s",
            id_unidad,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500