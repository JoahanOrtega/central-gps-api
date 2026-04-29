import logging
from flask import Blueprint, jsonify, request
from services.poi_service import (
    get_pois,
    create_poi,
    update_poi,
    delete_poi,
    get_poi_groups,
    create_poi_group,
    get_clients,
)
from utils.auth_guard import jwt_required, validate_empresa_access
from utils.validation import validate_payload
from validators import CreatePoiSchema, CreatePoiGroupSchema, UpdatePoiSchema

poi_bp = Blueprint("poi", __name__)

logger = logging.getLogger(__name__)


# ─── Helper: resolver id_empresa de contexto ──────────────────────────────────
# Encapsula el patrón repetido en CRUD endpoints. Fuentes en orden de
# prioridad: query param → body → JWT. Retorna (id_empresa, error_response)
# para que el caller pueda devolver el error directo si está incompleto.
def _resolve_empresa_context(body=None):
    id_empresa = (
        request.args.get("id_empresa", type=int)
        or (body or {}).get("id_empresa")
        or request.user.get("id_empresa")
    )
    id_usuario = request.user.get("sub")

    if not id_empresa or not id_usuario:
        return (
            None,
            None,
            (
                jsonify({"error": "Datos de autenticación incompletos"}),
                400,
            ),
        )

    if not validate_empresa_access(id_empresa, request.user):
        return (
            None,
            None,
            (
                jsonify({"error": "Acceso no autorizado a esta empresa"}),
                403,
            ),
        )

    return id_empresa, id_usuario, None


@poi_bp.route("/pois", methods=["GET"])
@jwt_required
def list_pois():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_pois(id_empresa, search if search else None)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/pois", methods=["POST"])
@jwt_required
def save_poi():
    """
    Crea un nuevo POI.

    Validación (marshmallow):
      - nombre: obligatorio, max 100 chars
      - tipo_poi: 1 (marcador), 2 (círculo) o 3 (polígono)
      - lat/lng: obligatorios para tipo 1 y 2, rango [-90,90] y [-180,180]
      - radio: obligatorio para tipo 2, > 0
      - polygon_path: obligatorio para tipo 3
      - colores: formato hex válido (#RRGGBB)

    Respuesta en error:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    # `data` queda filtrado: solo campos declarados en CreatePoiSchema.
    # Si el cliente mandó campos extra, se descartan silenciosamente.
    data, validation_error = validate_payload(CreatePoiSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp

        result = create_poi(data, id_empresa, id_usuario)
        return jsonify({"message": "POI creado correctamente", "poi": result}), 201

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/pois/<int:id_poi>", methods=["PATCH"])
@jwt_required
def patch_poi(id_poi: int):
    """
    Actualiza parcialmente un POI.

    Solo se mandan los campos que cambiaron. El service construye un
    UPDATE dinámico con las claves presentes en el payload — los campos
    omitidos no se tocan en BD.

    Validación:
      - Schema UpdatePoiSchema: valida formato de los campos presentes.
        Todos opcionales — el cliente solo manda lo que cambió.

    Respuestas:
      200 → { "message": "...", "actualizado": true, "id_poi": N }
      400 → body vacío o sin campos para actualizar
      403 → empresa no autorizada para el usuario
      404 → { "code": "POI_NOT_FOUND", "message": "..." }
      422 → errores de validación de schema
    """
    data = request.get_json(silent=True)

    data, validation_error = validate_payload(UpdatePoiSchema(), data)
    if validation_error:
        return validation_error

    try:
        # id_empresa es contexto, no campo de actualización. Lo separamos
        # antes de pasarlo al service para que no termine en el UPDATE SQL
        # (cambiar la empresa de un POI no es operación permitida).
        data.pop("id_empresa", None)

        id_empresa, id_usuario, error_resp = _resolve_empresa_context()
        if error_resp:
            return error_resp

        # Tras sacar id_empresa, el body podría quedar vacío.
        if not data:
            return jsonify({"error": "No hay campos para actualizar"}), 400

        result, error = update_poi(
            id_poi=id_poi,
            id_empresa=id_empresa,
            payload=data,
            id_usuario_cambio=int(id_usuario),
        )

        if error:
            status = {
                "POI_NOT_FOUND": 404,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "POI actualizado correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/pois/<int:id_poi>", methods=["DELETE"])
@jwt_required
def remove_poi(id_poi: int):
    """
    Elimina (soft-delete) un POI.

    El POI no se borra físicamente — se marca status=0. Mantenerlo en BD
    permite auditoría histórica y abre la puerta a una funcionalidad
    futura de "papelera" sin requerir re-ingresar las coordenadas.

    Respuestas:
      200 → { "message": "...", "eliminado": true, "id_poi": N }
      403 → empresa no autorizada para el usuario
      404 → { "code": "POI_NOT_FOUND", "message": "..." }
    """
    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context()
        if error_resp:
            return error_resp

        result, error = delete_poi(
            id_poi=id_poi,
            id_empresa=id_empresa,
            id_usuario_cambio=int(id_usuario),
        )

        if error:
            status = {
                "POI_NOT_FOUND": 404,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "POI eliminado correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/poi-groups", methods=["GET"])
@jwt_required
def list_poi_groups():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        return jsonify(get_poi_groups(id_empresa, search if search else None)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/poi-groups", methods=["POST"])
@jwt_required
def save_poi_group():
    """
    Crea un nuevo grupo de POIs.

    Validación (marshmallow):
      - nombre: obligatorio, max 100 chars
      - is_default: booleano (default false)
      - id_cliente: entero opcional

    Respuesta en error:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    data, validation_error = validate_payload(CreatePoiGroupSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_empresa, id_usuario, error_resp = _resolve_empresa_context(data)
        if error_resp:
            return error_resp

        result = create_poi_group(data, id_empresa, id_usuario)
        return jsonify({"message": "Grupo creado correctamente", "group": result}), 201

    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@poi_bp.route("/clients", methods=["GET"])
@jwt_required
def list_clients():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        return jsonify(get_clients(id_empresa)), 200
    except Exception as error:
        logger.error("Error en %s: %s", request.path, repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
