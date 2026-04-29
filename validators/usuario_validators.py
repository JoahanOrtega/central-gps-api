"""
Validator para crear usuario completo desde el wizard del Panel ERP.

Diferencias respecto a CreateEmpresaAdminSchema:
  - Permite elegir rol (admin_empresa o usuario), no es siempre admin.
  - Acepta sección de restricciones (días/horas/grupos/cliente/días consulta).
  - Acepta sección de permisos granulares (lista de id_permiso a asignar).

El schema valida formato; las reglas de negocio (unicidad de usuario,
existencia de empresa, rol válido) se validan en el service.
"""

from marshmallow import Schema, fields, validate, validates_schema, ValidationError


# ─── Constantes de validación ────────────────────────────────────────────────
# Centralizar los límites permite cambiarlos en un solo lugar y documentarlos.
# Espejean los límites de las columnas en t_usuarios + reglas de negocio.

USUARIO_MIN_LENGTH = 3
USUARIO_MAX_LENGTH = 100
CLAVE_MIN_LENGTH = 8
CLAVE_MAX_LENGTH = 128
NOMBRE_MIN_LENGTH = 2
NOMBRE_MAX_LENGTH = 200
EMAIL_MAX_LENGTH = 150
TELEFONO_MAX_LENGTH = 50

# Roles válidos para creación desde el wizard. Excluimos sudo_erp porque
# es un rol del sistema interno (Anthropic/devs), no se debe poder crear
# desde la UI ni siquiera por un sudo_erp existente.
ROLES_PERMITIDOS_CREACION = ("admin_empresa", "usuario")

# Días de la semana válidos para dias_acceso. Convención del legacy:
# string con códigos separados por comas, ej. "L,M,X,J,V" o "L,M,X,J,V,S,D".
# Acoplado al formato que entiende la lógica de auth (no validado aquí).
DIAS_VALIDOS = frozenset({"L", "M", "X", "J", "V", "S", "D"})


def _validate_dias_acceso(value):
    """
    Valida que dias_acceso sea un string con códigos válidos separados por comas.

    Aceptado:
      ""              → sin restricción (acceso todos los días)
      "L,M,X,J,V"     → solo días laborales
      "S,D"           → solo fines de semana

    Rechazado:
      "Lunes,Martes"  → códigos completos en lugar de letras
      "L,Z"           → códigos no reconocidos
      "L,M,L"         → duplicados
    """
    if not value:
        # Permitir vacío — significa "sin restricción de días".
        return

    partes = [d.strip() for d in value.split(",")]

    # Verificar que no hay duplicados.
    if len(partes) != len(set(partes)):
        raise ValidationError("dias_acceso contiene días duplicados")

    # Verificar que cada parte es un código válido.
    invalidos = [p for p in partes if p not in DIAS_VALIDOS]
    if invalidos:
        raise ValidationError(
            f"Días inválidos: {', '.join(invalidos)}. "
            f"Usa códigos: L, M, X, J, V, S, D"
        )


# ─── Sub-schemas ──────────────────────────────────────────────────────────────
# Separamos las 3 secciones del wizard en sub-schemas para que la estructura
# del JSON refleje la mental del usuario. El frontend mandará exactamente
# este shape — los desarrolladores que consuman este endpoint en el futuro
# leerán algo claro: { datos, restricciones, permisos }.

class _DatosSchema(Schema):
    """
    Sección "Datos Generales" del wizard (Paso 1).

    Campos obligatorios para que el usuario pueda autenticarse:
      - usuario: el login (único en t_usuarios).
      - clave: contraseña en texto plano (se hashea con bcrypt en service).
      - nombre: nombre real visible en UI.
      - rol: admin_empresa o usuario.

    Campos opcionales (perfil completo):
      - email, telefono.
    """

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
            ROLES_PERMITIDOS_CREACION,
            error=f"Rol inválido. Permitidos: {', '.join(ROLES_PERMITIDOS_CREACION)}",
        ),
    )

    email = fields.Email(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=EMAIL_MAX_LENGTH,
            error=f"El email no puede exceder {EMAIL_MAX_LENGTH} caracteres",
        ),
    )

    telefono = fields.Str(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Length(
            max=TELEFONO_MAX_LENGTH,
            error=f"El teléfono no puede exceder {TELEFONO_MAX_LENGTH} caracteres",
        ),
    )


class _RestriccionesSchema(Schema):
    """
    Sección "Restricciones" del wizard (Paso 2).

    Todos los campos son opcionales — un usuario puede crearse sin
    restricciones (el legacy lo permitía). Si vienen, se aplican como:

      - dias_acceso/hora_inicio/hora_fin: ventana de login permitida.
        El backend de auth valida estos campos al hacer login y rechaza
        si está fuera del rango configurado.

      - id_grupo_unidades: limita las unidades visibles para este usuario.
        Si se asigna, solo verá unidades del grupo en mapas y catálogos.

      - id_cliente: convierte al usuario en una "cuenta espejo" de un
        cliente — solo verá información relacionada con ese cliente.

      - dias_consulta: limita qué tan atrás puede consultar movimientos
        históricos. 0 = sin límite (default), N = últimos N días.
    """

    class Meta:
        unknown = "EXCLUDE"

    dias_acceso = fields.Str(
        required=False,
        load_default="",
        allow_none=True,
        validate=_validate_dias_acceso,
    )

    # Time fields acepta formato ISO HH:MM:SS o HH:MM. marshmallow lo
    # parsea a datetime.time. Si viene None, se usa el default del
    # schema en BD (00:00:00 / 23:59:59).
    hora_inicio_acceso = fields.Time(
        required=False,
        load_default=None,
        allow_none=True,
    )

    hora_fin_acceso = fields.Time(
        required=False,
        load_default=None,
        allow_none=True,
    )

    id_grupo_unidades = fields.Int(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Range(
            min=1,
            error="id_grupo_unidades debe ser positivo",
        ),
    )

    id_cliente = fields.Int(
        required=False,
        load_default=None,
        allow_none=True,
        validate=validate.Range(
            min=1,
            error="id_cliente debe ser positivo",
        ),
    )

    dias_consulta = fields.Int(
        required=False,
        load_default=0,
        validate=validate.Range(
            min=0,
            max=3650,  # 10 años — más allá no tiene sentido operativo
            error="dias_consulta debe ser entre 0 (sin límite) y 3650",
        ),
    )

    @validates_schema
    def _validate_horas_consistentes(self, data, **_kwargs):
        """
        Valida que hora_inicio < hora_fin si ambas vienen.

        Si solo viene una, no podemos validar la consistencia y dejamos
        pasar — el service usará el default de BD para la que falta.
        """
        inicio = data.get("hora_inicio_acceso")
        fin = data.get("hora_fin_acceso")

        if inicio is not None and fin is not None and inicio >= fin:
            raise ValidationError(
                "La hora de inicio debe ser anterior a la hora de fin",
                field_name="hora_fin_acceso",
            )


class _PermisosSchema(Schema):
    """
    Sección "Permisos" del wizard (Paso 3).

    Lista de id_permiso a asignar al usuario en r_usuario_permisos.
    Puede venir vacía si el usuario hereda todos los permisos del rol
    (admin_empresa) o si simplemente no tiene permisos granulares
    extra (usuario).

    El frontend ya envía la lista resuelta — si el usuario eligió
    "Acceso total", el frontend envía los IDs de TODOS los permisos.
    Si eligió "Solo lectura", solo los IDs de los `*.ver`. Si eligió
    "Personalizar", la lista que el usuario marcó manualmente.

    Validamos solo:
      - Que sean enteros positivos.
      - Que no haya duplicados.

    No validamos que los id_permiso existan en t_permisos — eso lo hace
    el service consultando la BD. Validar aquí requeriría un SELECT que
    el service ya hace de todos modos.
    """

    class Meta:
        unknown = "EXCLUDE"

    id_permisos = fields.List(
        fields.Int(
            validate=validate.Range(
                min=1,
                error="Cada id_permiso debe ser un entero positivo",
            ),
        ),
        required=False,
        load_default=[],
    )

    @validates_schema
    def _validate_no_duplicados(self, data, **_kwargs):
        """
        Rechaza listas con id_permiso duplicados.

        Sin esta validación, un cliente buggy podría mandar [1, 1, 2] y
        el INSERT de relaciones haría 3 filas (o fallaría por la PK
        compuesta). Mejor rechazar al inicio con mensaje claro.
        """
        ids = data.get("id_permisos", [])
        if len(ids) != len(set(ids)):
            raise ValidationError(
                "La lista de permisos contiene IDs duplicados",
                field_name="id_permisos",
            )


# ─── Schema principal ─────────────────────────────────────────────────────────
class CreateUsuarioCompletoSchema(Schema):
    """
    Valida el payload de POST /admin-erp/empresas/<id_empresa>/usuarios-completo.

    Estructura del payload esperado:
      {
        "datos":         { usuario, clave, nombre, rol, email?, telefono? },
        "restricciones": { dias_acceso?, hora_inicio?, hora_fin?, ... },
        "permisos":      { id_permisos: [int, ...] }
      }

    Diseño:
      - Tres sub-schemas anidados reflejan los 3 pasos del wizard.
      - El service recibe el dict ya validado y lo procesa por secciones.
      - Si el frontend manda "permisos" vacío (usuario sin permisos extra),
        es válido — la lista [] se persiste como "ninguna asignación
        granular".

    Defensa en profundidad:
      - unknown = "EXCLUDE" en cada nivel descarta campos no declarados.
      - Esto es CRÍTICO en el sub-schema "datos": evita que el cliente
        mande {"id_rol": <id_de_sudo_erp>} para escalar privilegios.
        El campo legítimo es "rol" (string), que pasa por validate.OneOf.
    """

    class Meta:
        unknown = "EXCLUDE"

    datos = fields.Nested(_DatosSchema, required=True)
    restricciones = fields.Nested(_RestriccionesSchema, load_default=dict)
    permisos = fields.Nested(_PermisosSchema, load_default=dict)