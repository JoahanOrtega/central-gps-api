"""Validadores de Rutas.

Una ruta llega con 1 o 2 logísticas, y cada logística con su trazo y paradas.
Validamos toda la estructura anidada de una vez con marshmallow.
"""

from marshmallow import Schema, fields, validate, EXCLUDE

# Tipos de ruta permitidos (mapean al campo `tipo` SMALLINT de la BD)
TIPO_RUTA_MAP = {
    "transporte_personal": 1,
    "transporte_publico": 4,
    "reparto": 2,
    "viaje_especial": 3,
}


class ParadaSchema(Schema):
    """Una parada (punto de abordaje) dentro de una logística."""

    class Meta:
        unknown = EXCLUDE

    numero = fields.Integer(required=True)
    nombre = fields.String(required=True, validate=validate.Length(min=1, max=300))
    direccion = fields.String(load_default="", allow_none=True)
    latitud = fields.Float(required=True, validate=validate.Range(min=-90, max=90))
    longitud = fields.Float(required=True, validate=validate.Range(min=-180, max=180))
    tipo_geocerca = fields.String(
        load_default="circular",
        validate=validate.OneOf(["circular", "poligonal", "rectangular"]),
    )
    radio = fields.Integer(load_default=100, validate=validate.Range(min=1))
    # vértices del polígono como lista de {lat, lng} — opcional
    poligono = fields.List(fields.Dict(), load_default=None, allow_none=True)


class LatLngSchema(Schema):
    class Meta:
        unknown = EXCLUDE

    lat = fields.Float(required=True)
    lng = fields.Float(required=True)


class LogisticaSchema(Schema):
    """Un sentido de la ruta (A-B o B-A) con su trazo y paradas."""

    class Meta:
        unknown = EXCLUDE

    id_logistica_ruta = fields.Integer(load_default=None, allow_none=True)
    tipo_logistica = fields.Integer(required=True, validate=validate.OneOf([1, 2]))
    direccion_inicio = fields.String(load_default="", allow_none=True)
    direccion_fin = fields.String(load_default="", allow_none=True)
    fecha_inicio = fields.String(load_default=None, allow_none=True)
    tiempo_recorrido_min = fields.Integer(load_default=None, allow_none=True)
    kilometros = fields.Float(load_default=None, allow_none=True)
    trace_color = fields.String(load_default="#2563eb")
    # El trazo llega como lista de coordenadas; el service lo codifica a polyline
    path = fields.List(fields.Nested(LatLngSchema), load_default=list)
    paradas = fields.List(fields.Nested(ParadaSchema), load_default=list)


class CreateRouteSchema(Schema):
    """Payload para crear una ruta nueva."""

    class Meta:
        unknown = EXCLUDE

    clave = fields.String(
        load_default="", allow_none=True, validate=validate.Length(max=50)
    )
    nombre = fields.String(required=True, validate=validate.Length(min=1, max=500))
    tipo = fields.String(
        required=True,
        validate=validate.OneOf(list(TIPO_RUTA_MAP.keys())),
    )
    id_cliente = fields.Integer(load_default=None, allow_none=True)
    observaciones = fields.String(load_default="", allow_none=True)
    id_grupo_rutas = fields.List(fields.Integer(), load_default=list)
    # id_empresa solo lo usa el sudo_erp; en usuarios normales viene del JWT
    id_empresa = fields.Integer(load_default=None, allow_none=True)
    # Una ruta debe tener al menos una logística
    logisticas = fields.List(
        fields.Nested(LogisticaSchema),
        required=True,
        validate=validate.Length(min=1, max=2),
    )


class UpdateRouteSchema(CreateRouteSchema):
    """Igual que crear, pero todos los campos son opcionales salvo logisticas."""

    class Meta:
        unknown = EXCLUDE

    nombre = fields.String(
        load_default=None, allow_none=True, validate=validate.Length(min=1, max=500)
    )
    tipo = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(list(TIPO_RUTA_MAP.keys())),
    )
    logisticas = fields.List(
        fields.Nested(LogisticaSchema), load_default=None, allow_none=True
    )
