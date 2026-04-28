from marshmallow import Schema, fields, validate, validates_schema, ValidationError


# ─── Constantes de fortaleza ──────────────────────────────────────────────────
# Nivel "básico" acordado: mínimo 8 caracteres, sin requisitos extra de
# composición. Centralizar el número aquí permite cambiarlo en un solo lugar
# si en el futuro se decide endurecer la política (p.ej. exigir mayúscula y
# número). El validate.Length() del schema lee este valor — no duplicar.
MIN_PASSWORD_LENGTH = 8

# Límite superior alineado con auth_validators.LoginSchema. bcrypt internamente
# trunca passwords a 72 bytes; aceptar más sería honestamente engañar al
# usuario y abrir un vector de DoS (bcrypt es lento por diseño y validar
# passwords de 10 KB satura CPU). 128 chars cubre passphrases razonables.
MAX_PASSWORD_LENGTH = 128


# ─── Base con Meta unknown=EXCLUDE ────────────────────────────────────────────
# Mismo patrón que _BaseAuthSchema en auth_validators.py — duplicar la clase
# base aquí (en vez de importarla) evita un import cruzado entre validators.
# El costo (4 líneas) es menor que el de un acoplamiento innecesario.
class _BasePasswordSchema(Schema):
    class Meta:
        unknown = "EXCLUDE"


class ChangePasswordSchema(_BasePasswordSchema):
    """
    Valida el payload de PATCH /auth/change-password.

    Campos:
      - current_password: la contraseña ACTUAL del usuario (verificada
        en el service contra el hash en BD antes de aceptar el cambio).
      - new_password:     la contraseña nueva. Debe cumplir la longitud
        mínima definida en MIN_PASSWORD_LENGTH.
      - confirm_password: repetición de la nueva. Validada contra
        new_password en validates_schema (validación cruzada).

    Por qué se valida la confirmación en BACKEND aunque el frontend
    también lo haga:
      Defensa en profundidad. Un cliente API que llame al endpoint
      directamente (Postman, script de migración, integración de un
      tercero) no pasa por el formulario del frontend. Si el backend
      no la valida, un POST malformado podría dejar al usuario con una
      contraseña que ni él recuerda haber escrito.

    Por qué new_password tiene validate.Length pero current_password no:
      current_password ya existe en BD — el usuario NO va a poder cambiar
      sus reglas de longitud retroactivamente. Si tiene 6 chars históricos,
      el flujo debe permitirle ingresarla para validarse. La política
      nueva solo aplica a la contraseña que se está creando.
    """

    current_password = fields.Str(
        required=True,
        validate=validate.Length(
            min=1,
            max=MAX_PASSWORD_LENGTH,
            error="La contraseña actual no puede estar vacía",
        ),
        metadata={"description": "Contraseña actual del usuario"},
    )

    new_password = fields.Str(
        required=True,
        validate=validate.Length(
            min=MIN_PASSWORD_LENGTH,
            max=MAX_PASSWORD_LENGTH,
            error=(
                f"La nueva contraseña debe tener al menos "
                f"{MIN_PASSWORD_LENGTH} caracteres"
            ),
        ),
        metadata={"description": "Contraseña nueva — mínimo 8 caracteres"},
    )

    confirm_password = fields.Str(
        required=True,
        validate=validate.Length(
            min=1,
            max=MAX_PASSWORD_LENGTH,
            error="Debes confirmar la contraseña nueva",
        ),
        metadata={"description": "Repetición de la contraseña nueva"},
    )

    @validates_schema
    def _check_passwords_match(self, data, **_kwargs):
        """
        Validación cruzada entre new_password y confirm_password.

        Marshmallow ejecuta esto DESPUÉS de los validadores por campo,
        así que cuando llegamos aquí ya sabemos que ambos vinieron y
        tienen longitud válida. Solo nos toca comparar.

        Si no coinciden, marshmallow lo agrega al dict de errores con
        la clave 'confirm_password' — el frontend pintará el error
        debajo de ese campo (no debajo de 'new_password').
        """
        if data.get("new_password") != data.get("confirm_password"):
            raise ValidationError(
                "Las contraseñas no coinciden",
                field_name="confirm_password",
            )

        # Validación adicional UX: la nueva no puede ser igual a la actual.
        # Sin esto el endpoint aceptaría el "cambio" y el usuario creería
        # haber rotado su credencial cuando en realidad nada cambió —
        # confunde y da falsa sensación de seguridad.
        if data.get("new_password") == data.get("current_password"):
            raise ValidationError(
                "La nueva contraseña debe ser distinta a la actual",
                field_name="new_password",
            )
