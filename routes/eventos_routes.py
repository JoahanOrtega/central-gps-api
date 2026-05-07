"""
Endpoints para el historial de eventos de geocerca.

Registrar en app.py:
    from routes.eventos_routes import eventos_bp
    app.register_blueprint(eventos_bp)

Endpoints:
    GET /eventos        Historial paginado con filtros
    GET /eventos/export Exportacion CSV de los eventos filtrados
"""

import csv
import io
import logging
from flask import Blueprint, jsonify, request, Response

from utils.auth_guard import jwt_required, validate_empresa_access
from validators.eventos_validator import EventosFiltrosSchema
from services.eventos_service import get_eventos, get_eventos_export

logger = logging.getLogger(__name__)

eventos_bp = Blueprint("eventos", __name__)


def _resolve_empresa_y_filtros():
    id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
        "id_empresa"
    )

    if not id_empresa:
        return None, None, (jsonify({"error": "Empresa no especificada"}), 403)

    if not validate_empresa_access(id_empresa, request.user):
        return (
            None,
            None,
            (jsonify({"error": "Acceso no autorizado a esta empresa"}), 403),
        )

    # Copiar args sin id_empresa — el schema no lo espera
    raw_args = {k: v for k, v in request.args.to_dict().items() if k != "id_empresa"}

    if "tipos_evento" in raw_args:
        try:
            raw_args["tipos_evento"] = [
                int(t.strip())
                for t in raw_args["tipos_evento"].split(",")
                if t.strip().isdigit()
            ]
        except ValueError:
            raw_args["tipos_evento"] = None

    schema = EventosFiltrosSchema()
    try:
        filtros = schema.load(raw_args)
    except Exception as exc:
        msgs = exc.messages if hasattr(exc, "messages") else str(exc)
        return None, None, (jsonify({"error": msgs}), 422)

    filtros["id_empresa"] = id_empresa
    return id_empresa, filtros, None


@eventos_bp.route("/eventos", methods=["GET"])
@jwt_required
def listar_eventos():
    """
    Retorna el historial de eventos de geocerca paginado.

    Query params (todos opcionales):
        desde        ISO 8601 UTC — default: hace 7 dias
        hasta        ISO 8601 UTC — default: ahora
        id_unidad    int          — filtrar por unidad
        id_poi       int          — filtrar por POI
        tipos_evento 10,11,12...  — filtrar por tipo (coma separado)
        pagina       int          — default: 1
        limite       int          — default: 50, max: 200
        id_empresa   int          — requerido solo para sudo_erp

    Respuestas:
        200 -> { eventos, total, pagina, limite, total_paginas, tiene_mas }
        403 -> empresa no especificada
        422 -> errores de validacion
        500 -> error interno
    """
    try:
        _, filtros, error_resp = _resolve_empresa_y_filtros()
        if error_resp:
            return error_resp

        resultado, error = get_eventos(filtros)
        if error:
            status = 500 if error["code"] == "DATABASE_ERROR" else 400
            return jsonify(error), status

        return jsonify(resultado), 200

    except Exception as exc:
        logger.error("Error en GET /eventos: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@eventos_bp.route("/eventos/export", methods=["GET"])
@jwt_required
def exportar_eventos():
    """
    Exporta los eventos filtrados como archivo CSV.

    Mismos query params que GET /eventos (sin pagina ni limite).
    Limite maximo interno: 5000 filas.

    Respuestas:
        200 -> archivo CSV con Content-Disposition: attachment
        403 -> empresa no especificada
        422 -> errores de validacion
        500 -> error interno
    """
    try:
        _, filtros, error_resp = _resolve_empresa_y_filtros()
        if error_resp:
            return error_resp

        eventos, error = get_eventos_export(filtros)
        if error:
            return jsonify(error), 500

        # Generar CSV en memoria
        output = io.StringIO()
        writer = csv.writer(output)

        # Cabecera
        # La fecha viene con offset -06:00 (America/Mexico_City) desde el service.
        # El encabezado refleja la zona local para que el usuario no se confunda.
        writer.writerow(
            [
                "Fecha/Hora (America/Mexico_City)",
                "Unidad",
                "POI",
                "Tipo de evento",
                "Descripcion",
                "ID Evento",
            ]
        )

        # Filas
        for ev in eventos:
            writer.writerow(
                [
                    ev.get("fecha_hora_gmt", ""),
                    ev.get("numero_unidad", ""),
                    ev.get("nombre_poi", ""),
                    ev.get("tipo_evento", ""),
                    ev.get("descripcion", ""),
                    ev.get("id_evento", ""),
                ]
            )

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=eventos_geocerca.csv",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )

    except Exception as exc:
        logger.error("Error en GET /eventos/export: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500
