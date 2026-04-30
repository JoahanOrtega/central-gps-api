"""
telemetry_routes.py — Endpoints de telemetría GPS

Convención de parámetros de fecha:
  - Todos los parámetros de fecha/hora que recibe el frontend están en UTC-6.
  - El servicio es responsable de convertir a UTC antes de consultar la BD.
  - Las respuestas siempre incluyen fechas en ISO 8601 con offset -06:00.
"""

from flask import Blueprint, jsonify, request
import logging
from services.telemetry_service import (
    get_route_by_mode,
    get_recent_trips_by_imei,
    get_trip_by_id,
    get_route_by_custom_range,
)
from utils.auth_guard import jwt_required

telemetry_bp = Blueprint("telemetry", __name__)
logger = logging.getLogger(__name__)

VALID_MODES = frozenset(
    {
        "latest",
        "today",
        "yesterday",
        "day_before_yesterday",
        # rangos por horas — el frontend los envía como custom con delta calculado
    }
)

MAX_POINTS = 5000


def _get_empresa(required: bool = True):
    """
    Lee id_empresa del query param (sudo_erp) o del JWT (usuario normal).
    Si required=True y no hay empresa, retorna None para que el endpoint
    devuelva 400.
    """
    val = request.args.get("id_empresa", type=int) or request.user.get("id_empresa")
    return val


def _empresa_or_400():
    """Helper que retorna (id_empresa, None) o (None, error_response)."""
    empresa = _get_empresa()
    if not empresa:
        return None, (jsonify({"error": "Empresa no definida"}), 400)
    return empresa, None


@telemetry_bp.route("/telemetry/route/<string:imei>", methods=["GET"])
@jwt_required
def get_route(imei):
    """
    Recorrido por modo predefinido.
    Param: mode = current | latest | today | yesterday | day_before_yesterday

    - current: viaje en curso. Si la unidad está apagada, devuelve [].
    - latest: último viaje completado.
    - today/yesterday/day_before_yesterday: día completo en UTC-6.
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        mode = request.args.get("mode", "").strip()
        if mode not in (
            "current",
            "latest",
            "today",
            "yesterday",
            "day_before_yesterday",
        ):
            return jsonify({"error": "mode inválido"}), 400

        result = get_route_by_mode(imei, mode, empresa)
        return jsonify(result), 200
    except Exception:
        logger.exception(
            "GET /telemetry/route/%s mode=%s", imei, request.args.get("mode")
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/recent-trips/<string:imei>", methods=["GET"])
@jwt_required
def get_recent_trips(imei):
    """Retorna los últimos 10 recorridos de los últimos 7 días."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        limit = min(request.args.get("limit", default=10, type=int), 50)
        result = get_recent_trips_by_imei(imei, limit=limit, id_empresa=empresa)

        # No exponer el campo `rows` (datos crudos internos) al cliente
        clean = [{k: v for k, v in t.items() if k != "rows"} for t in result]
        return jsonify(clean), 200
    except Exception:
        logger.exception("GET /telemetry/recent-trips/%s", imei)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/trip/<string:imei>/<string:trip_id>", methods=["GET"])
@jwt_required
def get_trip(imei, trip_id):
    """Retorna los puntos completos de un recorrido específico por ID."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_trip_by_id(imei, trip_id, empresa)
        if result is None:
            return jsonify({"error": "Recorrido no encontrado"}), 404

        return jsonify(result), 200
    except Exception:
        logger.exception("GET /telemetry/trip/%s/%s", imei, trip_id)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/route-custom/<string:imei>", methods=["GET"])
@jwt_required
def get_route_custom(imei):
    """
    Recorrido en rango personalizado.
    Params: start_date, start_time (opt), end_date, end_time (opt), limit (opt)
    Todas las fechas/horas se interpretan en UTC-6.
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        start_date = request.args.get("start_date", "").strip()
        start_time = request.args.get("start_time", "").strip() or None
        end_date = request.args.get("end_date", "").strip()
        end_time = request.args.get("end_time", "").strip() or None

        if not start_date or not end_date:
            return jsonify({"error": "start_date y end_date son obligatorios"}), 400

        if start_date > end_date:
            return (
                jsonify({"error": "start_date debe ser anterior o igual a end_date"}),
                400,
            )

        limit = min(request.args.get("limit", default=MAX_POINTS, type=int), MAX_POINTS)

        points = get_route_by_custom_range(
            imei,
            start_date,
            start_time,
            end_date,
            end_time,
            limit=limit,
            id_empresa=empresa,
        )
        return jsonify(points), 200
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        logger.exception("GET /telemetry/route-custom/%s", imei)
        return jsonify({"error": "Error interno del servidor"}), 500
