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

    Uso:
        @admin_empresa_required
        def mi_endpoint(): ...
    """

    @wraps(f)
    @jwt_required
    def decorated(*args, **kwargs):
        rol = request.user.get("rol")
        es_admin = request.user.get("es_admin_empresa", False)

        # sudo_erp siempre puede; admin_empresa también
        if rol != "sudo_erp" and not es_admin:
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
      1. sudo_erp       → acceso total, sin revisar permisos
      2. admin_empresa  → acceso total dentro de su empresa
      3. usuario normal → debe tener la clave en su campo 'permisos' del JWT

    El campo 'permisos' del JWT soporta dos formatos:
      - Wildcard: "*"               → acceso a todos los permisos
      - Lista:    "on,cund1,cpoi1"  → acceso solo a los permisos listados

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

            # Nivel 1: sudo_erp tiene acceso total al sistema
            if rol == "sudo_erp":
                return f(*args, **kwargs)

            # Nivel 2: admin_empresa tiene acceso total dentro de su empresa
            if user.get("es_admin_empresa", False):
                return f(*args, **kwargs)

            # Nivel 3: usuario normal — verificar contra el campo 'permisos' del JWT.
            # El campo viene como string separado por comas: "on,cund1,cpoi1"
            # o como wildcard "*" para acceso total.
            permisos_raw = user.get("permisos", "")

            # Wildcard: el usuario tiene todos los permisos habilitados
            if permisos_raw == "*":
                return f(*args, **kwargs)

            # Lista: verificar que la clave solicitada esté incluida
            permisos_lista = [p.strip() for p in permisos_raw.split(",") if p.strip()]

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
