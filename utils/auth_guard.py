from functools import wraps
from flask import request, jsonify
from utils.jwt_handler import decode_jwt


def jwt_required(f):
    """
    Decorador base: solo verifica que el token JWT sea válido.
    Disponible en todas las rutas protegidas como mínimo.
    El payload queda en request.user para uso posterior.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")

        if not token:
            return jsonify({"error": "Token requerido"}), 401

        try:
            payload = decode_jwt(token)
            request.user = payload
        except Exception:
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

        # El sudo ERP siempre puede, el admin de empresa también
        if rol != "sudo_erp" and not es_admin:
            return (
                jsonify({"error": "Acceso denegado. Se requiere rol de administrador"}),
                403,
            )

        return f(*args, **kwargs)

    return decorated


def permiso_required(clave_permiso: str):
    """
    Decorador de fábrica: valida que el usuario tenga un permiso específico.
    El sudo_erp siempre pasa. El resto se valida contra r_usuario_permisos.

    Uso:
        @permiso_required("cund1")
        def mi_endpoint(): ...
    """

    def decorator(f):
        @wraps(f)
        @jwt_required
        def decorated(*args, **kwargs):
            rol = request.user.get("rol")

            # El sudo_erp tiene acceso total sin consultar permisos
            if rol == "sudo_erp":
                return f(*args, **kwargs)

            # Para otros roles se valida en la lógica de negocio
            # pasando el permiso requerido al contexto de la request
            request.permiso_requerido = clave_permiso
            return f(*args, **kwargs)

        return decorated

    return decorator
