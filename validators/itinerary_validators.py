from marshmallow import (
    Schema,
    fields,
    validate,
    validates,
    validates_schema,
    ValidationError,
)

# Constantes

# Valores válidos para el campo tipo
TIPOS_VALIDOS = (1, 2)

# Días válidos (0=domingo … 6=sábado)
DIAS_VALIDOS = frozenset(range(7))

# Límites de tolerancia en minutos
TOLERANCIA_MIN = 0
TOLERANCIA_MAX = 120


# Sub-schema de paradas


class ParadaItinerarioSchema(Schema):
    """
    Valida una parada dentro del payload de un itinerario.

    Cada parada referencia una fila de t_paradas_ruta (id_parada) y
    le asigna una hora de abordaje opcional.
    """

    class Meta:
        unknown = "EXCLUDE"

    # Referencia a t_paradas_ruta.id_parada — requerido
    id_parada = fields.Int(required=True, strict=True)

    # Hora de abordaje en formato "HH:MM" o "HH:MM:SS" — opcional
    hora_abordaje = fields.Time(
        load_default=None,
        allow_none=True,
        format="%H:%M",
        metadata={"example": "06:15"},
    )

    # Tiempos de recorrido en segundos — opcionales, usados en cumplimiento
    segundos_recorrido_continuo = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=0),
    )
    segundos_recorrido_mixto = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=0),
    )


# Schema de creación


class CreateItinerarySchema(Schema):
    """
    Valida el payload de POST /operation/itineraries.

    Campos obligatorios: id_ruta, id_logistica_ruta.
    Todos los opcionales tienen load_default para que el service pueda
    acceder con payload["campo"] sin riesgo de KeyError.
    """

    class Meta:
        unknown = "EXCLUDE"

    # Relación con la ruta
    id_ruta = fields.Int(
        required=True,
        strict=True,
        metadata={"description": "Ruta sobre la que corre el itinerario"},
    )
    id_logistica_ruta = fields.Int(
        required=True,
        strict=True,
        metadata={"description": "Logística (sentido ida/vuelta) de la ruta"},
    )

    # Identificación del turno
    # Código visible: '1', '2', '1A', etc. Opcional porque un itinerario
    # individual (tipo=2) no necesita código.
    turno = fields.Str(
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=10),
    )

    # Tipo y días
    tipo = fields.Int(
        load_default=1,
        validate=validate.OneOf(
            TIPOS_VALIDOS,
            error=f"tipo debe ser uno de: {TIPOS_VALIDOS}. "
            "1=regular (días recurrentes), 2=especial (fecha concreta).",
        ),
    )

    # Array de días de la semana: [0,1,2,3,4] = dom-jue
    # Lista de enteros, validada en @validates
    dias = fields.List(
        fields.Int(strict=True),
        load_default=[],
    )

    # Horario
    hora_inicio = fields.Time(
        load_default=None,
        allow_none=True,
        format="%H:%M",
        metadata={"example": "06:00"},
    )
    hora_fin = fields.Time(
        load_default=None,
        allow_none=True,
        format="%H:%M",
        metadata={"example": "07:30"},
    )

    # Tolerancias
    minutos_tolerancia_inicio = fields.Int(
        load_default=30,
        validate=validate.Range(
            min=TOLERANCIA_MIN,
            max=TOLERANCIA_MAX,
            error=f"Tolerancia de inicio debe estar entre {TOLERANCIA_MIN} y {TOLERANCIA_MAX} minutos",
        ),
    )
    minutos_tolerancia_fin = fields.Int(
        load_default=0,
        validate=validate.Range(
            min=TOLERANCIA_MIN,
            max=TOLERANCIA_MAX,
            error=f"Tolerancia de fin debe estar entre {TOLERANCIA_MIN} y {TOLERANCIA_MAX} minutos",
        ),
    )
    minutos_tolerancia_anticipacion = fields.Int(
        load_default=10,
        validate=validate.Range(
            min=TOLERANCIA_MIN,
            max=TOLERANCIA_MAX,
            error=f"Tolerancia de anticipación debe estar entre {TOLERANCIA_MIN} y {TOLERANCIA_MAX} minutos",
        ),
    )

    # Vigencia
    fecha_inicio = fields.Date(
        load_default=None,
        allow_none=True,
        format="%Y-%m-%d",
        metadata={"example": "2026-01-01"},
    )

    # Paradas con hora de abordaje
    paradas = fields.List(
        fields.Nested(ParadaItinerarioSchema),
        load_default=[],
    )

    # Validaciones por campo

    @validates("dias")
    def validate_dias(self, value, **kwargs):
        """
        Valida que los días sean enteros 0-6 sin repetidos.
        0=domingo, 1=lunes, ..., 6=sábado (igual que JavaScript Date).
        """
        invalidos = [d for d in value if d not in DIAS_VALIDOS]
        if invalidos:
            raise ValidationError(
                f"Días inválidos: {invalidos}. Usa enteros 0-6 (0=dom, 6=sáb)."
            )
        if len(value) != len(set(value)):
            raise ValidationError("El campo dias contiene valores repetidos.")

    # Validaciones cruzadas

    @validates_schema
    def validate_horario(self, data, **kwargs):
        """
        Si se envían hora_inicio y hora_fin, valida que no sean iguales.
        Un itinerario de duración cero no tiene sentido operativamente.
        Los turnos nocturnos (hora_fin < hora_inicio) SÍ son válidos.
        """
        inicio = data.get("hora_inicio")
        fin = data.get("hora_fin")
        if inicio is not None and fin is not None and inicio == fin:
            raise ValidationError(
                {"hora_fin": ["hora_fin no puede ser igual a hora_inicio."]}
            )

    @validates_schema
    def validate_paradas_unicas(self, data, **kwargs):
        """
        Valida que no haya dos paradas con el mismo id_parada en el mismo
        itinerario — sería una inconsistencia de datos.
        """
        paradas = data.get("paradas", [])
        ids = [p["id_parada"] for p in paradas]
        if len(ids) != len(set(ids)):
            raise ValidationError(
                {"paradas": ["No puede haber dos paradas con el mismo id_parada."]}
            )

    @validates_schema
    def validate_tipo_especial(self, data, **kwargs):
        """
        Si tipo=2 (especial), fecha_inicio es requerida.
        Los itinerarios especiales son para una fecha concreta, no recurrentes.
        """
        if data.get("tipo") == 2 and not data.get("fecha_inicio"):
            raise ValidationError(
                {
                    "fecha_inicio": [
                        "fecha_inicio es requerida cuando tipo=2 (especial)."
                    ]
                }
            )


# Schema de actualización


class UpdateItinerarySchema(Schema):
    """
    Valida el payload de PUT /operation/itineraries/<id>.
    """

    class Meta:
        unknown = "EXCLUDE"

    # Requeridos (necesarios para validar unicidad y FKs)
    id_ruta = fields.Int(required=True, strict=True)
    id_logistica_ruta = fields.Int(required=True, strict=True)

    # Opcionales SIN load_default (patrón PUT del proyecto)
    turno = fields.Str(allow_none=True, validate=validate.Length(max=10))
    tipo = fields.Int(validate=validate.OneOf(TIPOS_VALIDOS))
    dias = fields.List(fields.Int(strict=True))
    hora_inicio = fields.Time(allow_none=True, format="%H:%M")
    hora_fin = fields.Time(allow_none=True, format="%H:%M")
    minutos_tolerancia_inicio = fields.Int(
        validate=validate.Range(min=TOLERANCIA_MIN, max=TOLERANCIA_MAX)
    )
    minutos_tolerancia_fin = fields.Int(
        validate=validate.Range(min=TOLERANCIA_MIN, max=TOLERANCIA_MAX)
    )
    minutos_tolerancia_anticipacion = fields.Int(
        validate=validate.Range(min=TOLERANCIA_MIN, max=TOLERANCIA_MAX)
    )
    fecha_inicio = fields.Date(allow_none=True, format="%Y-%m-%d")
    paradas = fields.List(fields.Nested(ParadaItinerarioSchema))

    @validates("dias")
    def validate_dias(self, value, **kwargs):
        """Misma validación que en CreateItinerarySchema."""
        invalidos = [d for d in value if d not in DIAS_VALIDOS]
        if invalidos:
            raise ValidationError(
                f"Días inválidos: {invalidos}. Usa enteros 0-6 (0=dom, 6=sáb)."
            )
        if len(value) != len(set(value)):
            raise ValidationError("El campo dias contiene valores repetidos.")

    @validates_schema
    def validate_horario(self, data, **kwargs):
        """Misma validación cruzada que en CreateItinerarySchema."""
        inicio = data.get("hora_inicio")
        fin = data.get("hora_fin")
        if inicio is not None and fin is not None and inicio == fin:
            raise ValidationError(
                {"hora_fin": ["hora_fin no puede ser igual a hora_inicio."]}
            )

    @validates_schema
    def validate_paradas_unicas(self, data, **kwargs):
        """Misma validación que en CreateItinerarySchema."""
        paradas = data.get("paradas", [])
        ids = [p["id_parada"] for p in paradas]
        if len(ids) != len(set(ids)):
            raise ValidationError(
                {"paradas": ["No puede haber dos paradas con el mismo id_parada."]}
            )
