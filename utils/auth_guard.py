from functools import wraps
from flask import request, jsonify
from utils.jwt_handler import decode_jwt


def jwt_required(f):
    """
    Decorador base: verifica que el token JWT sea válido y no haya expirado.

    Si el token es válido, almacena el payload decodificado en request.user
    para que los decoradores y endpoints posteriores puedan leerlo.

    Uso:
        @jwt_required
        def mi_endpoint(): ...
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()

        if not token:
            return jsonify({"error": "Token requerido"}), 401

        try:
            payload = decode_jwt(token)
            request.user = payload
        except Exception:
            # No exponer detalles del error — solo indicar que el token no es válido
            return jsonify({"error": "Token inválido o expirado"}), 401

        return f(*args, **kwargs)

    return decorated


def sudo_erp_required(f):
    """
    Decorador exclusivo para rutas del panel admin ERP.
    Solo permite acceso si el usuario tiene rol 'sudo_erp'.

    Uso:
        @sudo_erp_required
        def mi_endpoint(): ...
    """

    @wraps(f)
    @jwt_required  # Primero valida el token
    def decorated(*args, **kwargs):
        rol = request.user.get("rol")

        if rol != "sudo_erp":
            return (
                jsonify(
                    {"error": "Acceso denegado. Se requiere rol de administrador ERP"}
                ),
                403,
            )

        return f(*args, **kwargs)

    return decorated


def admin_empresa_required(f):
    """
    Decorador para rutas que requieren ser admin de una empresa.
    Permite acceso tanto al sudo_erp como al admin_empresa.

    Identificación del admin: mediante el campo `rol` del JWT
    (rol == "admin_empresa"). El campo booleano es_admin_empresa
    quedó obsoleto y fue eliminado para evitar redundancia con el rol.

    Uso:
        @admin_empresa_required
        def mi_endpoint(): ...
    """

    @wraps(f)
    @jwt_required
    def decorated(*args, **kwargs):
        rol = request.user.get("rol")

        # sudo_erp y admin_empresa pasan; cualquier otro rol es denegado.
        if rol not in ("sudo_erp", "admin_empresa"):
            return (
                jsonify({"error": "Acceso denegado. Se requiere rol de administrador"}),
                403,
            )

        return f(*args, **kwargs)

    return decorated


def _permisos_efectivos(user: dict) -> list[str] | str:
    """
    Normaliza el campo 'permisos' del JWT a una lista limpia.

    El campo puede venir en tres formatos por compatibilidad histórica:
      - Lista:    ["on", "unidades.ver"]    ← formato actual
      - Wildcard: "*"                        ← acceso total por configuración
      - String:   "on,cund1,cpoi1"           ← legacy PHP separado por comas

    Retorna la lista normalizada, o el string "*" intacto para que el
    llamador lo trate como wildcard. Centralizar esta normalización evita
    que cada decorador re-implemente el parseo (y diverja en el futuro).
    """
    permisos_raw = user.get("permisos")

    if permisos_raw == "*":
        return "*"
    if isinstance(permisos_raw, list):
        return permisos_raw
    if isinstance(permisos_raw, str):
        return [p.strip() for p in permisos_raw.split(",") if p.strip()]
    # None u otro tipo inesperado — tratarlo como sin permisos
    return []


def permiso_required_any(*claves_permiso: str):
    """
    Decorador de fábrica: permite el acceso si el usuario tiene AL MENOS UNO
    de los permisos indicados.

    ¿Por qué existe? Hay endpoints que sirven a más de un módulo: el listado
    de programaciones alimenta tanto la pantalla de Cumplimiento
    (cumplimiento.ver) como el Historial de Cumplimiento (hist_cumplim.ver).
    Exigir un único permiso obligaría a duplicar el endpoint o a regalar
    permisos de un módulo para usar el otro.

    Misma jerarquía que permiso_required:
      1. sudo_erp   → acceso total, sin revisar permisos (único bypass).
      2. wildcard * → acceso total por configuración (legacy).
      3. resto      → debe tener alguna de las claves en sus permisos.

    Uso:
        @permiso_required_any("cumplimiento.ver", "hist_cumplim.ver")
        def mi_endpoint(): ...
    """

    def decorator(f):
        @wraps(f)
        @jwt_required
        def decorated(*args, **kwargs):
            user = request.user

            # Nivel 1: sudo_erp tiene acceso total al sistema.
            # Es el único bypass — refleja que es operador interno, no cliente.
            if user.get("rol") == "sudo_erp":
                return f(*args, **kwargs)

            permisos = _permisos_efectivos(user)

            # Wildcard legacy: acceso total por configuración
            if permisos == "*":
                return f(*args, **kwargs)

            if not any(clave in permisos for clave in claves_permiso):
                listado = ", ".join(f"'{c}'" for c in claves_permiso)
                return (
                    jsonify(
                        {
                            "error": (
                                f"Acceso denegado. "
                                f"Se requiere alguno de los permisos: {listado}."
                            )
                        }
                    ),
                    403,
                )

            return f(*args, **kwargs)

        return decorated

    return decorator


def permiso_required(clave_permiso: str):
    """
    Decorador de fábrica: verifica que el usuario tenga un permiso específico
    antes de permitir el acceso al endpoint.

    Es el caso particular de permiso_required_any con una sola clave —
    delegar evita dos copias de la misma jerarquía de autorización que
    podrían divergir con el tiempo (código limpio: una sola fuente de
    verdad para la lógica de permisos).

    Jerarquía de acceso:
      1. sudo_erp       → acceso total, sin revisar permisos (único bypass).
      2. cualquier rol  → debe tener la clave en su lista `permisos` del JWT.
                          Incluye admin_empresa, usuario común y cualquier
                          rol futuro. La lista se calcula al login como la
                          UNIÓN de permisos heredados del rol (r_rol_permisos)
                          más permisos específicos (r_usuario_permisos).

    Uso:
        @permiso_required("unidades.ver")
        def mi_endpoint(): ...

    Args:
        clave_permiso: Clave del permiso requerido (ej: "unidades.ver").
    """
    return permiso_required_any(clave_permiso)


def validate_empresa_access(id_empresa_solicitada: int, user_payload: dict) -> bool:
    """
    Valida que el usuario tenga acceso real a la empresa solicitada.

    Reglas:
      - sudo_erp → acceso total a cualquier empresa
      - admin_empresa / usuario → solo su propia empresa del JWT

    Uso en endpoints que reciben id_empresa del cliente:
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")
        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

    Args:
        id_empresa_solicitada: ID de la empresa que el cliente quiere acceder.
        user_payload: Payload del JWT del usuario autenticado (request.user).

    Returns:
        True si el usuario tiene acceso, False en caso contrario.
    """
    rol = user_payload.get("rol")

    # sudo_erp puede operar en cualquier empresa
    if rol == "sudo_erp":
        return True

    # Otros roles solo pueden operar en su propia empresa del JWT
    empresa_del_token = user_payload.get("id_empresa")
    return empresa_del_token == id_empresa_solicitada


def user_has_permission(user: dict, clave_permiso: str) -> bool:
    """Indica si un usuario (dict del JWT) tiene un permiso dado.

    Misma lógica que el decorador permiso_required pero como función suelta,
    para usarla en servicios/rutas donde el chequeo no es de acceso al endpoint
    sino de una sub-acción (ej. permitir cambiar el login dentro de un update
    que ya pasó su propia autorización).

    sudo_erp y wildcard "*" siempre dan True.
    """
    if not user:
        return False
    if user.get("rol") == "sudo_erp":
        return True

    permisos_raw = user.get("permisos")
    if permisos_raw == "*":
        return True
    if isinstance(permisos_raw, list):
        permisos_lista = permisos_raw
    elif isinstance(permisos_raw, str):
        permisos_lista = [p.strip() for p in permisos_raw.split(",") if p.strip()]
    else:
        permisos_lista = []

    return clave_permiso in permisos_lista