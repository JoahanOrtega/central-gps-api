from flask import Blueprint, jsonify, request
import logging
from services.telemetry_service import (
    get_latest_position_by_imei,
    get_positions_history_by_imei,
    get_route_by_mode,
    get_recent_trips_by_imei,
    get_trip_by_id,
    get_route_by_custom_range,
)
from utils.auth_guard import jwt_required

telemetry_bp = Blueprint("telemetry", __name__)

logger = logging.getLogger(__name__)


def get_required_empresa():
    """
    Lee id_empresa del query param primero (para sudo_erp que cambia
    de empresa vía el frontend), y del JWT como fallback (usuarios normales
    que tienen su empresa fija en el token).

    Retorna el valor o None — el endpoint que llama es responsable de
    validar y retornar 400 explícitamente si es None.
    """
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


@telemetry_bp.route("/telemetry/latest/<string:imei>", methods=["GET"])
@jwt_required
def get_latest_telemetry(imei):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        result = get_latest_position_by_imei(imei, id_empresa)
        if not result:
            return jsonify({"error": "No se encontró telemetría o no autorizado"}), 404
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /telemetry/latest: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/history/<string:imei>", methods=["GET"])
@jwt_required
def get_telemetry_history(imei):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        start_date = request.args.get("start")
        end_date = request.args.get("end")
        limit = request.args.get("limit", default=500, type=int)
        if not start_date or not end_date:
            return jsonify({"error": "Los parámetros start y end son requeridos"}), 400
        result = get_positions_history_by_imei(imei, start_date, end_date, limit)
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /telemetry/history: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/route/<string:imei>", methods=["GET"])
@jwt_required
def get_route(imei):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        mode = request.args.get("mode", "").strip()
        if mode not in ("latest", "today", "yesterday", "day_before_yesterday"):
            return jsonify({"error": "El parámetro mode no es válido"}), 400
        result = get_route_by_mode(imei, mode, id_empresa)
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /telemetry/route: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/recent-trips/<string:imei>", methods=["GET"])
@jwt_required
def get_recent_trips(imei):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        result = get_recent_trips_by_imei(imei, limit=10, id_empresa=id_empresa)
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /telemetry/recent-trips: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/trip/<string:imei>/<string:trip_id>", methods=["GET"])
@jwt_required
def get_trip(imei, trip_id):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        result = get_trip_by_id(imei, trip_id, id_empresa)
        if result is None:
            return jsonify({"error": "Recorrido no encontrado"}), 404
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /telemetry/trip: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@telemetry_bp.route("/telemetry/route-custom/<string:imei>", methods=["GET"])
@jwt_required
def get_route_custom(imei):
    try:
        id_empresa = get_required_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        start_date = request.args.get("start_date")
        start_time = request.args.get("start_time")
        end_date = request.args.get("end_date")
        end_time = request.args.get("end_time")
        if not start_date or not end_date:
            return jsonify({"error": "start_date y end_date son obligatorios"}), 400
        points = get_route_by_custom_range(
            imei,
            start_date,
            start_time,
            end_date,
            end_time,
            limit=5000,
            id_empresa=id_empresa,
        )
        return jsonify(points), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as error:
        logger.error("Error en /telemetry/route-custom: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
