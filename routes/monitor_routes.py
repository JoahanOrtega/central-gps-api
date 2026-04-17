from flask import Blueprint, jsonify, request
import logging
from services.monitor_service import (
    get_units_with_latest_telemetry,
    get_unit_summary_by_imei,
)
from utils.auth_guard import jwt_required

monitor_bp = Blueprint("monitor", __name__)

logger = logging.getLogger(__name__)


@monitor_bp.route("/monitor/units-live", methods=["GET"])
@jwt_required
def get_units_live():
    try:
        # Query param primero (para sudo_erp), JWT como fallback (usuarios normales)
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        search = request.args.get("search", "").strip()
        result = get_units_with_latest_telemetry(id_empresa, search if search else None)
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /monitor/units-live: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@monitor_bp.route("/monitor/unit-summary/<string:imei>", methods=["GET"])
@jwt_required
def get_unit_summary(imei):
    try:
        # Query param primero (para sudo_erp), JWT como fallback (usuarios normales)
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400
        result = get_unit_summary_by_imei(imei, id_empresa)
        if not result:
            return jsonify({"error": "Unidad no encontrada"}), 404
        return jsonify(result), 200
    except Exception as error:
        logger.error("Error en /monitor/unit-summary: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
