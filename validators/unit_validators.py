from marshmallow import Schema, fields, validate, validates, ValidationError
from datetime import date


class CreateUnitSchema(Schema):
    """
    Valida el payload de POST /units.

    Campos obligatorios: numero, marca, tipo, imei, chip, fecha_instalacion
    Reglas de negocio:
      - IMEI: exactamente 10 dígitos numéricos (estándar GSM)
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
        """IMEI estándar GSM: exactamente 10 dígitos numéricos."""
        if not value.isdigit() or len(value) != 10:
            raise ValidationError("El IMEI debe tener exactamente 10 dígitos numéricos")

    @validates("fecha_instalacion")
    def validate_fecha_instalacion(self, value, **kwargs):
        """La fecha de instalación no puede ser futura."""
        if value > date.today():
            raise ValidationError(
                "La fecha de instalación no puede ser una fecha futura"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Update schema — PATCH /units/<id>
# ═══════════════════════════════════════════════════════════════════════════
#
# Diferencias vs CreateUnitSchema:
#
# 1. TODOS los campos son opcionales. PATCH envía solo lo que cambió —
#    si el usuario editó el odómetro y la matrícula, el payload lleva
#    solo esos 2 campos. El servicio construye el UPDATE dinámicamente.
#
# 2. La matriz "qué rol puede editar qué campo" NO vive aquí — este
#    schema solo valida FORMATO (ej. el IMEI que llegó tiene 10 dígitos).
#    El filtro por rol se aplica en services/unit_service.update_unit.
#    Esto separa responsabilidades: Marshmallow → formato; servicio →
#    autorización de negocio.
#
# 3. unknown="EXCLUDE" descarta silenciosamente campos que no estén
#    definidos aquí. Si un atacante manda {"status": 0} para "borrar" la
#    unidad, se ignora sin error — no expone qué campos existen.


class UpdateUnitSchema(Schema):
    """
    Valida el payload de PATCH /units/<id>.

    Todos los campos son opcionales; solo se valida el formato de los
    que lleguen. El filtro de qué campos puede cambiar cada rol se
    aplica en el servicio (no aquí) porque depende de lógica de negocio
    (rol del usuario, permisos, empresa).
    """

    class Meta:
        # Descartar campos no declarados en lugar de fallar. Previene
        # que un body con claves inesperadas (o intentos de escalación
        # de privilegios por campo desconocido) genere un 400 ruidoso.
        unknown = "EXCLUDE"

    # ── Identidad y datos básicos ────────────────────────────────────────
    numero = fields.Str(validate=validate.Length(min=1, max=20))
    marca = fields.Str(validate=validate.Length(min=1, max=50))
    modelo = fields.Str(allow_none=True, validate=validate.Length(max=50))
    anio = fields.Str(allow_none=True, validate=validate.Length(max=4))
    matricula = fields.Str(allow_none=True, validate=validate.Length(max=20))
    no_serie = fields.Str(allow_none=True)
    tipo = fields.Int(
        validate=validate.OneOf(
            [1, 2, 3, 4, 5, 6, 7], error="Tipo de unidad no válido"
        ),
    )
    odometro_inicial = fields.Float(
        validate=validate.Range(min=0, error="El odómetro no puede ser negativo"),
    )
    imagen = fields.Str(allow_none=True)

    # ── Asignaciones ─────────────────────────────────────────────────────
    id_operador = fields.Int(allow_none=True)
    fecha_asignacion_operador = fields.Date(allow_none=True)
    id_grupo_unidades = fields.List(fields.Int())

    # ── Equipo instalado (solo sudo_erp puede cambiar estos) ─────────────
    # El schema acepta el formato pero el servicio rechaza con 403 si
    # el rol no es sudo_erp — ver services/unit_service.update_unit.
    id_modelo_avl = fields.Int(allow_none=True)
    imei = fields.Str()
    chip = fields.Str(validate=validate.Length(min=1, max=20))
    fecha_instalacion = fields.Date()
    input1 = fields.Int(validate=validate.Range(min=0, max=1))
    input2 = fields.Int(validate=validate.Range(min=0, max=1))
    output1 = fields.Int(validate=validate.Range(min=0, max=1))
    output2 = fields.Int(validate=validate.Range(min=0, max=1))

    # ── Datos adicionales: combustible ───────────────────────────────────
    tipo_combustible = fields.Str(allow_none=True)
    capacidad_tanque = fields.Float(allow_none=True, validate=validate.Range(min=0))
    rendimiento_establecido = fields.Float(
        allow_none=True, validate=validate.Range(min=0)
    )

    # ── Datos adicionales: seguro y verificación ─────────────────────────
    nombre_aseguradora = fields.Str(allow_none=True)
    telefono_aseguradora = fields.Str(allow_none=True)
    no_poliza_seguro = fields.Str(allow_none=True)
    vigencia_poliza_seguro = fields.Date(allow_none=True)
    vigencia_verificacion_vehicular = fields.Date(allow_none=True)

    # ── Validaciones por campo ───────────────────────────────────────────
    # Solo se ejecutan si el campo está presente en el payload (Marshmallow
    # no dispara @validates para campos ausentes cuando son opcionales).

    @validates("imei")
    def validate_imei(self, value, **kwargs):
        """IMEI estándar GSM: exactamente 10 dígitos numéricos."""
        if not value.isdigit() or len(value) != 10:
            raise ValidationError("El IMEI debe tener exactamente 10 dígitos numéricos")

    @validates("fecha_instalacion")
    def validate_fecha_instalacion(self, value, **kwargs):
        """La fecha de instalación no puede ser futura."""
        if value > date.today():
            raise ValidationError(
                "La fecha de instalación no puede ser una fecha futura"
            )
