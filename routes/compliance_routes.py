"""
compliance_routes.py — Endpoints del módulo de Cumplimiento (Entrega 3A).

Programación:
  GET    /operation/compliance                     → listado de programaciones
  GET    /operation/compliance/<id>                → detalle con paradas y unidad
  POST   /operation/compliance/schedule            → programar itinerario para fecha
  DELETE /operation/compliance/<id>                → cancelar programación

Asignación de unidades:
  POST   /operation/compliance/<id>/assign         → asignar unidad
  DELETE /operation/compliance/assignment/<id>     → desasignar unidad

Permiso requerido: cumplimiento.ver / cumplimiento.editar
"""

import logging
from flask import Blueprint, jsonify, request

from services.compliance_service import (
    get_programacion,
    get_programacion_by_id,
    crear_programacion,
    cancelar_programacion,
    asignar_unidad,
    desasignar_unidad,
)
from validators.compliance_validators import (
    ProgramarItinerarioSchema,
    AsignarUnidadSchema,
    FiltrosProgramacionSchema,
)
from utils.auth_guard import jwt_required, permiso_required, permiso_required_any
from utils.validation import validate_payload

compliance_bp = Blueprint("compliance", __name__)
logger = logging.getLogger(__name__)


# ── Helper empresa ─────────────────────────────────────────────────────────────


def _get_empresa() -> int | None:
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


def _empresa_or_400():
    e = _get_empresa()
    if not e:
        return None, (jsonify({"error": "Empresa no definida"}), 400)
    return e, None


@compliance_bp.route("/operation/compliance", methods=["GET"])
@jwt_required
@permiso_required_any("cumplimiento.ver", "hist_cumplim.ver")
def list_programacion():
    """
    Lista la programación de itinerarios para un rango de fechas.

    Query params requeridos: fecha_inicio, fecha_fin (YYYY-MM-DD)
    Query params opcionales: id_itinerario, id_ruta, status, id_empresa
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        # Parsear y validar filtros desde query params
        filtros_raw = {
            "fecha_inicio": request.args.get("fecha_inicio"),
            "fecha_fin": request.args.get("fecha_fin"),
            "id_itinerario": request.args.get("id_itinerario", type=int),
            "id_ruta": request.args.get("id_ruta", type=int),
            "status": request.args.get("status", type=int),
        }
        filtros, verr = validate_payload(FiltrosProgramacionSchema(), filtros_raw)
        if verr:
            return verr

        result = get_programacion(
            id_empresa=empresa,
            fecha_inicio=filtros["fecha_inicio"].isoformat(),
            fecha_fin=filtros["fecha_fin"].isoformat(),
            id_itinerario=filtros.get("id_itinerario"),
            id_ruta=filtros.get("id_ruta"),
            status=filtros.get("status"),
        )
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/compliance")
        return jsonify({"error": "Error interno del servidor"}), 500


@compliance_bp.route("/operation/compliance/<int:id_itinerario_fecha>", methods=["GET"])
@jwt_required
@permiso_required_any("cumplimiento.ver", "hist_cumplim.ver")
def get_programacion_endpoint(id_itinerario_fecha: int):
    """Detalle completo de una programación con paradas y unidad asignada."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_programacion_by_id(id_itinerario_fecha, empresa)
        if not result:
            return jsonify({"error": "Programación no encontrada"}), 404

        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/compliance/%d", id_itinerario_fecha)
        return jsonify({"error": "Error interno del servidor"}), 500


@compliance_bp.route("/operation/compliance/schedule", methods=["POST"])
@jwt_required
@permiso_required("cumplimiento.editar")
def schedule_itinerary():
    """
    Programa un itinerario para una fecha concreta.

    Body: { id_itinerario, fecha }

    Retorna 200 si ya existía, 201 si se creó nuevo.
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        data, verr = validate_payload(
            ProgramarItinerarioSchema(), request.get_json(silent=True)
        )
        if verr:
            return verr

        id_usuario = int(request.user.get("sub"))
        result = crear_programacion(
            id_itinerario=data["id_itinerario"],
            fecha=data["fecha"].isoformat(),
            id_empresa=empresa,
            id_usuario=id_usuario,
        )

        status_code = 200 if result["ya_existia"] else 201
        return jsonify(result), status_code

    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception:
        logger.exception("POST /operation/compliance/schedule")
        return jsonify({"error": "Error interno del servidor"}), 500


@compliance_bp.route(
    "/operation/compliance/<int:id_itinerario_fecha>", methods=["DELETE"]
)
@jwt_required
@permiso_required("cumplimiento.editar")
def cancel_programacion(id_itinerario_fecha: int):
    """Cancela una programación (status=0). No elimina el registro."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        cancelled = cancelar_programacion(id_itinerario_fecha, empresa)
        if not cancelled:
            return jsonify({"error": "Programación no encontrada o ya cancelada"}), 404

        return jsonify({"message": "Programación cancelada correctamente"}), 200

    except Exception:
        logger.exception("DELETE /operation/compliance/%d", id_itinerario_fecha)
        return jsonify({"error": "Error interno del servidor"}), 500


# ══════════════════════════════════════════════════════════════════════════════
# ASIGNACIÓN DE UNIDADES
# ══════════════════════════════════════════════════════════════════════════════


@compliance_bp.route(
    "/operation/compliance/<int:id_itinerario_fecha>/assign", methods=["POST"]
)
@jwt_required
@permiso_required("cumplimiento.editar")
def assign_unit(id_itinerario_fecha: int):
    """
    Asigna una unidad a una programación e inicializa las paradas con PostGIS.

    Body: { id_unidad, tipo_asignacion }
    tipo_asignacion: 1=titular, 2=apoyo

    Retorna id_itinerario_fecha_unidad del registro creado.
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        data, verr = validate_payload(
            AsignarUnidadSchema(), request.get_json(silent=True)
        )
        if verr:
            return verr

        id_usuario = int(request.user.get("sub"))
        id_ifu = asignar_unidad(
            id_itinerario_fecha=id_itinerario_fecha,
            id_unidad=data["id_unidad"],
            tipo_asignacion=data["tipo_asignacion"],
            id_empresa=empresa,
            id_usuario=id_usuario,
        )

        return (
            jsonify(
                {
                    "id_itinerario_fecha_unidad": id_ifu,
                    "message": "Unidad asignada correctamente",
                }
            ),
            201,
        )

    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception:
        logger.exception("POST /operation/compliance/%d/assign", id_itinerario_fecha)
        return jsonify({"error": "Error interno del servidor"}), 500


@compliance_bp.route(
    "/operation/compliance/assignment/<int:id_ifu>", methods=["DELETE"]
)
@jwt_required
@permiso_required("cumplimiento.editar")
def unassign_unit(id_ifu: int):
    """Desasigna una unidad de una programación."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        removed = desasignar_unidad(id_ifu, empresa)
        if not removed:
            return jsonify({"error": "Asignación no encontrada"}), 404

        return jsonify({"message": "Unidad desasignada correctamente"}), 200

    except Exception:
        logger.exception("DELETE /operation/compliance/assignment/%d", id_ifu)
        return jsonify({"error": "Error interno del servidor"}), 500