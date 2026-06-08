"""
itinerary_group_validators.py — Schemas para Grupos y Roles de Itinerarios.

Schemas:
  - CreateGroupSchema   / UpdateGroupSchema
  - DiaRolSchema                              ← sub-schema para un día del rol
  - CreateRoleSchema    / UpdateRoleSchema
"""

from marshmallow import Schema, fields, validate, validates_schema, ValidationError

# ─── Grupos ───────────────────────────────────────────────────────────────────


class CreateGroupSchema(Schema):
    """Payload de POST /operation/itinerary-groups."""

    class Meta:
        unknown = "EXCLUDE"

    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=150))
    observaciones = fields.Str(load_default=None, allow_none=True)
    id_cliente = fields.Int(load_default=None, allow_none=True)

    # IDs de itinerarios a incluir en el grupo al crearlo (opcional)
    id_itinerarios = fields.List(
        fields.Int(strict=True),
        load_default=[],
    )


class UpdateGroupSchema(Schema):
    """Payload de PUT /operation/itinerary-groups/<id>."""

    class Meta:
        unknown = "EXCLUDE"

    nombre = fields.Str(validate=validate.Length(min=1, max=150))
    observaciones = fields.Str(allow_none=True)
    id_cliente = fields.Int(allow_none=True)
    # Si viene, reemplaza la lista completa de miembros
    id_itinerarios = fields.List(fields.Int(strict=True))


# ─── Roles ────────────────────────────────────────────────────────────────────


class DiaRolSchema(Schema):
    """
    Sub-schema para un día dentro de la secuencia del rol.

    Un día puede ser:
      - Un itinerario real:  es_descanso=False, id_itinerario requerido
      - Un descanso:         es_descanso=True,  id_itinerario no aplica
    """

    class Meta:
        unknown = "EXCLUDE"

    # Día del ciclo (1-based). Obligatorio.
    dia_rol = fields.Int(required=True, strict=True, validate=validate.Range(min=1))

    # Posición dentro del día (cuando un día tiene varios itinerarios)
    orden = fields.Int(load_default=1, validate=validate.Range(min=1))

    # Si es día de descanso, id_itinerario no es requerido
    es_descanso = fields.Bool(load_default=False)

    # Requerido cuando es_descanso=False
    id_itinerario = fields.Int(load_default=None, allow_none=True, strict=True)

    @validates_schema
    def validate_itinerario_requerido(self, data, **kwargs):
        """
        Si no es descanso, id_itinerario es obligatorio.
        Si es descanso, id_itinerario debe ser null/ausente.
        """
        es_descanso = data.get("es_descanso", False)
        id_itinerario = data.get("id_itinerario")

        if not es_descanso and not id_itinerario:
            raise ValidationError(
                {
                    "id_itinerario": [
                        "id_itinerario es requerido cuando es_descanso=False."
                    ]
                }
            )


class CreateRoleSchema(Schema):
    """Payload de POST /operation/itinerary-roles."""

    class Meta:
        unknown = "EXCLUDE"

    # Identificador corto visible al usuario
    clave = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=150))
    fecha_inicio_rol = fields.Date(
        load_default=None, allow_none=True, format="%Y-%m-%d"
    )
    dias_duracion = fields.Int(load_default=0, validate=validate.Range(min=0))
    observaciones = fields.Str(load_default=None, allow_none=True)

    # Secuencia de días del rol
    dias = fields.List(fields.Nested(DiaRolSchema), load_default=[])

    @validates_schema
    def validate_dias_consistentes(self, data, **kwargs):
        """
        Si se definen días, valida consistencia:
        - No puede haber dos entradas con el mismo (dia_rol, orden).
        - Si dias_duracion > 0, todos los dia_rol deben estar dentro del rango.
        """
        dias = data.get("dias", [])
        if not dias:
            return

        # Unicidad de (dia_rol, orden)
        claves = [(d["dia_rol"], d.get("orden", 1)) for d in dias]
        if len(claves) != len(set(claves)):
            raise ValidationError(
                {"dias": ["No puede haber dos entradas con el mismo dia_rol y orden."]}
            )

        # Coherencia con dias_duracion
        duracion = data.get("dias_duracion", 0)
        if duracion > 0:
            max_dia = max(d["dia_rol"] for d in dias)
            if max_dia > duracion:
                raise ValidationError(
                    {
                        "dias": [
                            f"dia_rol máximo ({max_dia}) supera dias_duracion ({duracion})."
                        ]
                    }
                )


class UpdateRoleSchema(Schema):
    """Payload de PUT /operation/itinerary-roles/<id>."""

    class Meta:
        unknown = "EXCLUDE"

    # Todos opcionales — sin load_default para no sobreescribir con NULL
    clave = fields.Str(allow_none=True, validate=validate.Length(max=50))
    nombre = fields.Str(validate=validate.Length(min=1, max=150))
    fecha_inicio_rol = fields.Date(allow_none=True, format="%Y-%m-%d")
    dias_duracion = fields.Int(validate=validate.Range(min=0))
    observaciones = fields.Str(allow_none=True)

    # Si viene, reemplaza la secuencia completa
    dias = fields.List(fields.Nested(DiaRolSchema))

    @validates_schema
    def validate_dias_consistentes(self, data, **kwargs):
        """Misma validación que en CreateRoleSchema."""
        dias = data.get("dias")
        if not dias:
            return

        claves = [(d["dia_rol"], d.get("orden", 1)) for d in dias]
        if len(claves) != len(set(claves)):
            raise ValidationError(
                {"dias": ["No puede haber dos entradas con el mismo dia_rol y orden."]}
            )

        duracion = data.get("dias_duracion", 0)
        if duracion > 0:
            max_dia = max(d["dia_rol"] for d in dias)
            if max_dia > duracion:
                raise ValidationError(
                    {
                        "dias": [
                            f"dia_rol máximo ({max_dia}) supera dias_duracion ({duracion})."
                        ]
                    }
                )
