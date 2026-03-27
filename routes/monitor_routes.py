from flask import Blueprint, jsonify, request
from services.monitor_service import get_units_with_latest_telemetry
from utils.auth_guard import jwt_required

monitor_bp = Blueprint("monitor", __name__)


@monitor_bp.route("/monitor/units-live", methods=["GET"])
@jwt_required
def get_units_live():
    try:
        search = request.args.get("search", "").strip()
        result = get_units_with_latest_telemetry(search if search else None)
        return jsonify(result), 200

    except Exception as error:
        print("ERROR EN /monitor/units-live:", error)
        return jsonify({"error": "Error interno del servidor"}), 500
