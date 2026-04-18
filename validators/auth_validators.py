from marshmallow import Schema, fields, validate


class LoginSchema(Schema):
    """
    Valida el payload de POST /auth/login.

    Validaciones aplicadas:
      - username y password son obligatorios y no pueden estar vacíos
      - username tiene longitud máxima para prevenir payloads gigantes
      - password tiene longitud mínima (no rechazar contraseñas válidas cortas)
        y máxima (prevenir ataques de DoS con bcrypt y contraseñas enormes)

    Seguridad:
      - No se valida el formato del username (puede ser email o nombre de usuario)
      - El mensaje de error no revela si el usuario existe — eso lo hace auth_service
    """

    username = fields.Str(
        required=True,
        validate=[
            validate.Length(
                min=1, max=100, error="El nombre de usuario no puede estar vacío"
            ),
        ],
        metadata={"description": "Nombre de usuario o email"},
    )
    password = fields.Str(
        required=True,
        validate=[
            validate.Length(
                min=1,
                max=128,
                # Limitar a 128 chars previene DoS con bcrypt:
                # bcrypt tiene complejidad O(costo * len(password))
                error="La contraseña no puede estar vacía",
            ),
        ],
        metadata={"description": "Contraseña del usuario"},
    )


class SwitchCompanySchema(Schema):
    """
    Valida el payload de POST /auth/switch-company.

    id_empresa debe ser un entero positivo — nunca cero ni negativo,
    ya que los IDs de BD son SERIAL (empiezan en 1).
    """

    id_empresa = fields.Int(
        required=True,
        validate=validate.Range(
            min=1, error="id_empresa debe ser un número entero positivo"
        ),
        metadata={"description": "ID de la empresa a la que se quiere cambiar"},
    )
