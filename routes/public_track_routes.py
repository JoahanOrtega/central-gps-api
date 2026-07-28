import logging
from flask import Blueprint, jsonify

from services.unit_token_service import get_unit_by_token
from services.telemetry_service import get_latest_positions_by_imeis
from utils.limiter import limiter

logger = logging.getLogger(__name__)

# Blueprint para endpoints públicos de rastreo de unidades. No requiere
# autenticación, pero sí un token secreto que se genera para cada unidad.
public_track_bp = Blueprint("public_track", __name__)


@public_track_bp.route("/public/track/unit/<token>", methods=["GET"])
# Limitamos a 30 requests/minuto por IP para evitar que un bot haga scraping de
# tokens y descubra unidades. El token es secreto, pero no es un password: si
@limiter.limit("30 per minute")
def track_unit_by_token(token: str):
    """
    Endpoint público para obtener la última posición de una unidad a partir de un
    token secreto. No requiere autenticación, pero sí un token que se genera para
    cada unidad. El token se puede revocar o expirar, y no se distingue entre
    "no existe", "revocado" o "expirado" para no filtrar información a quien sondea tokens. 
    La respuesta incluye información de la unidad y su última posición conocida.

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