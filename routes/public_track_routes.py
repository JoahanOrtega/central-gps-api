import logging
from flask import Blueprint, jsonify

from services.unit_token_service import get_unit_by_token
from services.telemetry_service import get_latest_positions_by_imeis

logger = logging.getLogger(__name__)

# Blueprint SEPARADO a propósito: estas rutas son públicas por diseño (sin JWT).
# Mantenerlas fuera de units_bp deja explícito que aquí no hay guard de auth y
# evita que un @jwt_required global las cubra por accidente. El token ES la
# credencial.
public_track_bp = Blueprint("public_track", __name__)


@public_track_bp.route("/public/track/unit/<token>", methods=["GET"])
def track_unit_by_token(token: str):
    """
    Devuelve la posición actual de una unidad a partir de su token de rastreo.

    SIN autenticación: cualquiera con el enlace puede ver la unidad — ese es el
    propósito del token. Solo expone datos no sensibles (número, marca, modelo)
    más la última posición. Revocar el token o desactivar el acceso invalida
    este endpoint al instante (lo maneja get_unit_by_token).
    """
    try:
        unidad = get_unit_by_token(token)
        if unidad is None:
            # 404 genérico: no distinguimos "no existe" de "revocado" o
            # "expirado" para no filtrar información a quien sondea tokens.
            return jsonify({"error": "Enlace de rastreo no válido"}), 404

        # Última posición desde la telemetría. Reutiliza la misma función que el
        # mapa en vivo (una sola unidad, lista de un imei).
        posicion = None
        imei = unidad.get("imei")
        if imei:
            posiciones = get_latest_positions_by_imeis([imei])
            if posiciones:
                posicion = posiciones[0]

        return (
            jsonify(
                {
                    "unidad": {
                        "numero": unidad["numero"],
                        "marca": unidad["marca"],
                        "modelo": unidad["modelo"],
                        "vel_max": unidad["vel_max"],
                    },
                    "posicion": posicion,
                }
            ),
            200,
        )
    except Exception as exc:
        # No incluimos el token en el log para no dejarlo en texto plano.
        logger.error(
            "Error en GET /public/track/unit/<token>: %s", repr(exc), exc_info=True
        )
        return jsonify({"error": "Error interno del servidor"}), 500
