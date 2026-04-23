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

    Retorno:
        Tupla `(clean_data, error_response)` donde:
          - Si el payload es VÁLIDO:
              clean_data      → dict con SOLO los campos declarados en el schema
                                (campos unknown descartados si Meta.unknown="EXCLUDE")
              error_response  → None
          - Si el payload es INVÁLIDO:
              clean_data      → None
              error_response  → Tupla (Flask response, 422)

    Uso recomendado en un route:

        data = request.get_json(silent=True)
        data, error = validate_payload(CreateUnitSchema(), data)
        if error:
            return error

        # A partir de aquí `data` es el payload LIMPIO — solo contiene
        # campos declarados en el schema. Cualquier campo extra que haya
        # mandado el cliente (intentando escalar privilegios, por ejemplo)
        # fue descartado por marshmallow.
        result = create_unit(data, ...)

    Por qué esto importa (seguridad):

        Antes de este cambio, validate_payload solo validaba pero el
        route seguía usando el dict ORIGINAL del request. Si un atacante
        mandaba {"usuario": "x", "clave": "y", "id_rol": 1, "status": 0},
        el schema los descartaba internamente pero el route podía leer
        data.get("id_rol") y usarlo.

        Con el retorno limpio, el route OBLIGATORIAMENTE usa solo campos
        validados. Si el service intenta leer un campo que el schema no
        declaro, obtiene None — defensa en profundidad.

    Args:
        schema: Instancia del schema de marshmallow a usar.
        data:   Diccionario del payload JSON (puede ser None si el body esta vacio).

    Returns:
        Tupla (clean_data, error):
          - (dict, None)  → payload valido y limpio
          - (None, tuple) → payload invalido, devolver error desde el route
    """
    # Body vacío o no JSON — error antes de intentar validar
    if data is None:
        return None, (
            jsonify({"error": "El cuerpo de la solicitud es requerido"}),
            400,
        )

    try:
        # schema.load() devuelve el payload deserializado con SOLO los
        # campos declarados (si Meta.unknown = "EXCLUDE") y con los tipos
        # ya convertidos (ej: "2026-04-22" → datetime.date).
        clean_data = schema.load(data)
        return clean_data, None
    except ValidationError as err:
        return None, (
            jsonify(
                {
                    "error": "Datos invalidos",
                    # err.messages es un dict {campo: [lista de mensajes]}
                    # Exactamente el formato que el frontend necesita para
                    # mostrar errores inline por campo.
                    "fields": err.messages,
                }
            ),
            422,
        )
