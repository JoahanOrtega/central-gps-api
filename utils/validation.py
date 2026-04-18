from flask import jsonify
from marshmallow import Schema, ValidationError


def validate_payload(schema: Schema, data: dict | None):
    """
    Valida un payload JSON contra un schema de marshmallow.

    Centralizar aquí garantiza que todos los endpoints devuelven
    el mismo formato de error de validación — consistencia de API.

    Formato de respuesta en error:
        HTTP 422 Unprocessable Entity
        {
            "error": "Datos inválidos",
            "fields": {
                "imei": ["El IMEI debe tener exactamente 10 dígitos numéricos"],
                "fecha_instalacion": ["La fecha no puede ser futura"]
            }
        }

    Uso en un route:
        data = request.get_json(silent=True)
        error_response = validate_payload(CreateUnitSchema(), data)
        if error_response:
            return error_response   # (response, 422)

        # Si llegamos aquí el payload es válido
        result = create_unit(data, ...)

    Args:
        schema: Instancia del schema de marshmallow a usar.
        data:   Diccionario del payload JSON (puede ser None si el body está vacío).

    Returns:
        Tupla (response, 422) si hay errores de validación.
        None si el payload es válido.
    """
    # Body vacío o no JSON — error antes de intentar validar
    if data is None:
        return jsonify({"error": "El cuerpo de la solicitud es requerido"}), 400

    try:
        schema.load(data)
        return None  # Sin errores — el caller puede continuar
    except ValidationError as err:
        return (
            jsonify(
                {
                    "error": "Datos inválidos",
                    # err.messages es un dict {campo: [lista de mensajes]}
                    # Exactamente el formato que el frontend necesita para
                    # mostrar errores inline por campo
                    "fields": err.messages,
                }
            ),
            422,
        )
