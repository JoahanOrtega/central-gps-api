from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.dashboard_service import get_dashboard_summary
from utils.auth_guard import permiso_required

dashboard_bp = Blueprint("dashboard", __name__)
logger = logging.getLogger(__name__)


def _resolve_id_empresa() -> int | None:
    """Query param (sudo_erp) con fallback al id_empresa del JWT."""
    from_query = request.args.get("id_empresa", type=int)
    if from_query:
        return from_query

    jwt_value = request.user.get("id_empresa")  # type: ignore[attr-defined]
    return int(jwt_value) if jwt_value is not None else None


@dashboard_bp.route("/dashboard/summary", methods=["GET"])
@permiso_required("dashboard.ver")
def dashboard_summary():
    """Resumen de métricas del dashboard para la empresa activa."""
    try:
        id_empresa = _resolve_id_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        periodo = request.args.get("periodo", "hoy").strip().lower()

        try:
            result = get_dashboard_summary(id_empresa, periodo)
        except ValueError:
            # Periodo fuera del catálogo — error del cliente, no del servidor.
            return jsonify({"error": "Periodo inválido. Usa: hoy, 7d o 30d"}), 400

        return jsonify(result), 200

    except Exception as exc:
        logger.error("Error en /dashboard/summary: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
