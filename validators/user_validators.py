"""
Validators para el módulo Catálogos > Usuarios.

Schemas:
  - CreateUserSchema: payload del wizard al CREAR un usuario.
  - UpdateUserSchema: payload del wizard al EDITAR un usuario.
  - StatusUserSchema:  payload simple para inhabilitar/reactivar.

Diseño:
  - UpdateUserSchema espeja CreateUserSchema con todos los campos
    OPCIONALES y SIN load_default — el patrón PATCH del proyecto.
  - El campo `usuario` (login) NO es editable: se omite en UpdateUserSchema.
  - El campo `clave` NO es editable desde el wizard: si el sudo_erp
    quiere resetear, usa el endpoint exclusivo /admin-erp/...

Reusa la misma estructura anidada de 3 secciones (datos, restricciones,
permisos) para consistencia con el flujo del wizard del frontend.
"""

from marshmallow import Schema, fields, validate, validates_schema, ValidationError

# ─── Constantes de validación ────────────────────────────────────────────────
# Espejean los límites del schema y de t_usuarios. Centralizar permite
# cambiar el límite en un solo lugar y mantener mensajes consistentes.
USUARIO_MIN_LENGTH = 3
USUARIO_MAX_LENGTH = 100
CLAVE_MIN_LENGTH = 8
CLAVE_MAX_LENGTH = 128
NOMBRE_MIN_LENGTH = 2
NOMBRE_MAX_LENGTH = 200
EMAIL_MAX_LENGTH = 150
TELEFONO_MAX_LENGTH = 50

# Roles permitidos para creación desde el wizard. sudo_erp se excluye
# explícitamente — es un rol de sistema que NO debe poder crearse desde
# la UI ni siquiera por otro sudo_erp. Si en el futuro se necesita un
# segundo sudo_erp, se hace por SQL directo con auditoría manual.
ROLES_CREABLES = ("admin_empresa", "usuario")

# Días válidos para dias_acceso. Convención del legacy:
# string con códigos separados por comas, ej. "L,M,X,J,V".
DIAS_VALIDOS = frozenset({"L", "M", "X", "J", "V", "S", "D"})


def _validate_dias_acceso(value):
    """
    Valida formato de dias_acceso.

    Aceptado: ""  (sin restricción) / "L,M,X,J,V" (códigos válidos).
    Rechazado: códigos inválidos, duplicados, formato malformado.
    """
    if not value:
        return  # vacío = sin restricción de días, válido

    partes = [d.strip() for d in value.split(",")]

    if len(partes) != len(set(partes)):
        raise ValidationError("dias_acceso contiene días duplicados")

    invalidos = [p for p in partes if p not in DIAS_VALIDOS]
    if invalidos:
        raise ValidationError(
            f"Días inválidos: {', '.join(invalidos)}. "
            f"Usa códigos: L, M, X, J, V, S, D"
        )


# ─── Sub-schemas reusables ────────────────────────────────────────────────────


class _DatosCreateSchema(Schema):
    """Sección 'Datos' del payload de CREACIÓN."""

    class Meta:
        unknown = "EXCLUDE"

    usuario = fields.Str(
        required=True,
        validate=validate.Length(
            min=USUARIO_MIN_LENGTH,
            max=USUARIO_MAX_LENGTH,
            error=f"El usuario debe tener entre {USUARIO_MIN_LENGTH} y {USUARIO_MAX_LENGTH} caracteres",
        ),
    )
    clave = fields.Str(
        required=True,
        validate=validate.Length(
            min=CLAVE_MIN_LENGTH,
            max=CLAVE_MAX_LENGTH,
            error=f"La contraseña debe tener entre {CLAVE_MIN_LENGTH} y {CLAVE_MAX_LENGTH} caracteres",
        ),
    )
    nombre = fields.Str(
        required=True,
        validate=validate.Length(
            min=NOMBRE_MIN_LENGTH,
            max=NOMBRE_MAX_LENGTH,
            error=f"El nombre debe tener entre {NOMBRE_MIN_LENGTH} y {NOMBRE_MAX_LENGTH} caracteres",
        ),
    )
    rol = fields.Str(
        required=True,
        validate=validate.OneOf(
            ROLES_CREABLES,
            error=f"Rol inválido. Permitidos: {', '.join(ROLES_CREABLES)}",
        ),
    )
    email = fields.Email(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=EMAIL_MAX_LENGTH),
    )
    telefono = fields.Str(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(max=TELEFONO_MAX_LENGTH),
    )


class _DatosUpdateSchema(Schema):
    """
    Sección 'Datos' del payload de EDICIÓN.

    Diferencias respecto a _DatosCreateSchema:
      - usuario: NO está. El login es inmutable — cambiarlo rompería
        auditoría histórica y referencias en logs.
      - clave: NO está. La contraseña no se cambia desde el wizard
        para no permitir cambios silenciosos. Si el sudo_erp quiere
        resetear, usa el endpoint exclusivo /admin-erp/.../reset-password.
      - Todos los campos restantes son opcionales (PATCH parcial).
        SIN load_default para que el service detecte qué cambió.
    """

    class Meta:
        unknown = "EXCLUDE"

    nombre = fields.Str(
        validate=validate.Length(min=NOMBRE_MIN_LENGTH, max=NOMBRE_MAX_LENGTH),
    )
    rol = fields.Str(
        validate=validate.OneOf(ROLES_CREABLES),
    )
    email = fields.Email(
        allow_none=True, validate=validate.Length(max=EMAIL_MAX_LENGTH)
    )
    telefono = fields.Str(
        allow_none=True, validate=validate.Length(max=TELEFONO_MAX_LENGTH)
    )


class _RestriccionesSchema(Schema):
    """
    Sección 'Restricciones' (común a creación y edición).

    Todos los campos son opcionales en ambos flujos. En creación,
    valores omitidos toman defaults sensatos (sin restricción de días,
    horario completo). En edición, valores omitidos NO se tocan.
    """

    class Meta:
        unknown = "EXCLUDE"

    dias_acceso = fields.Str(
        load_default="",
        allow_none=True,
        validate=_validate_dias_acceso,
    )
    hora_inicio_acceso = fields.Time(load_default=None, allow_none=True)
    hora_fin_acceso = fields.Time(load_default=None, allow_none=True)
    id_grupo_unidades = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=1),
    )
    id_cliente = fields.Int(
        load_default=None,
        allow_none=True,
        validate=validate.Range(min=1),
    )
    dias_consulta = fields.Int(
        load_default=0,
        validate=validate.Range(min=0, max=3650, error="dias_consulta debe ser 0–3650"),
    )

    @validates_schema
    def _validate_horas(self, data, **_kwargs):
        """Valida que hora_inicio < hora_fin si ambas vienen."""
        inicio = data.get("hora_inicio_acceso")
        fin = data.get("hora_fin_acceso")
        if inicio is not None and fin is not None and inicio >= fin:
            raise ValidationError(
                "La hora de inicio debe ser anterior a la hora de fin",
                field_name="hora_fin_acceso",
            )


class _PermisosSchema(Schema):
    """
    Sección 'Permisos' (común a creación y edición).

    Lista de id_permiso. En edición, [] significa "desasignar todos los
    permisos granulares" — el usuario quedaría con solo los heredados
    del rol. Esto es intencional: el frontend siempre envía la lista
    final completa, no diffs.
    """

    class Meta:
        unknown = "EXCLUDE"

    id_permisos = fields.List(
        fields.Int(validate=validate.Range(min=1)),
        load_default=[],
    )

    @validates_schema
    def _validate_no_duplicados(self, data, **_kwargs):
        ids = data.get("id_permisos", [])
        if len(ids) != len(set(ids)):
            raise ValidationError(
                "La lista de permisos contiene IDs duplicados",
                field_name="id_permisos",
            )


# ─── Schemas principales ──────────────────────────────────────────────────────


class CreateUserSchema(Schema):
    """
    Payload del wizard al CREAR un usuario.

    Estructura (3 secciones anidadas espejean el wizard del frontend):
      {
        "datos":         { usuario, clave, nombre, rol, email?, telefono? },
        "restricciones": { dias_acceso?, hora_inicio?, hora_fin?, ... },
        "permisos":      { id_permisos: [int, ...] }
      }

    Notas:
      - 'datos' es required — al crear NECESITAMOS usuario, clave, nombre, rol.
      - 'restricciones' tiene load_default=dict — un cliente puede mandar
        solo la sección "datos" y dejar restricciones/permisos vacías.
      - 'permisos' load_default=dict — usuario sin permisos extra hereda
        solo del rol.
    """

    class Meta:
        unknown = "EXCLUDE"

    datos = fields.Nested(_DatosCreateSchema, required=True)
    restricciones = fields.Nested(_RestriccionesSchema, load_default=dict)
    permisos = fields.Nested(_PermisosSchema, load_default=dict)


class UpdateUserSchema(Schema):
    """
    Payload del wizard al EDITAR un usuario.

    Diferencias respecto a CreateUserSchema:
      - Las 3 secciones son OPCIONALES — el cliente puede mandar solo
        las secciones que cambiaron (ej. solo "permisos" si el rol y
        restricciones se quedan igual).
      - Dentro de cada sección, los campos también son opcionales.
      - SIN load_default a nivel raíz: si una sección no viene, el
        service NO la toca en BD.

    Casos de uso:
      - Renombrar usuario:        { datos: { nombre: "Nuevo nombre" } }
      - Cambiar rol:              { datos: { rol: "admin_empresa" } }
      - Restringir horario:       { restricciones: { hora_inicio: "08:00", hora_fin: "18:00" } }
      - Reasignar permisos:       { permisos: { id_permisos: [1,3,5] } }
      - Desasignar todos:         { permisos: { id_permisos: [] } }
      - Múltiples cambios:        cualquier combinación de las anteriores
    """

    class Meta:
        unknown = "EXCLUDE"

    # Sin load_default → si el cliente NO manda esta clave, no aparece
    # en data y el service sabe que no debe tocar esa sección.
    datos = fields.Nested(_DatosUpdateSchema)
    restricciones = fields.Nested(_RestriccionesSchema)
    permisos = fields.Nested(_PermisosSchema)


class StatusUserSchema(Schema):
    """
    Payload simple para inhabilitar/reactivar un usuario.

    El status es 0 (inhabilitado) o 1 (activo).

    NOTA: el endpoint público de catálogos solo permite inhabilitar
    (1 → 0). Reactivar (0 → 1) se hace desde el endpoint exclusivo del
    Panel ERP — porque el catálogo ni siquiera muestra los usuarios
    inhabilitados, así que no hay UI para reactivar.
    """

    class Meta:
        unknown = "EXCLUDE"

    status = fields.Int(
        required=True,
        validate=validate.OneOf(
            [0, 1], error="status debe ser 0 (inhabilitar) o 1 (reactivar)"
        ),
    )
