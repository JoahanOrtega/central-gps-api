from marshmallow import Schema, fields, validate, validates_schema, ValidationError


class CreatePoiSchema(Schema):
    """
    Valida el payload de POST /pois.

    Todos los campos opcionales tienen load_default para que marshmallow
    los inyecte en el payload aunque el cliente no los envíe.
    Así poi_service.py puede acceder con payload["campo"] de forma segura.

    Validación cruzada por tipo_poi:
      tipo_poi=1 (marcador) → lat/lng requeridos
      tipo_poi=2 (círculo)  → lat/lng + radio requeridos
      tipo_poi=3 (polígono) → polygon_path requerido

    Notas de diseño:
      - `id_empresa` es opcional aquí pero el endpoint la usa: si no viene
        en el body, la lee del JWT. El sudo_erp la envía explícitamente
        porque su JWT no tiene empresa fija.
      - `unknown = "EXCLUDE"` descarta silenciosamente cualquier campo
        extra (evita 422 ruidosos y funciona como defensa ligera contra
        intentos de escalación de privilegios por campo desconocido).
    """

    class Meta:
        unknown = "EXCLUDE"

    # ── Contexto (no es un "campo de POI" pero el endpoint lo usa) ────────────
    # Ver la nota en el docstring sobre id_empresa.
    id_empresa = fields.Int(load_default=None, allow_none=True)

    # ── Obligatorios ──────────────────────────────────────────────────────────
    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    tipo_poi = fields.Int(
        required=True,
        validate=validate.OneOf(
            [1, 2, 3],
            error="tipo_poi debe ser 1 (marcador), 2 (círculo) o 3 (polígono)",
        ),
    )

    # ── Clasificación del elemento ────────────────────────────────────────────
    # El service usa payload["tipo_elemento"] y payload["id_elemento"] directamente
    tipo_elemento = fields.Str(load_default="poi")
    id_elemento = fields.Int(load_default=None, allow_none=True)

    # ── Geometría — todos con load_default para evitar KeyError en el service ─
    lat = fields.Float(
        load_default=None,
        allow_none=True,
        validate=validate.Range(
            min=-90, max=90, error="Latitud debe estar entre -90 y 90"
        ),
    )
    lng = fields.Float(
        load_default=None,
        allow_none=True,
        validate=validate.Range(
            min=-180, max=180, error="Longitud debe estar entre -180 y 180"
        ),
    )
    radio = fields.Float(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=1, error="El radio debe ser mayor a 0"),
    )
    polygon_path = fields.Str(load_default=None, allow_none=True)
    bounds = fields.Str(load_default=None, allow_none=True)
    area = fields.Float(load_default=None, allow_none=True)

    # ── Apariencia del marcador ───────────────────────────────────────────────
    tipo_marker = fields.Int(load_default=1)
    url_marker = fields.Str(load_default=None, allow_none=True)
    marker_path = fields.Str(load_default=None, allow_none=True)
    marker_color = fields.Str(
        load_default="#000000",
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del marcador debe ser hex válido (#RRGGBB)",
        ),
    )
    icon = fields.Str(load_default=None, allow_none=True)
    icon_color = fields.Str(
        load_default="#000000",
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$", error="Color del ícono debe ser hex válido (#RRGGBB)"
        ),
    )
    radio_color = fields.Str(
        load_default="#000000",
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$", error="Color del radio debe ser hex válido (#RRGGBB)"
        ),
    )
    polygon_color = fields.Str(
        load_default="#000000",
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del polígono debe ser hex válido (#RRGGBB)",
        ),
    )

    # ── Datos adicionales ─────────────────────────────────────────────────────
    direccion = fields.Str(load_default=None, allow_none=True)
    observaciones = fields.Str(load_default=None, allow_none=True)
    id_grupo_pois = fields.List(fields.Int(), load_default=[])

    @validates_schema
    def validate_geometry(self, data, **kwargs):
        """Valida que los campos geométricos sean consistentes con tipo_poi."""
        tipo = data.get("tipo_poi")

        if tipo in (1, 2):
            if data.get("lat") is None:
                raise ValidationError(
                    {"lat": ["La latitud es requerida para este tipo de POI"]}
                )
            if data.get("lng") is None:
                raise ValidationError(
                    {"lng": ["La longitud es requerida para este tipo de POI"]}
                )

        if tipo == 2:
            if not data.get("radio"):
                raise ValidationError(
                    {"radio": ["El radio es requerido para POI tipo círculo"]}
                )

        if tipo == 3:
            if not data.get("polygon_path"):
                raise ValidationError(
                    {
                        "polygon_path": [
                            "El polygon_path es requerido para POI tipo polígono"
                        ]
                    }
                )


class CreatePoiGroupSchema(Schema):
    """
    Valida el payload de POST /poi-groups.

    El service accede con payload["id_cliente"], payload["nombre"],
    payload["observaciones"] y payload["is_default"] directamente —
    todos tienen load_default para evitar KeyError.

    Nota: id_empresa es opcional (el endpoint la lee del body si viene, o
    del JWT). Ver nota equivalente en CreatePoiSchema.
    """

    class Meta:
        unknown = "EXCLUDE"

    # id_empresa: ver nota en CreatePoiSchema sobre el patrón sudo_erp.
    id_empresa = fields.Int(load_default=None, allow_none=True)

    nombre = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    # El service usa payload["id_cliente"] directamente — necesita default
    id_cliente = fields.Int(load_default=None, allow_none=True)
    # El service usa payload["observaciones"] directamente — necesita default
    observaciones = fields.Str(load_default=None, allow_none=True)
    # El service usa payload["is_default"] directamente — necesita default
    is_default = fields.Bool(load_default=False)


class UpdatePoiSchema(Schema):
    """
    Valida el payload de PATCH /pois/<id>.

    Diferencias clave respecto a CreatePoiSchema:
      - TODOS los campos son opcionales: el cliente solo manda lo que cambió.
      - SIN load_default: marshmallow no inyecta defaults — el service
        construye el UPDATE dinámico solo con las claves presentes en data.
        Si pusiéramos load_default=None, marshmallow inyectaría None y el
        UPDATE sobrescribiría con NULL columnas que el cliente no quería tocar.
      - SIN @validates_schema cruzado por tipo_poi: el cliente puede mandar
        solo `nombre` o solo `direccion` sin tener que reenviar la geometría.
        Si manda nuevos lat/lng o radio, los validators por campo ya cubren
        rangos válidos; la consistencia geométrica no es responsabilidad
        del PATCH parcial.

    El service usa data.keys() y data.values() iterando — por eso es CRÍTICO
    no inyectar defaults aquí. Ese es el único patrón seguro para PATCH.

    Notas de diseño:
      - `id_empresa` se permite por contexto (sudo_erp), igual que en Create.
        El service lo separa antes del UPDATE — nunca termina en el SQL.
      - `id_grupo_pois` reemplaza completamente la lista de grupos del POI
        si viene; si no viene, no se tocan los grupos existentes.
    """

    class Meta:
        unknown = "EXCLUDE"

    # ── Contexto ──────────────────────────────────────────────────────────────
    id_empresa = fields.Int(allow_none=True)

    # ── Atributos editables ───────────────────────────────────────────────────
    # Sin load_default: si no viene, no aparece en data.keys().
    nombre = fields.Str(validate=validate.Length(min=1, max=100))
    direccion = fields.Str(allow_none=True)
    observaciones = fields.Str(allow_none=True)

    tipo_poi = fields.Int(
        validate=validate.OneOf(
            [1, 2, 3],
            error="tipo_poi debe ser 1 (marcador), 2 (círculo) o 3 (polígono)",
        ),
    )

    # ── Geometría ─────────────────────────────────────────────────────────────
    lat = fields.Float(
        allow_none=True,
        validate=validate.Range(
            min=-90, max=90, error="Latitud debe estar entre -90 y 90"
        ),
    )
    lng = fields.Float(
        allow_none=True,
        validate=validate.Range(
            min=-180, max=180, error="Longitud debe estar entre -180 y 180"
        ),
    )
    radio = fields.Float(
        allow_none=True,
        validate=validate.Range(min=1, error="El radio debe ser mayor a 0"),
    )
    polygon_path = fields.Str(allow_none=True)
    bounds = fields.Str(allow_none=True)
    area = fields.Float(allow_none=True)

    # ── Apariencia ────────────────────────────────────────────────────────────
    tipo_marker = fields.Int()
    url_marker = fields.Str(allow_none=True)
    marker_path = fields.Str(allow_none=True)
    marker_color = fields.Str(
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del marcador debe ser hex válido (#RRGGBB)",
        ),
    )
    icon = fields.Str(allow_none=True)
    icon_color = fields.Str(
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del ícono debe ser hex válido (#RRGGBB)",
        ),
    )
    radio_color = fields.Str(
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del radio debe ser hex válido (#RRGGBB)",
        ),
    )
    polygon_color = fields.Str(
        validate=validate.Regexp(
            r"^#[0-9a-fA-F]{6}$",
            error="Color del polígono debe ser hex válido (#RRGGBB)",
        ),
    )

    # ── Grupos ────────────────────────────────────────────────────────────────
    # Si viene, reemplaza completamente la asignación de grupos del POI.
    # Si no viene en data.keys(), el service no toca r_grupo_pois_pois.
    id_grupo_pois = fields.List(fields.Int())
