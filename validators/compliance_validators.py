"""
compliance_validators.py — Schemas para el módulo de Cumplimiento (3A).
"""

from marshmallow import Schema, fields, validate, validates_schema, ValidationError
from datetime import date


class ProgramarItinerarioSchema(Schema):
    """Payload de POST /operation/compliance/schedule."""

    class Meta:
        unknown = "EXCLUDE"

    id_itinerario = fields.Int(required=True, strict=True)
    fecha = fields.Date(required=True, format="%Y-%m-%d")

    @validates_schema
    def validate_fecha_futura(self, data, **kwargs):
        """No permitir programar fechas muy antiguas (más de 1 año atrás)."""
        fecha = data.get("fecha")
        if fecha and (date.today() - fecha).days > 365:
            raise ValidationError(
                {
                    "fecha": [
                        "No se puede programar un itinerario con más de 1 año de antigüedad."
                    ]
                }
            )


class AsignarUnidadSchema(Schema):
    """Payload de POST /operation/compliance/<id>/assign."""

    class Meta:
        unknown = "EXCLUDE"

    id_unidad = fields.Int(required=True, strict=True)
    tipo_asignacion = fields.Int(
        load_default=1,
        validate=validate.OneOf(
            [1, 2],
            error="tipo_asignacion debe ser 1 (titular) o 2 (apoyo).",
        ),
    )


class FiltrosProgramacionSchema(Schema):
    """Query params para GET /operation/compliance."""

    class Meta:
        unknown = "EXCLUDE"

    fecha_inicio = fields.Date(required=True, format="%Y-%m-%d")
    fecha_fin = fields.Date(required=True, format="%Y-%m-%d")
    id_itinerario = fields.Int(load_default=None, allow_none=True)
    id_ruta = fields.Int(load_default=None, allow_none=True)
    status = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf([0, 1, 2, 3]),
    )

    @validates_schema
    def validate_rango(self, data, **kwargs):
        inicio = data.get("fecha_inicio")
        fin = data.get("fecha_fin")
        if inicio and fin:
            if fin < inicio:
                raise ValidationError(
                    {"fecha_fin": ["fecha_fin no puede ser anterior a fecha_inicio."]}
                )
            if (fin - inicio).days > 31:
                raise ValidationError(
                    {"fecha_fin": ["El rango máximo de consulta es 31 días."]}
                )
