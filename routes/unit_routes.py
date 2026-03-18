from flask import Blueprint, jsonify
from services.unit_service import get_units

units_bp = Blueprint("units", __name__)


@units_bp.route("/units", methods=["GET"])
def list_units():
    try:
        units = get_units()
        return jsonify(units), 200
    except Exception as error:
        return jsonify({"error": str(error)}), 500