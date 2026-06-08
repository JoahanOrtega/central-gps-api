import logging
from flask import Blueprint, jsonify, request
from services.itinerary_service import (
    get_itineraries_grouped,
    get_itineraries_paged,
    get_itinerary_by_id,
    is_turno_taken,
    create_itinerary,
    update_itinerary,
    delete_itinerary,
    PAGE_SIZE_DEFAULT,
    PAGE_SIZE_MAX,
)
from validators.itinerary_validators import (
    CreateItinerarySchema,
    UpdateItinerarySchema,
)
from utils.auth_guard import jwt_required, permiso_required
from utils.validation import validate_payload

itinerary_bp = Blueprint("itineraries", __name__)
logger = logging.getLogger(__name__)


# Helper para leer id_empresa


def _get_empresa() -> int | None:
    """
    Lee id_empresa del query param (sudo_erp) o del JWT (usuario normal).
    Mismo patrón que _get_empresa() en route_routes.py.
    """
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


def _empresa_or_400():
    """Retorna (id_empresa, None) o (None, response_400)."""
    empresa = _get_empresa()
    if not empresa:
        return None, (jsonify({"error": "Empresa no definida"}), 400)
    return empresa, None


# GET — Listado agrupado por ruta


@itinerary_bp.route("/operation/itineraries", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.ver")
def list_itineraries():
    """
    Listado de itinerarios agrupados por ruta.

    Query params opcionales:
      ?search=texto      → filtra por nombre o clave de ruta
      ?id_ruta=1         → filtra por ruta específica
      ?id_empresa=1      → empresa (solo sudo_erp)

    Respuesta:
      [
        {
          "id_ruta": 1, "clave_ruta": "R01", "nombre_ruta": "...",
          "itinerarios": [ { id_itinerario, turno, dias, ... }, ... ]
        },
        ...
      ]
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        search = request.args.get("search", "").strip()
        id_ruta = request.args.get("id_ruta", type=int)

        result = get_itineraries_grouped(
            id_empresa=empresa,
            search=search,
            id_ruta=id_ruta,
        )
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/itineraries")
        return jsonify({"error": "Error interno del servidor"}), 500


# GET — Listado plano paginado


@itinerary_bp.route("/operation/itineraries/paged", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.ver")
def list_itineraries_paged():
    """
    Listado plano paginado de itinerarios.

    Útil para selects, búsquedas y vistas de tabla con paginación.

    Query params:
      ?page=1            → número de página (base 1, default 1)
      ?page_size=25      → registros por página (máx 100, default 25)
      ?search=texto      → filtra en nombre/clave de ruta o código de turno
      ?id_ruta=1         → filtra por ruta específica
      ?id_empresa=1      → empresa (solo sudo_erp)

    Respuesta:
      {
        "data":       [ { id_itinerario, turno, nombre_ruta, ... }, ... ],
        "pagination": { "page": 1, "page_size": 25, "total": 147, "total_pages": 6 }
      }
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        # Parsear y sanitizar parámetros de paginación
        page = request.args.get("page", 1, type=int)
        page_size = request.args.get("page_size", PAGE_SIZE_DEFAULT, type=int)

        # Proteger contra valores fuera de rango antes de llegar al service
        if page < 1:
            return jsonify({"error": "page debe ser >= 1"}), 400
        if not (1 <= page_size <= PAGE_SIZE_MAX):
            return (
                jsonify({"error": f"page_size debe estar entre 1 y {PAGE_SIZE_MAX}"}),
                400,
            )

        search = request.args.get("search", "").strip()
        id_ruta = request.args.get("id_ruta", type=int)

        result = get_itineraries_paged(
            id_empresa=empresa,
            page=page,
            page_size=page_size,
            search=search,
            id_ruta=id_ruta,
        )
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/itineraries/paged")
        return jsonify({"error": "Error interno del servidor"}), 500


# GET — Detalle de un itinerario


@itinerary_bp.route("/operation/itineraries/<int:id_itinerario>", methods=["GET"])
@jwt_required
@permiso_required("itinerarios.ver")
def get_itinerary(id_itinerario: int):
    """
    Detalle completo de un itinerario, incluyendo paradas con hora de abordaje.

    Respuesta:
      {
        id_itinerario, id_ruta, turno, tipo, dias, hora_inicio, hora_fin,
        tolerancias, nombre_ruta, ...
        "paradas": [ { id_parada, numero, nombre, hora_abordaje, ... }, ... ]
      }
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_itinerary_by_id(id_itinerario, empresa)
        if result is None:
            return jsonify({"error": "Itinerario no encontrado"}), 404

        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/itineraries/%d", id_itinerario)
        return jsonify({"error": "Error interno del servidor"}), 500


# POST — Crear itinerario


@itinerary_bp.route("/operation/itineraries", methods=["POST"])
@jwt_required
@permiso_required("itinerarios.editar")
def create_itinerary_endpoint():
    """
    Crea un nuevo itinerario con sus paradas.

    Body JSON: ver CreateItinerarySchema.

    Retorna:
      201 → { "id_itinerario": <id>, "message": "..." }
      409 → turno duplicado en la misma logística
      422 → payload inválido
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        data, validation_error = validate_payload(
            CreateItinerarySchema(), request.get_json(silent=True)
        )
        if validation_error:
            return validation_error

        # Verificar que el turno no esté duplicado en la misma logística
        if data.get("turno") and is_turno_taken(
            turno=data["turno"],
            id_logistica_ruta=data["id_logistica_ruta"],
        ):
            return (
                jsonify(
                    {"error": f"El turno '{data['turno']}' ya existe en esta logística"}
                ),
                409,
            )

        id_usuario = int(request.user.get("sub"))
        id_itinerario = create_itinerary(data, empresa, id_usuario)

        return (
            jsonify(
                {
                    "id_itinerario": id_itinerario,
                    "message": "Itinerario creado correctamente",
                }
            ),
            201,
        )

    except Exception:
        logger.exception("POST /operation/itineraries")
        return jsonify({"error": "Error interno del servidor"}), 500


# PUT — Actualizar itinerario


@itinerary_bp.route("/operation/itineraries/<int:id_itinerario>", methods=["PUT"])
@jwt_required
@permiso_required("itinerarios.editar")
def update_itinerary_endpoint(id_itinerario: int):
    """
    Actualiza un itinerario existente. Reemplaza sus paradas por las nuevas.

    Body JSON: ver UpdateItinerarySchema.

    Retorna:
      200 → { "message": "..." }
      404 → itinerario no encontrado o no pertenece a la empresa
      409 → turno duplicado en la misma logística
      422 → payload inválido
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        data, validation_error = validate_payload(
            UpdateItinerarySchema(), request.get_json(silent=True)
        )
        if validation_error:
            return validation_error

        # Verificar unicidad del turno excluyendo el itinerario actual
        if data.get("turno") and is_turno_taken(
            turno=data["turno"],
            id_logistica_ruta=data["id_logistica_ruta"],
            exclude_id=id_itinerario,
        ):
            return (
                jsonify(
                    {"error": f"El turno '{data['turno']}' ya existe en esta logística"}
                ),
                409,
            )

        id_usuario = int(request.user.get("sub"))
        updated = update_itinerary(id_itinerario, data, empresa, id_usuario)

        if not updated:
            return jsonify({"error": "Itinerario no encontrado"}), 404

        return jsonify({"message": "Itinerario actualizado correctamente"}), 200

    except Exception:
        logger.exception("PUT /operation/itineraries/%d", id_itinerario)
        return jsonify({"error": "Error interno del servidor"}), 500


# DELETE — Eliminar itinerario (soft-delete)


@itinerary_bp.route("/operation/itineraries/<int:id_itinerario>", methods=["DELETE"])
@jwt_required
@permiso_required("itinerarios.borrar")
def delete_itinerary_endpoint(id_itinerario: int):
    """
    Marca el itinerario como inactivo (status=0).
    No elimina las paradas ni el historial.

    Retorna:
      200 → { "message": "..." }
      404 → itinerario no encontrado o no pertenece a la empresa
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        deleted = delete_itinerary(id_itinerario, empresa)

        if not deleted:
            return jsonify({"error": "Itinerario no encontrado"}), 404

        return jsonify({"message": "Itinerario eliminado correctamente"}), 200

    except Exception:
        logger.exception("DELETE /operation/itineraries/%d", id_itinerario)
        return jsonify({"error": "Error interno del servidor"}), 500
