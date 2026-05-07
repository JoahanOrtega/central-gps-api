"""
validators/poi_alertas_validator.py
================================================================================
Schema de validacion para la configuracion de alertas POI.

Reglas de coherencia entre campos (espejo de los CHECK de t_alertas_poi):
  - Si permanencia=1: tipo_permanencia y minutos_permanencia son requeridos.
  - Si vel_max=1:     vel_max_permitida es requerida.
  - Si alcance=1:     id_grupo_unidades es requerido.

Marshmallow valida la estructura — los checks de coherencia se aplican
en @validates_schema para dar mensajes de error especificos por campo.
"""

from marshmallow import Schema, fields, validates_schema, ValidationError


class UpsertAlertaPoiSchema(Schema):
    """
    Valida el payload para crear o actualizar la alerta de un POI.
    Todos los campos son opcionales — se usan los defaults si no vienen.
    """

    # Toggle de alerta de entrada/salida
    # 0=desactivada, 1=activa
    in_out = fields.Integer(
        load_default=0,
        validate=lambda v: v in (0, 1),
        metadata={"description": "1=activar alerta de entrada/salida"},
    )

    # Toggle de alerta de permanencia
    permanencia = fields.Integer(
        load_default=0,
        validate=lambda v: v in (0, 1),
    )
    # 1=excede tiempo maximo, 2=no cumple tiempo minimo
    tipo_permanencia = fields.Integer(
        load_default=None,
        allow_none=True,
        validate=lambda v: v is None or v in (1, 2),
    )
    # Minutos umbral para la alerta de permanencia
    minutos_permanencia = fields.Integer(
        load_default=None,
        allow_none=True,
        validate=lambda v: v is None or v > 0,
    )

    # Toggle de alerta de velocidad maxima dentro del POI
    vel_max = fields.Integer(
        load_default=0,
        validate=lambda v: v in (0, 1),
    )
    # Velocidad limite en km/h
    vel_max_permitida = fields.Integer(
        load_default=None,
        allow_none=True,
        validate=lambda v: v is None or v > 0,
    )

    # Alcance de la alerta
    # 1=solo aplica al grupo id_grupo_unidades, 2=todas las unidades
    alcance = fields.Integer(
        load_default=2,
        validate=lambda v: v in (1, 2),
    )
    id_grupo_unidades = fields.Integer(
        load_default=None,
        allow_none=True,
    )

    @validates_schema
    def validar_coherencia(self, data, **kwargs):
        """
        Valida que los campos dependientes esten presentes cuando
        su toggle esta activo.
        """
        errors = {}

        # Permanencia activa requiere tipo y minutos
        if data.get("permanencia") == 1:
            if not data.get("tipo_permanencia"):
                errors["tipo_permanencia"] = [
                    "Requerido cuando permanencia esta activa (1=excede maximo, 2=no cumple minimo)"
                ]
            if not data.get("minutos_permanencia"):
                errors["minutos_permanencia"] = [
                    "Requerido cuando permanencia esta activa. Debe ser mayor a 0."
                ]

        # Velocidad maxima activa requiere el limite
        if data.get("vel_max") == 1:
            if not data.get("vel_max_permitida"):
                errors["vel_max_permitida"] = [
                    "Requerido cuando vel_max esta activa. Debe ser mayor a 0."
                ]

        # Alcance por grupo requiere el grupo
        if data.get("alcance") == 1:
            if not data.get("id_grupo_unidades"):
                errors["id_grupo_unidades"] = [
                    "Requerido cuando alcance=1 (por grupo de unidades)"
                ]

        if errors:
            raise ValidationError(errors)
