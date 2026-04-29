import logging
from flask import Blueprint, request, jsonify
from utils.auth_guard import (
    sudo_erp_required,
    permiso_required,
    validate_empresa_access,
)
from utils.validation import validate_payload
from validators import CreateEmpresaAdminSchema, CreateUsuarioCompletoSchema
from services import erp_service

logger = logging.getLogger(__name__)

# Blueprint con prefijo /admin-erp para todas las rutas del panel ERP
erp_bp = Blueprint("erp", __name__, url_prefix="/admin-erp")


# ── Helpers de validación ──────────────────────────────────────────────────────


def _parse_limit(
    raw: str | None, default: int = 100, max_value: int = 500
) -> tuple[int | None, str | None]:
    """
    Parsea y valida el parámetro 'limit' de una query string.

    Reglas:
      - Si no se provee, usa el default.
      - Si no es un entero válido, retorna error.
      - El valor se clampea entre 1 y max_value — nunca permite valores
        fuera de rango que puedan devolver tablas enteras o valores negativos.

    Args:
        raw:       Valor crudo del query param (puede ser None).
        default:   Valor por defecto si no se provee el parámetro.
        max_value: Cota máxima permitida.

    Returns:
        Tupla (valor_int, None) en éxito o (None, mensaje_error) en fallo.
    """
    if raw is None:
        return default, None

    try:
        value = int(raw)
    except (ValueError, TypeError):
        return (
            None,
            f"El parámetro 'limit' debe ser un número entero, se recibió: '{raw}'",
        )

    # Clampear al rango [1, max_value] — silenciosamente, sin error.
    # Valores negativos o cero no tienen sentido semántico.
    # Valores superiores al máximo se recortan para proteger la BD.
    return max(1, min(value, max_value)), None


# ── EMPRESAS ───────────────────────────────────────────────────────────────────


@erp_bp.route("/empresas", methods=["GET"])
@sudo_erp_required
def list_companies():
    """Dashboard: resumen de todas las empresas."""
    try:
        data, error = erp_service.get_all_companies()
        if error:
            # No exponer el mensaje interno al cliente — puede contener
            # nombres de tablas, queries o información del schema de BD.
            logger.error("Error en GET /admin-erp/empresas: %s", error)
            return jsonify({"error": "No fue posible obtener las empresas"}), 500
        return jsonify(data), 200
    except Exception as exc:
        logger.error("Error en GET /admin-erp/empresas: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@erp_bp.route("/empresas", methods=["POST"])
@sudo_erp_required
def create_company():
    """Crear una nueva empresa."""
    try:
        body = request.get_json(silent=True) or {}

        # Validar campo obligatorio antes de tocar el servicio
        nombre = body.get("nombre", "").strip()
        if not nombre:
            return jsonify({"error": "El campo 'nombre' es obligatorio"}), 400

        data, error = erp_service.create_company(
            nombre=nombre,
            direccion=body.get("direccion"),
            telefonos=body.get("telefonos"),
            lat=body.get("lat"),
            lng=body.get("lng"),
            logo=body.get("logo"),
            id_usuario_registro=int(request.user["sub"]),
        )
        if error:
            logger.error("Error en POST /admin-erp/empresas: %s", error)
            return jsonify({"error": "No fue posible crear la empresa"}), 500
        return jsonify(data), 201

    except Exception as exc:
        logger.error("Error en POST /admin-erp/empresas: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@erp_bp.route("/empresas/<int:id_empresa>", methods=["PUT"])
@sudo_erp_required
def update_company(id_empresa):
    """Actualizar datos de una empresa."""
    try:
        body = request.get_json(silent=True) or {}
        data, error = erp_service.update_company(
            id_empresa=id_empresa,
            datos=body,
            id_usuario_cambio=int(request.user["sub"]),
        )
        if error:
            logger.error("Error en PUT /admin-erp/empresas/%s: %s", id_empresa, error)
            return jsonify({"error": "No fue posible actualizar la empresa"}), 500
        return jsonify(data), 200

    except Exception as exc:
        logger.error(
            "Error en PUT /admin-erp/empresas/%s: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@erp_bp.route("/empresas/<int:id_empresa>/status", methods=["PATCH"])
@sudo_erp_required
def toggle_company(id_empresa):
    """Activar o suspender una empresa. Body: { status: 0|1 }"""
    try:
        body = request.get_json(silent=True) or {}
        status = body.get("status")

        if status not in (0, 1):
            return jsonify({"error": "El campo 'status' debe ser 0 o 1"}), 400

        data, error = erp_service.toggle_company_status(
            id_empresa=id_empresa,
            status=status,
            id_usuario_cambio=int(request.user["sub"]),
        )
        if error:
            logger.error(
                "Error en PATCH /admin-erp/empresas/%s/status: %s", id_empresa, error
            )
            return (
                jsonify({"error": "No fue posible cambiar el status de la empresa"}),
                500,
            )
        return jsonify(data), 200

    except Exception as exc:
        logger.error(
            "Error en PATCH /admin-erp/empresas/%s/status: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# ── USUARIOS Y ADMINS DE EMPRESA ──────────────────────────────────────────────


@erp_bp.route("/empresas/<int:id_empresa>/usuarios", methods=["GET"])
@sudo_erp_required
def list_company_users(id_empresa):
    """Lista todos los usuarios de una empresa."""
    try:
        data, error = erp_service.get_users_by_company(id_empresa)
        if error:
            logger.error(
                "Error en GET /admin-erp/empresas/%s/usuarios: %s", id_empresa, error
            )
            return jsonify({"error": "No fue posible obtener los usuarios"}), 500
        return jsonify(data), 200

    except Exception as exc:
        logger.error(
            "Error en GET /admin-erp/empresas/%s/usuarios: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# Mapeo de códigos de error del service a códigos HTTP.
# Se separa del service (que no debe conocer HTTP) y permite que las rutas
# ofrezcan mensajes distintos sin duplicar lógica de negocio.
_ADMIN_CREATE_ERROR_STATUS = {
    "EMPRESA_NOT_FOUND": 404,
    "USERNAME_TAKEN": 409,
    "ROL_NOT_CONFIGURED": 500,
    "DATABASE_ERROR": 500,
}


@erp_bp.route("/empresas/<int:id_empresa>/usuarios", methods=["POST"])
@sudo_erp_required
def create_company_admin(id_empresa):
    """
    Crea un usuario admin_empresa dentro de una empresa existente.

    Solo el sudo_erp puede ejecutar este endpoint. El usuario creado:
      - Tiene rol 'admin_empresa' en t_usuarios (fuente de verdad)
      - Queda asociado a la empresa (id_empresa en t_usuarios y registro
        histórico en r_empresa_usuarios)
      - Recibe automáticamente los permisos de r_rol_permiso para admin_empresa
        (todos los permisos EXCEPTO cund3 = crear unidades)
      - Puede iniciar sesión y acceder a su panel inmediatamente

    Body JSON esperado (validado por CreateEmpresaAdminSchema):
      {
        "usuario":  "juanperez",          // login, único en t_usuarios
        "clave":    "password123",        // 8-128 chars, se hashea con bcrypt
        "nombre":   "Juan Pérez",         // nombre visible
        "email":    "juan@cliente.com",   // opcional
        "telefono": "+52 55 1234 5678"    // opcional
      }

    Respuestas:
      201 { "message": "...", "usuario": { id_usuario, usuario, nombre, ... } }
      404 { "error": "La empresa no existe o está inactiva" }
      409 { "error": "El nombre de usuario ya está en uso" }
      422 { "error": "Datos inválidos", "fields": {...} }  ← marshmallow
      500 { "error": "..." }
    """
    data = request.get_json(silent=True)

    # Validar payload antes de tocar la BD — fail fast.
    # `data` queda filtrado: solo usuario, clave, nombre, email, telefono.
    # Cualquier intento de mandar id_rol, status, etc. se descarta aquí.
    data, validation_error = validate_payload(CreateEmpresaAdminSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_usuario_registro = int(request.user["sub"])

        result, error = erp_service.create_empresa_admin(
            id_empresa=id_empresa,
            datos_usuario=data,
            id_usuario_registro=id_usuario_registro,
        )

        if error:
            code = error.get("code", "DATABASE_ERROR")
            status = _ADMIN_CREATE_ERROR_STATUS.get(code, 500)

            # Los errores con código conocido (404, 409) son de negocio y se
            # devuelven con el mensaje tal cual (ya vienen sanitizados).
            # Los 500 (ROL_NOT_CONFIGURED, DATABASE_ERROR) se logean pero al
            # cliente se le devuelve un mensaje genérico para no filtrar
            # detalles internos como nombres de tablas o queries.
            if status >= 500:
                logger.error(
                    "Error 500 en POST /admin-erp/empresas/%s/usuarios (%s): %s",
                    id_empresa,
                    code,
                    error.get("message"),
                )
                return (
                    jsonify({"error": "No fue posible crear el usuario"}),
                    status,
                )

            return jsonify({"error": error["message"]}), status

        return (
            jsonify(
                {
                    "message": "Usuario admin de empresa creado correctamente",
                    "usuario": result,
                }
            ),
            201,
        )

    except Exception as exc:
        logger.error(
            "Error en POST /admin-erp/empresas/%s/usuarios: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@erp_bp.route(
    "/empresas/<int:id_empresa>/usuarios/<int:id_usuario>/admin", methods=["PATCH"]
)
@sudo_erp_required
def set_admin(id_empresa, id_usuario):
    """
    Promueve o revoca el rol admin de empresa a un usuario.
    Body: { es_admin: true|false }
    """
    try:
        body = request.get_json(silent=True) or {}
        es_admin = body.get("es_admin")

        if es_admin is None:
            return jsonify({"error": "El campo 'es_admin' es obligatorio"}), 400

        data, error = erp_service.set_admin_empresa(
            id_usuario=id_usuario,
            id_empresa=id_empresa,
            es_admin=bool(es_admin),
            id_usuario_cambio=int(request.user["sub"]),
        )
        if error:
            logger.error("Error en PATCH .../usuarios/%s/admin: %s", id_usuario, error)
            return (
                jsonify({"error": "No fue posible actualizar el rol de administrador"}),
                500,
            )
        return jsonify(data), 200

    except Exception as exc:
        logger.error(
            "Error en PATCH .../usuarios/%s/admin: %s",
            id_usuario,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# ── PERMISOS DEL SISTEMA ───────────────────────────────────────────────────────


@erp_bp.route("/permisos", methods=["GET"])
@sudo_erp_required
def list_permissions():
    """Catálogo de todos los permisos del sistema."""
    try:
        data, error = erp_service.get_all_permissions()
        if error:
            logger.error("Error en GET /admin-erp/permisos: %s", error)
            return jsonify({"error": "No fue posible obtener los permisos"}), 500
        return jsonify(data), 200

    except Exception as exc:
        logger.error("Error en GET /admin-erp/permisos: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


@erp_bp.route("/permisos", methods=["POST"])
@sudo_erp_required
def create_permission():
    """Agregar un nuevo permiso al catálogo."""
    try:
        body = request.get_json(silent=True) or {}

        clave = body.get("clave", "").strip()
        nombre = body.get("nombre", "").strip()

        if not clave or not nombre:
            return (
                jsonify({"error": "Los campos 'clave' y 'nombre' son obligatorios"}),
                400,
            )

        data, error = erp_service.create_permission(
            clave=clave,
            nombre=nombre,
            modulo=body.get("modulo"),
            descripcion=body.get("descripcion"),
        )
        if error:
            logger.error("Error en POST /admin-erp/permisos: %s", error)
            return jsonify({"error": "No fue posible crear el permiso"}), 500
        return jsonify(data), 201

    except Exception as exc:
        logger.error("Error en POST /admin-erp/permisos: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# ── AUDITORÍA ──────────────────────────────────────────────────────────────────


# Entidades válidas para filtrar el log de auditoría.
# Lista explícita para prevenir que se pasen valores arbitrarios al servicio
# que podrían causar comportamientos inesperados en la query de BD.
_ENTIDADES_VALIDAS = frozenset({"empresa", "usuario", "permiso", "unidad", "poi"})

# Límite máximo de registros de auditoría por request.
# Protege contra queries que devuelvan la tabla completa de una vez.
_AUDIT_LIMIT_MAX = 500


@erp_bp.route("/auditoria", methods=["GET"])
@sudo_erp_required
def get_audit():
    """
    Log de auditoría del sistema.

    Query params opcionales:
      ?limit=100   → número de registros (1–500, default 100)
      ?entidad=empresa → filtrar por tipo de entidad
    """
    try:
        # Parsear y validar limit — previene 500 por valor no numérico
        # y evita queries sin cota que devuelvan toda la tabla
        limit, limit_error = _parse_limit(
            request.args.get("limit"),
            default=100,
            max_value=_AUDIT_LIMIT_MAX,
        )
        if limit_error:
            return jsonify({"error": limit_error}), 400

        # Validar entidad contra lista blanca — previene pasar valores
        # arbitrarios al servicio que podrían afectar la query de BD
        entidad_raw = request.args.get("entidad", "").strip().lower() or None
        if entidad_raw and entidad_raw not in _ENTIDADES_VALIDAS:
            return (
                jsonify(
                    {
                        "error": (
                            f"Entidad '{entidad_raw}' no válida. "
                            f"Valores permitidos: {', '.join(sorted(_ENTIDADES_VALIDAS))}"
                        )
                    }
                ),
                400,
            )

        data, error = erp_service.get_audit_log(limit=limit, entidad=entidad_raw)
        if error:
            logger.error("Error en GET /admin-erp/auditoria: %s", error)
            return jsonify({"error": "No fue posible obtener el log de auditoría"}), 500
        return jsonify(data), 200

    except Exception as exc:
        logger.error("Error en GET /admin-erp/auditoria: %s", repr(exc), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500


# ─── CREAR USUARIO COMPLETO (wizard del Panel ERP) ────────────────────────────


# Mapping de errores del service a códigos HTTP.
# Centralizado como dict para que sea fácil ver de un vistazo qué
# error de negocio mapea a qué status. Si en el futuro se agrega un
# nuevo "code" en el service, basta con añadir la línea aquí.
#
# Códigos NO incluidos aquí caen al default 500 — eso es deliberado:
# si llega un code desconocido, lo más seguro es tratarlo como error
# interno hasta clasificarlo explícitamente.
_USUARIO_COMPLETO_ERROR_STATUS = {
    "EMPRESA_NOT_FOUND": 404,
    "USERNAME_TAKEN": 409,  # conflict — recurso duplicado
    "INVALID_PERMISSIONS": 422,  # unprocessable — datos incoherentes
    "ROL_NOT_CONFIGURED": 500,  # config del sistema, no del cliente
}


@erp_bp.route("/empresas/<int:id_empresa>/usuarios-completo", methods=["POST"])
@permiso_required("usuarios.editar")
def create_company_user_complete(id_empresa):
    """
    Crea un usuario completo (rol + restricciones + permisos granulares)
    desde el wizard del Panel ERP.

    Autorización (jerárquica):
      - sudo_erp:      bypass de permisos, puede en cualquier empresa.
      - admin_empresa: tiene 'usuarios.editar' por defecto (heredado del
                       rol en r_rol_permisos), puede en SU empresa.
      - usuario:       solo si el sudo_erp o un admin_empresa le asignó
                       'usuarios.editar' explícitamente, puede en SU empresa.

      validate_empresa_access garantiza que admin_empresa y usuario solo
      puedan crear usuarios en su propia empresa. sudo_erp pasa siempre.

    Body JSON esperado (validado por CreateUsuarioCompletoSchema):
      {
        "datos": {
          "usuario":  "juanperez",
          "clave":    "password123",
          "nombre":   "Juan Pérez",
          "rol":      "usuario",          // o "admin_empresa"
          "email":    "juan@cliente.com", // opcional
          "telefono": "+52 55 1234 5678"  // opcional
        },
        "restricciones": {
          "dias_acceso":         "L,M,X,J,V",  // opcional
          "hora_inicio_acceso":  "08:00",      // opcional
          "hora_fin_acceso":     "18:00",      // opcional
          "id_grupo_unidades":   3,            // opcional
          "id_cliente":          15,           // opcional
          "dias_consulta":       30            // opcional, default 0
        },
        "permisos": {
          "id_permisos": [1, 3, 5, 7]   // opcional, default []
        }
      }

    Respuestas:
      201 → {"message", "usuario": {...}}
      403 → sin permiso 'usuarios.editar' o intentando otra empresa
      404 → empresa no existe / inactiva
      409 → nombre de usuario ya tomado
      422 → datos inválidos por marshmallow o INVALID_PERMISSIONS
      500 → error interno (rol no configurado, BD caída, etc.)
    """
    # Validación de empresa: capa adicional al permiso 'usuarios.editar'.
    # permiso_required dice "puedes hacer la acción", pero NO dice "en qué
    # empresas". Sin esta validación, un admin_empresa de la empresa 5
    # con permiso usuarios.editar podría crear usuarios en empresa 9.
    if not validate_empresa_access(id_empresa, request.user):
        return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

    data = request.get_json(silent=True)

    # Validar payload contra el schema completo (3 secciones anidadas).
    # El schema descarta cualquier campo no declarado en cualquiera de los
    # niveles — defensa en profundidad contra escalación de privilegios
    # vía campos como id_rol, status o id_empresa en datos.
    data, validation_error = validate_payload(CreateUsuarioCompletoSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_usuario_registro = int(request.user["sub"])

        result, error = erp_service.create_usuario_completo(
            id_empresa=id_empresa,
            payload=data,
            id_usuario_registro=id_usuario_registro,
        )

        if error:
            code = error.get("code", "DATABASE_ERROR")
            status = _USUARIO_COMPLETO_ERROR_STATUS.get(code, 500)

            # Errores 5xx: log detallado pero mensaje genérico al cliente.
            # No queremos filtrar detalles internos como nombres de tablas
            # o queries fallidas. Errores 4xx (negocio) sí van con el
            # mensaje original porque son útiles para el usuario.
            if status >= 500:
                logger.error(
                    "Error 500 en POST /admin-erp/empresas/%s/usuarios-completo (%s): %s",
                    id_empresa,
                    code,
                    error.get("message"),
                )
                return (
                    jsonify({"error": "No fue posible crear el usuario"}),
                    status,
                )

            return jsonify({"error": error["message"], "code": code}), status

        return (
            jsonify(
                {
                    "message": "Usuario creado correctamente",
                    "usuario": result,
                }
            ),
            201,
        )

    except Exception as exc:
        logger.error(
            "Error en POST /admin-erp/empresas/%s/usuarios-completo: %s",
            id_empresa,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500
