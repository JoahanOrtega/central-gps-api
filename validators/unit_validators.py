from marshmallow import Schema, fields, validate, validates, ValidationError
from datetime import date


class CreateUnitSchema(Schema):
    """
    Valida el payload de POST /units.

    Campos obligatorios: numero, marca, tipo, imei, chip, fecha_instalacion
    Reglas de negocio:
      - IMEI: exactamente 15 dígitos numéricos (estándar GSM)
      - odometro_inicial: >= 0
      - fecha_instalacion: no puede ser futura
      - tipo: valor del catálogo [1-7]
    """

    # ── Obligatorios ──────────────────────────────────────────────────────────
    numero = fields.Str(required=True, validate=validate.Length(min=1, max=20))
    marca = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    tipo = fields.Int(
        required=True,
        validate=validate.OneOf(
            [1, 2, 3, 4, 5, 6, 7], error="Tipo de unidad no válido"
        ),
    )
    imei = fields.Str(required=True)
    chip = fields.Str(required=True, validate=validate.Length(min=1, max=20))
    fecha_instalacion = fields.Date(required=True)
    odometro_inicial = fields.Float(
        load_default=0.0,
        validate=validate.Range(min=0, error="El odómetro no puede ser negativo"),
    )

    # ── Opcionales ────────────────────────────────────────────────────────────
    modelo = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    anio = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4)
    )
    matricula = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=20)
    )
    no_serie = fields.Str(load_default=None, allow_none=True)
    imagen = fields.Str(load_default=None, allow_none=True)
    id_modelo_avl = fields.Int(load_default=None, allow_none=True)
    id_operador = fields.Int(load_default=None, allow_none=True)
    fecha_asignacion_operador = fields.Date(load_default=None, allow_none=True)
    id_grupo_unidades = fields.List(fields.Int(), load_default=[])
    tipo_combustible = fields.Str(load_default=None, allow_none=True)
    capacidad_tanque = fields.Float(
        load_default=None, allow_none=True, validate=validate.Range(min=0)
    )
    rendimiento_establecido = fields.Float(
        load_default=None, allow_none=True, validate=validate.Range(min=0)
    )
    nombre_aseguradora = fields.Str(load_default=None, allow_none=True)
    telefono_aseguradora = fields.Str(load_default=None, allow_none=True)
    no_poliza_seguro = fields.Str(load_default=None, allow_none=True)
    vigencia_poliza_seguro = fields.Date(load_default=None, allow_none=True)
    vigencia_verificacion_vehicular = fields.Date(load_default=None, allow_none=True)
    input1 = fields.Int(load_default=0, validate=validate.Range(min=0, max=1))
    input2 = fields.Int(load_default=0, validate=validate.Range(min=0, max=1))
    output1 = fields.Int(load_default=0, validate=validate.Range(min=0, max=1))
    output2 = fields.Int(load_default=0, validate=validate.Range(min=0, max=1))

    @validates("imei")
    def validate_imei(self, value, **kwargs):
        """IMEI estándar GSM: exactamente 15 dígitos numéricos."""
        if not value.isdigit() or len(value) != 15:
            raise ValidationError("El IMEI debe tener exactamente 15 dígitos numéricos")

    @validates("fecha_instalacion")
    def validate_fecha_instalacion(self, value, **kwargs):
        """La fecha de instalación no puede ser futura."""
        if value > date.today():
            raise ValidationError(
                "La fecha de instalación no puede ser una fecha futura"
            )
