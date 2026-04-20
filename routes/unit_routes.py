import logging
from flask import Blueprint, jsonify, request
from services.unit_service import (
    get_units,
    create_unit,
    get_unit_detail,
    update_unit,
)
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
@permiso_required("cund3")
def create_new_unit():
    """
    Crea una nueva unidad.

    Autorización (cund3 = "Crear unidades"):
      - sudo_erp       → acceso por bypass de rol.
      - admin_empresa  → denegado por diseño (el rol NO hereda cund3).
      - usuario        → permitido solo si el admin_empresa le asigna cund3
                         explícitamente en r_usuario_permisos.

    Validación (marshmallow):
      - numero, marca, tipo, imei, chip, fecha_instalacion: obligatorios
      - imei: exactamente 10 dígitos numéricos
      - odometro_inicial: >= 0
      - fecha_instalacion: no futura
      - tipo: valor del catálogo [1-7]

    Respuesta en error de validación:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    # Validar antes de tocar la BD — si el payload es inválido, fallar rápido
    validation_error = validate_payload(CreateUnitSchema(), data)
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


# ═══════════════════════════════════════════════════════════════════════════
# Detalle y edición de una unidad
# ═══════════════════════════════════════════════════════════════════════════


@units_bp.route("/units/<int:id_unidad>", methods=["GET"])
@permiso_required("cund_edit")
def get_unit_full_detail(id_unidad: int):
    """
    Devuelve el detalle completo de una unidad.

    Autorización (cund_edit = "Editar unidades"):
      - sudo_erp       → acceso por bypass de rol. Ve TODOS los campos,
                         incluido el equipo instalado (IMEI, chip, inputs).
      - admin_empresa  → tiene cund_edit por defecto (ver seed). Ve los
                         campos operativos SIN el equipo instalado.
      - usuario        → solo si el admin_empresa le asignó cund_edit en
                         r_usuario_permisos. Mismas restricciones que arriba.

    Nota: usamos cund_edit para LEER el detalle también (no solo para
    editarlo). El listado público sigue protegido solo con jwt_required
    (cund1 en el legacy) — este endpoint es para la pantalla de edición
    y comparte su permiso.

    Respuestas:
      200 → { ...campos de la unidad filtrados por rol }
      403 → sin permiso cund_edit (lo devuelve el decorador)
      404 → unidad no existe o pertenece a otra empresa
    """
    try:
        id_empresa = request.user.get("id_empresa")
        rol = request.user.get("rol")

        # Un usuario sin empresa asignada no debería haber llegado aquí
        # (auth_service bloquea login de no-sudo sin id_empresa). Pero
        # validamos por si acaso — el sudo_erp sí puede tener id_empresa
        # si ya hizo switch, si no, no puede ver unidades de nadie.
        if not id_empresa:
            return jsonify({"error": "Empresa no definida en la sesión"}), 400

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
@permiso_required("cund_edit")
def patch_unit(id_unidad: int):
    """
    Actualiza parcialmente una unidad.

    Autorización por campo:
      - sudo_erp       → puede editar todos los campos.
      - admin_empresa  → puede editar datos operativos (número, marca,
                         modelo, matrícula, operador, combustible, seguro,
                         verificación). NO puede editar equipo instalado
                         (IMEI, chip, modelo AVL, inputs/outputs, fecha
                         instalación). El servicio rechaza con 403.
      - usuario        → mismas reglas que admin_empresa (si tiene el
                         permiso asignado).

    Validación:
      - Schema UpdateUnitSchema: valida formato de los campos presentes.
        Todos opcionales — se actualiza solo lo que viene en el body.

    Respuestas:
      200 → { "message": "...", "actualizado": true }
      403 → { "code": "FIELDS_NOT_ALLOWED", "message": "..." } (servicio)
      404 → { "code": "UNIT_NOT_FOUND", "message": "..." }
      422 → errores de validación de schema
    """
    data = request.get_json(silent=True)

    # Validar formato antes de ir a la BD
    validation_error = validate_payload(UpdateUnitSchema(), data)
    if validation_error:
        return validation_error

    # Body vacío no tiene sentido — ahorra un round-trip a la BD.
    if not data:
        return jsonify({"error": "No hay campos para actualizar"}), 400

    try:
        id_empresa = request.user.get("id_empresa")
        rol = request.user.get("rol")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

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
