"""
notification_routes.py — Endpoints de la campanita de notificaciones.

Sin clave de permiso: las notificaciones son datos PERSONALES del usuario
autenticado (el WHERE id_usuario del servicio es el guard real). El token
identifica al dueño; id_empresa acota a la empresa activa.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services.notification_service import listar, marcar_leidas
from utils.auth_guard import jwt_required

logger = logging.getLogger(__name__)

notifications_bp = Blueprint("notifications", __name__)


def _id_usuario_del_token() -> int:
    # request.user["sub"] SIEMPRE es string (lección del proyecto):
    # castear antes de usarlo en SQL.
    return int(request.user["sub"])


@notifications_bp.route("/notifications", methods=["GET"])
@jwt_required
def get_notifications():
    """
    Últimas notificaciones del usuario en la empresa activa + badge.

    Query params:
        id_empresa (int, requerido)
        limit (int, default 20, máx 50)
        offset (int, default 0)
        solo_no_leidas (0|1, default 0)
    """
    try:
        id_empresa = int(request.args.get("id_empresa", ""))
    except ValueError:
        return jsonify({"error": "id_empresa es requerido"}), 400

    limit = min(int(request.args.get("limit", 20)), 50)
    offset = max(int(request.args.get("offset", 0)), 0)
    solo_no_leidas = request.args.get("solo_no_leidas") == "1"

    try:
        data = listar(
            id_usuario=_id_usuario_del_token(),
            id_empresa=id_empresa,
            limit=limit,
            offset=offset,
            solo_no_leidas=solo_no_leidas,
        )
        return jsonify(data), 200
    except Exception as exc:
        logger.error("Error en GET /notifications: %s", repr(exc), exc_info=True)
        return jsonify({"error": "No se pudieron obtener las notificaciones"}), 500


@notifications_bp.route("/notifications/read", methods=["POST"])
@jwt_required
def mark_notifications_read():
    """
    Marca notificaciones como leídas.

    Body JSON:
        { "id_empresa": <int>, "ids": [<int>, ...] }  → marca esas
        { "id_empresa": <int> }                       → marca TODAS
    """
    body = request.get_json(silent=True) or {}

    id_empresa = body.get("id_empresa")
    if not isinstance(id_empresa, int):
        return jsonify({"error": "id_empresa es requerido"}), 400

    ids = body.get("ids")
    if ids is not None and (
        not isinstance(ids, list) or not all(isinstance(i, int) for i in ids)
    ):
        return jsonify({"error": "ids debe ser una lista de enteros"}), 400

    try:
        actualizadas = marcar_leidas(
            id_usuario=_id_usuario_del_token(),
            id_empresa=id_empresa,
            ids=ids,
        )
        return jsonify({"actualizadas": actualizadas}), 200
    except Exception as exc:
        logger.error(
            "Error en POST /notifications/read: %s", repr(exc), exc_info=True
        )
        return jsonify({"error": "No se pudieron marcar las notificaciones"}), 500
