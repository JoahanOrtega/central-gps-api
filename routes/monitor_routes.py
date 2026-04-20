"""
monitor_routes.py — Endpoints HTTP del módulo de monitoreo en vivo.

Expone los dos endpoints que consume el frontend:
  GET /monitor/units-live           → unidades activas + telemetría + conteos
  GET /monitor/unit-summary/<imei>  → resumen liviano para el TripDrawer

Convención:
  - id_empresa se acepta por query-string (para uso sudo_erp) y, como
    fallback, se extrae del JWT del usuario autenticado.
  - Errores no controlados → 500 con payload genérico (no filtran detalles
    de BD al cliente).
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.monitor_service import (
    get_unit_summary_by_imei,
    get_units_with_latest_telemetry,
)
from utils.auth_guard import jwt_required

monitor_bp = Blueprint("monitor", __name__)
logger = logging.getLogger(__name__)


def _resolve_id_empresa() -> int | None:
    """
    Resuelve id_empresa de la petición actual.

    Prioridad:
      1. Query param `id_empresa` (usado por el rol sudo_erp que puede
         cambiar de empresa en vivo).
      2. Campo `id_empresa` del JWT (usuarios normales).

    Returns:
        int | None: El id_empresa resuelto, o None si no se pudo determinar.
    """
    from_query = request.args.get("id_empresa", type=int)
    if from_query:
        return from_query

    jwt_value = request.user.get("id_empresa")  # type: ignore[attr-defined]
    return int(jwt_value) if jwt_value is not None else None


@monitor_bp.route("/monitor/units-live", methods=["GET"])
@jwt_required
def get_units_live():
    """
    Devuelve { units: [...], counts: { total, engine_on, engine_off, engine_unknown } }

    El campo `counts` reemplaza el cálculo que el frontend hacía por cada
    render del badge 'X encendidas / Y apagadas' del UnitsDrawer.
    """
    try:
        id_empresa = _resolve_id_empresa()
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
def get_unit_summary(imei: str):
    """
    Devuelve el resumen de una unidad para el TripDrawer.

    Respuesta exitosa:
        {
          "id": 42,
          "numero": "U123",
          "marca": "Kenworth",
          "modelo": "T880",
          "imei": "...",
          "vel_max": 95.0,
          "last_report": "2026-04-17T12:33:00-06:00",
          "status": "100000000",
          "engine_state": "on",
          "hasTelemetry": true
        }
    """
    try:
        id_empresa = _resolve_id_empresa()
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        result = get_unit_summary_by_imei(imei, id_empresa)
        if not result:
            return jsonify({"error": "Unidad no encontrada"}), 404

        return jsonify(result), 200

    except Exception as error:
        logger.error("Error en /monitor/unit-summary: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
