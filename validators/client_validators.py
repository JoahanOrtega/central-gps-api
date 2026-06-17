from marshmallow import Schema, fields, validate


class ClientPoiSchema(Schema):
    """
    Geocerca (POI) del cliente — objeto anidado en el payload.

    Refleja los campos que produce el GeoFenceTab del frontend. El service
    (create_client / update_client) toma este objeto y crea o actualiza el
    registro en t_pois dentro de la misma transacción, ligando el id_poi.

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


class CreateClientSchema(Schema):
    """
    Valida el payload de POST /catalogs/clients.
    unknown = "EXCLUDE" descarta cualquier campo extra que mande
    el cliente evita intentos de inyectar columnas no permitidas.
    """

    class Meta:
        unknown = "EXCLUDE"

    # Identificación
    # clave debe ser única por empresa — la validación de unicidad se hace
    # en el service, no aquí, porque requiere consultar la BD.
    clave = fields.Str(
        required=True,
        validate=validate.Length(
            min=1, max=50, error="La clave debe tener entre 1 y 50 caracteres"
        ),
    )
    nombre = fields.Str(
        required=True,
        validate=validate.Length(
            min=1, max=200, error="El nombre debe tener entre 1 y 200 caracteres"
        ),
    )

    # Datos de contacto (todos opcionales)
    contacto = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=200, error="El contacto no puede superar 200 caracteres"
        ),
    )
    telefono = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=50, error="El teléfono no puede superar 50 caracteres"
        ),
    )
    email = fields.Email(
        load_default=None,
        allow_none=True,
        metadata={"description": "Correo del cliente — se valida formato RFC 5321"},
    )
    observaciones = fields.Str(load_default=None, allow_none=True)

    # Relaciones
    # id_poi conecta al cliente con su ubicación geográfica (tabla t_pois)
    id_poi = fields.Int(load_default=None, allow_none=True)
    poi = fields.Nested(ClientPoiSchema, load_default=None, allow_none=True)

    # imagen guarda la ruta/nombre del archivo
    imagen = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=200, error="La ruta de imagen no puede superar 200 caracteres"
        ),
    )


class UpdateClientSchema(Schema):
    """
    Valida el payload de PUT /catalogs/clients/<id>.
    """

    class Meta:
        unknown = "EXCLUDE"

    clave = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            min=1, max=50, error="La clave debe tener entre 1 y 50 caracteres"
        ),
    )
    nombre = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            min=1, max=200, error="El nombre debe tener entre 1 y 200 caracteres"
        ),
    )
    contacto = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=200),
    )
    telefono = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=50),
    )
    email = fields.Email(load_default=None, allow_none=True)
    observaciones = fields.Str(load_default=None, allow_none=True)
    id_poi = fields.Int(load_default=None, allow_none=True)
    poi = fields.Nested(ClientPoiSchema, load_default=None, allow_none=True)
    imagen = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=200),
    )
