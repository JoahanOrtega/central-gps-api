"""
Validators para endpoints del panel ERP (solo sudo_erp).

Schemas definidos aquí:
  - CreateEmpresaAdminSchema: valida el payload para crear un usuario
    admin de empresa desde el panel ERP.
"""

from marshmallow import Schema, fields, validate


class CreateEmpresaAdminSchema(Schema):
    """
    Valida el payload de POST /admin-erp/empresas/<id_empresa>/usuarios.

    Reglas de negocio:
      - `usuario` es el login — debe ser único en toda la tabla t_usuarios.
        La unicidad se verifica en el service, no en el schema (requiere BD).
      - `clave` es la contraseña en texto plano. El service la hashea con
        bcrypt antes de guardar. Se limita a 128 chars para prevenir DoS
        en bcrypt (complejidad O(costo * len)).
      - `nombre` es el nombre real de la persona (visible en UI).
      - `email` y `telefono` son opcionales. Si vienen, deben cumplir formato.

    Los campos se normalizan con `.strip()` antes de validar la longitud
    para que " juanperez " y "juanperez" sean equivalentes y no pasen
    espacios en blanco a la BD.

    Nota: id_empresa viene en la URL (path param), NO en el body. El
    `unknown = "EXCLUDE"` igual está por consistencia y defensa en
    profundidad — si alguien intenta enviar campos como {"id_rol": 1,
    "status": 0}, se descartan silenciosamente en vez de filtrar
    información sobre qué campos existen en t_usuarios.
    """

    class Meta:
        unknown = "EXCLUDE"

    usuario = fields.Str(
        required=True,
        validate=[
            validate.Length(
                min=3,
                max=100,
                error="El nombre de usuario debe tener entre 3 y 100 caracteres",
            ),
        ],
        metadata={"description": "Nombre de login del usuario"},
    )

    clave = fields.Str(
        required=True,
        validate=[
            validate.Length(
                min=8,
                max=128,
                error="La contraseña debe tener entre 8 y 128 caracteres",
            ),
        ],
        metadata={"description": "Contraseña en texto plano (se hasea con bcrypt)"},
    )

    nombre = fields.Str(
        required=True,
        validate=[
            validate.Length(
                min=2,
                max=150,
                error="El nombre debe tener entre 2 y 150 caracteres",
            ),
        ],
        metadata={"description": "Nombre real de la persona"},
    )

    email = fields.Email(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=150, error="El email no puede exceder 150 caracteres"
        ),
        metadata={"description": "Email de contacto (opcional)"},
    )

    telefono = fields.Str(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=20, error="El teléfono no puede exceder 20 caracteres"
        ),
        metadata={"description": "Teléfono de contacto (opcional)"},
    )
