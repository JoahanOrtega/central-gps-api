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


def permiso_required(clave_permiso: str):
    """
    Decorador de fábrica: verifica que el usuario tenga un permiso específico
    antes de permitir el acceso al endpoint.

    Jerarquía de acceso:
      1. sudo_erp       → acceso total, sin revisar permisos (único bypass).
      2. cualquier rol  → debe tener la clave en su lista `permisos` del JWT.
                          Incluye admin_empresa, usuario común y cualquier
                          rol futuro. La lista se calcula al login como la
                          UNIÓN de permisos heredados del rol (r_rol_permisos)
                          más permisos específicos (r_usuario_permisos).

    Nota sobre admin_empresa:
      El admin_empresa NO tiene bypass automático. Sus capacidades se
      definen en r_rol_permisos — si le asignan todos los permisos del
      catálogo, se comporta como antes; si le quitan uno (ej: cund3 =
      crear unidades), ese endpoint queda bloqueado sin tocar código.
      Esto mantiene la lógica de autorización 100% en datos.

    El campo 'permisos' del JWT puede venir como:
      - Lista:    ["on", "cund1", "cpoi1"]   ← formato actual
      - Wildcard: "*"                         ← compatibilidad legacy
      - String:   "on,cund1,cpoi1"            ← compatibilidad legacy PHP

    Uso:
        @permiso_required("cund1")
        def mi_endpoint(): ...

    Args:
        clave_permiso: Clave del permiso requerido (ej: "cund1", "cpoi1", "on").
    """

    def decorator(f):
        @wraps(f)
        @jwt_required
        def decorated(*args, **kwargs):
            user = request.user
            rol = user.get("rol")

            # Nivel 1: sudo_erp tiene acceso total al sistema.
            # Es el único bypass — refleja que es operador interno, no cliente.
            if rol == "sudo_erp":
                return f(*args, **kwargs)

            # Nivel 2: cualquier otro rol (incluido admin_empresa) se valida
            # contra su lista de permisos efectivos. Esto elimina privilegios
            # hardcodeados y centraliza la autorización en datos.
            permisos_raw = user.get("permisos")

            # Wildcard legacy: acceso total por configuración
            if permisos_raw == "*":
                return f(*args, **kwargs)

            # Normalizar a lista: el campo puede venir como list[str] (formato
            # nuevo desde authenticate_user) o como string legacy separado por
            # comas ("on,cund1,cpoi1"). Ambos casos producen una lista limpia.
            if isinstance(permisos_raw, list):
                permisos_lista = permisos_raw
            elif isinstance(permisos_raw, str):
                permisos_lista = [
                    p.strip() for p in permisos_raw.split(",") if p.strip()
                ]
            else:
                # None u otro tipo inesperado — tratarlo como sin permisos
                permisos_lista = []

            if clave_permiso not in permisos_lista:
                return (
                    jsonify(
                        {
                            "error": (
                                f"Acceso denegado. "
                                f"Se requiere el permiso '{clave_permiso}'."
                            )
                        }
                    ),
                    403,
                )

            return f(*args, **kwargs)

        return decorated

    return decorator


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
