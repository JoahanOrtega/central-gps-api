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


class OperatorPoiSchema(Schema):
    """
    Geocerca (POI) del domicilio del operador — objeto anidado en el payload.

    Refleja los campos que produce el GeoFenceTab del frontend. El service
    (create_operator) toma este objeto y crea el registro en t_pois dentro de
    la misma transacción del operador, ligando el id_poi resultante.

    tipo_poi: 1 = circular (usa radio), 2 = poligonal (usa polygon_path/area).
    """

    class Meta:
        unknown = "EXCLUDE"

    tipo_poi = fields.Int(load_default=1, allow_none=True)
    direccion = fields.Str(load_default=None, allow_none=True)
    lat = fields.Float(load_default=None, allow_none=True)
    lng = fields.Float(load_default=None, allow_none=True)
    radio = fields.Int(load_default=None, allow_none=True)
    bounds = fields.Str(load_default=None, allow_none=True)
    area = fields.Str(load_default=None, allow_none=True)
    polygon_path = fields.Str(load_default=None, allow_none=True)
    polygon_color = fields.Str(load_default=None, allow_none=True)
    radio_color = fields.Str(load_default=None, allow_none=True)


class CreateOperatorSchema(Schema):
    """Payload de POST /operadores."""

    class Meta:
        unknown = "EXCLUDE"

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

    erp_link = fields.Str(load_default=None, allow_none=True)

    # Geocerca / POI asociado al operador.
    id_poi = fields.Int(load_default=None, allow_none=True)
    # Domicilio (geocerca) como objeto anidado — el service lo convierte en
    # un registro de t_pois. Si viene, tiene prioridad sobre id_poi.
    poi = fields.Nested(OperatorPoiSchema, load_default=None, allow_none=True)
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

    nombre = fields.Str(validate=validate.Length(min=1, max=200))
    clave = fields.Str(allow_none=True, validate=validate.Length(max=50))
    telefono = fields.Str(allow_none=True, validate=validate.Length(max=50))
    direccion = fields.Str(allow_none=True)
    imagen = fields.Str(allow_none=True, validate=validate.Length(max=200))
    fecha_nacimiento = fields.Str(allow_none=True)
    vencimiento_licencia = fields.Str(allow_none=True)
    licencia = fields.Str(allow_none=True, validate=validate.Length(max=50))
    tipo_licencia = fields.Str(allow_none=True, validate=validate.Length(max=10))
    erp_link = fields.Str(allow_none=True)
    id_poi = fields.Int(allow_none=True)
    poi = fields.Nested(OperatorPoiSchema, allow_none=True)
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
