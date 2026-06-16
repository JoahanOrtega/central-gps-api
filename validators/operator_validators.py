"""
operator_validators.py — Schemas de validación para el catálogo de operadores.

Schemas:
  - CreateOperatorSchema  → POST /operadores
  - UpdateOperatorSchema  → PATCH /operadores/<id>
  - CreateOperatorGroupSchema / UpdateOperatorGroupSchema → grupos (Entrega 3)

Convención marshmallow del proyecto:
  - unknown = "EXCLUDE": los campos no declarados se descartan en silencio,
    así el payload llega limpio al service.
  - Campos de fecha como Str (no Date) porque el frontend manda 'YYYY-MM-DD'
    o cadena vacía; el service convierte '' → NULL. Validar el formato exacto
    aquí sería frágil con el calendario del frontend.
"""

from marshmallow import Schema, fields, validate

# ─── Operadores ───────────────────────────────────────────────────────────────


class CreateOperatorSchema(Schema):
    """Payload de POST /operadores."""

    class Meta:
        unknown = "EXCLUDE"

    id_empresa = fields.Int(load_default=None, allow_none=True)
    # nombre es el único obligatorio (coincide con NOT NULL en t_operadores).
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=200))

    clave = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    telefono = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    direccion = fields.Str(load_default=None, allow_none=True)
    imagen = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )

    # Fechas como string ('YYYY-MM-DD' o ''); el service normaliza '' → NULL.
    fecha_nacimiento = fields.Str(load_default=None, allow_none=True)
    vencimiento_licencia = fields.Str(load_default=None, allow_none=True)

    licencia = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    tipo_licencia = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=10)
    )

    # El identificador de tarjeta RFID — el objetivo de todo el módulo.
    rfid_tag = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    erp_link = fields.Str(load_default=None, allow_none=True)

    # Geocerca / POI asociado al operador.
    id_poi = fields.Int(load_default=None, allow_none=True)
    # Asignación a unidad (relación r_unidad_operador).
    id_unidad_operador = fields.Int(load_default=None, allow_none=True)

    # Grupos a los que pertenece el operador (relación N:M).
    id_grupo_operadores = fields.List(
        fields.Int(strict=True),
        load_default=[],
    )


class UpdateOperatorSchema(Schema):
    """
    Payload de PATCH /operadores/<id>.

    Todos los campos opcionales: el PATCH actualiza solo lo que llega. Si
    id_grupo_operadores viene, reemplaza la lista completa de grupos; si no
    viene, los grupos no se tocan.
    """

    class Meta:
        unknown = "EXCLUDE"

    id_empresa = fields.Int(allow_none=True)
    nombre = fields.Str(validate=validate.Length(min=1, max=200))
    clave = fields.Str(allow_none=True, validate=validate.Length(max=50))
    telefono = fields.Str(allow_none=True, validate=validate.Length(max=50))
    direccion = fields.Str(allow_none=True)
    imagen = fields.Str(allow_none=True, validate=validate.Length(max=200))
    fecha_nacimiento = fields.Str(allow_none=True)
    vencimiento_licencia = fields.Str(allow_none=True)
    licencia = fields.Str(allow_none=True, validate=validate.Length(max=50))
    tipo_licencia = fields.Str(allow_none=True, validate=validate.Length(max=10))
    rfid_tag = fields.Str(allow_none=True, validate=validate.Length(max=50))
    erp_link = fields.Str(allow_none=True)
    id_poi = fields.Int(allow_none=True)
    id_unidad_operador = fields.Int(allow_none=True)
    id_grupo_operadores = fields.List(fields.Int(strict=True))


# ─── Grupos de operadores (Entrega 3) ─────────────────────────────────────────


class CreateOperatorGroupSchema(Schema):
    """Payload de POST /operador-grupos."""

    class Meta:
        unknown = "EXCLUDE"

    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=150))
    observaciones = fields.Str(load_default=None, allow_none=True)
    id_operadores = fields.List(fields.Int(strict=True), load_default=[])


class UpdateOperatorGroupSchema(Schema):
    """Payload de PUT /operador-grupos/<id>."""

    class Meta:
        unknown = "EXCLUDE"

    nombre = fields.Str(validate=validate.Length(min=1, max=150))
    observaciones = fields.Str(allow_none=True)
    id_operadores = fields.List(fields.Int(strict=True))
