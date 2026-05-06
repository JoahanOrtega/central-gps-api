"""
validators/eventos_validator.py
================================================================================
Schema de validacion para los filtros del historial de eventos de geocerca.
"""

from datetime import datetime, timezone, timedelta
from marshmallow import Schema, fields, validates, validates_schema, ValidationError


class EventosFiltrosSchema(Schema):
    """
    Valida y normaliza los query params del endpoint GET /eventos.

    Todos los campos son opcionales excepto id_empresa (lo resuelve el
    endpoint desde el JWT, no viene en el body).

    Defaults:
        desde       -> hace 7 dias a las 00:00:00 UTC
        hasta       -> hoy a las 23:59:59 UTC
        pagina      -> 1
        limite      -> 50 (max 200)
        tipos_evento -> todos (10,11,12,13,14,15)
    """

    desde = fields.DateTime(
        load_default=None,
        allow_none=True,
        metadata={
            "description": "Inicio del rango en ISO 8601 (UTC). Default: hace 7 dias."
        },
    )
    hasta = fields.DateTime(
        load_default=None,
        allow_none=True,
        metadata={"description": "Fin del rango en ISO 8601 (UTC). Default: ahora."},
    )
    id_unidad = fields.Integer(
        load_default=None,
        allow_none=True,
    )
    id_poi = fields.Integer(
        load_default=None,
        allow_none=True,
    )
    # Lista de tipos separados por coma: ?tipos_evento=10,11,12
    tipos_evento = fields.List(
        fields.Integer(),
        load_default=None,
        allow_none=True,
    )
    pagina = fields.Integer(
        load_default=1,
        validate=lambda v: v >= 1,
    )
    limite = fields.Integer(
        load_default=50,
        validate=lambda v: 1 <= v <= 200,
    )

    @validates("tipos_evento")
    def validar_tipos(self, value, **kwargs):
        if value is None:
            return
        validos = {10, 11, 12, 13, 14, 15}
        invalidos = [t for t in value if t not in validos]
        if invalidos:
            raise ValidationError(
                f"Tipos de evento invalidos: {invalidos}. "
                f"Validos: {sorted(validos)}"
            )

    @validates_schema
    def aplicar_defaults_fechas(self, data, **kwargs):
        """
        Aplica defaults de fechas y valida que 'desde' <= 'hasta'.
        """
        ahora = datetime.now(timezone.utc)

        if data.get("desde") is None:
            data["desde"] = ahora.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=7)

        if data.get("hasta") is None:
            data["hasta"] = ahora.replace(hour=23, minute=59, second=59, microsecond=0)

        # Asegurar que ambas fechas tengan timezone UTC
        if data["desde"].tzinfo is None:
            data["desde"] = data["desde"].replace(tzinfo=timezone.utc)
        if data["hasta"].tzinfo is None:
            data["hasta"] = data["hasta"].replace(tzinfo=timezone.utc)

        if data["desde"] > data["hasta"]:
            raise ValidationError(
                {"desde": ["'desde' debe ser anterior o igual a 'hasta'"]}
            )

        # Limitar rango maximo a 90 dias para proteger el servidor
        if (data["hasta"] - data["desde"]).days > 90:
            raise ValidationError({"desde": ["El rango maximo es de 90 dias"]})
