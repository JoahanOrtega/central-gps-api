"""
================================================================================
Schema de validacion para los filtros del historial de eventos de geocerca.
================================================================================
"""

from datetime import datetime, timezone, timedelta
from marshmallow import (
    Schema,
    fields,
    validates,
    validates_schema,
    post_load,
    ValidationError,
)


class IsoDateTimeUTC(fields.DateTime):
    """
    DateTime que acepta los siguientes formatos:
      - 2026-05-05T06:00:00.000Z      (sufijo Z común en JavaScript)
      - 2026-05-05T06:00:00+00:00     (offset explícito)
      - 2026-05-05T06:00:00           (naive, se asume UTC)

    Marshmallow 4 retiró el fallback con python-dateutil y delega en
    datetime.fromisoformat(), que en Python <3.11 NO acepta 'Z'.
    """

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, str) and value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return super()._deserialize(value, attr, data, **kwargs)


class EventosFiltrosSchema(Schema):
    """
    Valida y normaliza los query params del endpoint GET /eventos.
    """

    desde = IsoDateTimeUTC(load_default=None, allow_none=True)
    hasta = IsoDateTimeUTC(load_default=None, allow_none=True)

    id_unidad = fields.Integer(load_default=None, allow_none=True)
    id_poi = fields.Integer(load_default=None, allow_none=True)

    tipos_evento = fields.List(
        fields.Integer(),
        load_default=None,
        allow_none=True,
    )

    pagina = fields.Integer(load_default=1, validate=lambda v: v >= 1)
    limite = fields.Integer(load_default=50, validate=lambda v: 1 <= v <= 200)

    @validates("tipos_evento")
    def validar_tipos(self, value, **kwargs):
        if not value:
            return
        validos = {3, 4, 10, 11, 12, 13, 14, 15, 19}
        invalidos = [t for t in value if t not in validos]
        if invalidos:
            raise ValidationError(
                f"Tipos de evento invalidos: {invalidos}. "
                f"Validos: {sorted(validos)}"
            )

    @validates_schema
    def validar_rango(self, data, **kwargs):
        """Sólo VALIDA. Los defaults se aplican en post_load."""
        desde = data.get("desde")
        hasta = data.get("hasta")
        if desde and hasta and desde > hasta:
            raise ValidationError(
                {"desde": ["'desde' debe ser anterior o igual a 'hasta'"]}
            )

    @post_load
    def aplicar_defaults_y_normalizar(self, data, **kwargs):
        """
        Marshmallow 4: post_load es el lugar correcto para
        mutar/normalizar el resultado final.
        """
        ahora = datetime.now(timezone.utc)

        if data.get("desde") is None:
            data["desde"] = ahora.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=7)
        if data.get("hasta") is None:
            data["hasta"] = ahora.replace(hour=23, minute=59, second=59, microsecond=0)

        # Asegurar timezone UTC en ambas
        if data["desde"].tzinfo is None:
            data["desde"] = data["desde"].replace(tzinfo=timezone.utc)
        if data["hasta"].tzinfo is None:
            data["hasta"] = data["hasta"].replace(tzinfo=timezone.utc)

        if (data["hasta"] - data["desde"]).days > 90:
            raise ValidationError({"desde": ["El rango maximo es de 90 dias"]})

        return data
