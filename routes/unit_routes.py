import logging
from flask import Blueprint, jsonify, request
from services.unit_service import get_units, create_unit
from utils.auth_guard import jwt_required, permiso_required, validate_empresa_access
from utils.validation import validate_payload
from validators import CreateUnitSchema

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
