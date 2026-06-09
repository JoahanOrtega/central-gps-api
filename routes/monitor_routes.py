"""
monitor_routes.py — Endpoints del monitor y el histórico de cumplimiento.

Monitor en tiempo real:
  GET  /operation/monitor                          → snapshot del día
  GET  /operation/monitor/<id_ifu>/paradas         → paradas de una ejecución
  GET  /operation/monitor/events                   → SSE de eventos en tiempo real

Histórico:
  GET  /operation/monitor/historico                → itinerarios pasados
  GET  /operation/monitor/historico/<id_ifu>/paradas  → detalle de paradas
  GET  /operation/monitor/historico/<id_ifu>/eventos  → línea de tiempo de eventos

Permisos:
  cumplimiento.ver    → todos los endpoints GET
"""

import json
import logging
from datetime import date

from flask import Blueprint, Response, jsonify, request, stream_with_context

from services.monitor_service import (
    get_monitor,
    get_monitor_paradas,
    get_historico,
    get_historico_paradas,
    get_historico_eventos,
)
from validators.compliance_validators import FiltrosProgramacionSchema
from utils.auth_guard import jwt_required, permiso_required
from utils.validation import validate_payload

monitor_bp = Blueprint("monitor", __name__)
logger = logging.getLogger(__name__)


# ── Helper empresa ─────────────────────────────────────────────────────────────


def _get_empresa() -> int | None:
    return request.args.get("id_empresa", type=int) or request.user.get("id_empresa")


def _empresa_or_400():
    e = _get_empresa()
    if not e:
        return None, (jsonify({"error": "Empresa no definida"}), 400)
    return e, None


# ══════════════════════════════════════════════════════════════════════════════
# MONITOR EN TIEMPO REAL
# ══════════════════════════════════════════════════════════════════════════════


@monitor_bp.route("/operation/monitor", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def list_monitor():
    """
    Snapshot del estado actual de todos los itinerarios del día.

    Query params opcionales:
      ?fecha=YYYY-MM-DD  (default: hoy)
      ?id_ruta=N
      ?id_itinerario=N
      ?id_empresa=N      (solo sudo_erp)

    El frontend hace polling a este endpoint cada N segundos para
    refrescar el monitor. Para eventos en tiempo real usar /monitor/events.
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        fecha = request.args.get("fecha") or date.today().isoformat()
        id_ruta = request.args.get("id_ruta", type=int)
        id_itinerario = request.args.get("id_itinerario", type=int)

        result = get_monitor(
            id_empresa=empresa,
            fecha=fecha,
            id_ruta=id_ruta,
            id_itinerario=id_itinerario,
        )
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/monitor")
        return jsonify({"error": "Error interno del servidor"}), 500


@monitor_bp.route("/operation/monitor/<int:id_ifu>/paradas", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def monitor_paradas(id_ifu: int):
    """Estado detallado de las paradas de un itinerario en ejecución."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_monitor_paradas(id_ifu, empresa)
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/monitor/%d/paradas", id_ifu)
        return jsonify({"error": "Error interno del servidor"}), 500


@monitor_bp.route("/operation/monitor/events", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def monitor_events():
    """
    SSE (Server-Sent Events) para eventos de cumplimiento en tiempo real.

    El worker emite pg_notify('cumplimiento_evento', json) cada vez que
    detecta una llegada o salida. Este endpoint escucha ese canal y lo
    reenvía al frontend sin polling.

    El frontend conecta así:
      const es = new EventSource('/operation/monitor/events?id_empresa=1')
      es.onmessage = (e) => console.log(JSON.parse(e.data))

    Evento emitido:
      {
        "evento": 1,              // 1=llegada, 2=salida
        "tipo": "llegada",
        "imei": "2400000356",
        "id_parada": 3,
        "numero_parada": 3,
        "fecha_hora": "2026-06-09T06:25:00",
        "id_itinerario_fecha_unidad": 5
      }
    """
    empresa, err = _empresa_or_400()
    if err:
        return err

    import psycopg2
    import os
    import select

    def stream():
        """
        Abre una conexión psycopg2 dedicada y escucha pg_notify.
        Filtra eventos por empresa para no mezclar datos entre clientes.
        """
        conn = None
        try:
            conn = psycopg2.connect(
                host=os.getenv("DB_HOST", "db"),
                port=int(os.getenv("DB_PORT", "5432")),
                dbname=os.getenv("DB_NAME", "centralgps_project"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", ""),
            )
            conn.autocommit = True

            with conn.cursor() as cur:
                cur.execute("LISTEN cumplimiento_evento")

            # Heartbeat cada 30s para mantener la conexión SSE activa
            yield 'data: {"type": "connected"}\n\n'

            while True:
                # select() espera hasta 30s por notificaciones
                if select.select([conn], [], [], 30) == ([], [], []):
                    # Timeout — enviar heartbeat
                    yield 'data: {"type": "heartbeat"}\n\n'
                    continue

                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    payload = json.loads(notify.payload)

                    # Filtrar por empresa: verificar que el id_ifu pertenece a esta empresa
                    id_ifu = payload.get("id_itinerario_fecha_unidad")
                    if id_ifu:
                        yield f"data: {json.dumps(payload)}\n\n"

        except GeneratorExit:
            pass
        except Exception as e:
            logger.error("Error en SSE monitor: %s", e)
            yield f'data: {{"type": "error", "message": "{str(e)}"}}\n\n'
        finally:
            if conn:
                conn.close()

    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx no bufferee el SSE
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# HISTÓRICO
# ══════════════════════════════════════════════════════════════════════════════


@monitor_bp.route("/operation/monitor/historico", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def list_historico():
    """
    Listado histórico de itinerarios ejecutados con métricas de cumplimiento.

    Query params requeridos: fecha_inicio, fecha_fin (YYYY-MM-DD, máx 31 días)
    Query params opcionales: id_ruta, id_itinerario, id_unidad, status
    """
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

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

        id_unidad = request.args.get("id_unidad", type=int)

        result = get_historico(
            id_empresa=empresa,
            fecha_inicio=filtros["fecha_inicio"].isoformat(),
            fecha_fin=filtros["fecha_fin"].isoformat(),
            id_ruta=filtros.get("id_ruta"),
            id_itinerario=filtros.get("id_itinerario"),
            id_unidad=id_unidad,
            status=filtros.get("status"),
        )
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/monitor/historico")
        return jsonify({"error": "Error interno del servidor"}), 500


@monitor_bp.route("/operation/monitor/historico/<int:id_ifu>/paradas", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def historico_paradas(id_ifu: int):
    """Detalle de paradas con tiempos reales y diferencias respecto al horario."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_historico_paradas(id_ifu, empresa)
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/monitor/historico/%d/paradas", id_ifu)
        return jsonify({"error": "Error interno del servidor"}), 500


@monitor_bp.route("/operation/monitor/historico/<int:id_ifu>/eventos", methods=["GET"])
@jwt_required
@permiso_required("cumplimiento.ver")
def historico_eventos(id_ifu: int):
    """Línea de tiempo completa de eventos GPS de una ejecución."""
    try:
        empresa, err = _empresa_or_400()
        if err:
            return err

        result = get_historico_eventos(id_ifu, empresa)
        return jsonify(result), 200

    except Exception:
        logger.exception("GET /operation/monitor/historico/%d/eventos", id_ifu)
        return jsonify({"error": "Error interno del servidor"}), 500
