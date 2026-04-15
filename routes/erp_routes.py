from flask import Blueprint, request, jsonify
from utils.auth_guard import sudo_erp_required
from services import erp_service

# Blueprint con prefijo /admin-erp para todas las rutas del panel ERP
erp_bp = Blueprint("erp", __name__, url_prefix="/admin-erp")


# ─────────────────────────────────────────────
# EMPRESAS
# ─────────────────────────────────────────────


@erp_bp.route("/empresas", methods=["GET"])
@sudo_erp_required
def list_companies():
    """Dashboard: resumen de todas las empresas."""
    data, error = erp_service.get_all_companies()
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200


@erp_bp.route("/empresas", methods=["POST"])
@sudo_erp_required
def create_company():
    """Crear una nueva empresa."""
    body = request.get_json() or {}

    # Validar campo obligatorio
    if not body.get("nombre"):
        return jsonify({"error": "El campo 'nombre' es obligatorio"}), 400

    data, error = erp_service.create_company(
        nombre=body.get("nombre"),
        direccion=body.get("direccion"),
        telefonos=body.get("telefonos"),
        lat=body.get("lat"),
        lng=body.get("lng"),
        logo=body.get("logo"),
        id_usuario_registro=int(request.user["sub"]),
    )
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 201


@erp_bp.route("/empresas/<int:id_empresa>", methods=["PUT"])
@sudo_erp_required
def update_company(id_empresa):
    """Actualizar datos de una empresa."""
    body = request.get_json() or {}
    data, error = erp_service.update_company(
        id_empresa=id_empresa, datos=body, id_usuario_cambio=int(request.user["sub"])
    )
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200


@erp_bp.route("/empresas/<int:id_empresa>/status", methods=["PATCH"])
@sudo_erp_required
def toggle_company(id_empresa):
    """Activar o suspender una empresa. Body: { status: 0|1 }"""
    body = request.get_json() or {}
    status = body.get("status")

    if status not in (0, 1):
        return jsonify({"error": "El campo 'status' debe ser 0 o 1"}), 400

    data, error = erp_service.toggle_company_status(
        id_empresa=id_empresa, status=status, id_usuario_cambio=int(request.user["sub"])
    )
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200


# ─────────────────────────────────────────────
# USUARIOS Y ADMINS DE EMPRESA
# ─────────────────────────────────────────────


@erp_bp.route("/empresas/<int:id_empresa>/usuarios", methods=["GET"])
@sudo_erp_required
def list_company_users(id_empresa):
    """Lista todos los usuarios de una empresa."""
    data, error = erp_service.get_users_by_company(id_empresa)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200


@erp_bp.route(
    "/empresas/<int:id_empresa>/usuarios/<int:id_usuario>/admin", methods=["PATCH"]
)
@sudo_erp_required
def set_admin(id_empresa, id_usuario):
    """
    Promueve o revoca el rol admin de empresa a un usuario.
    Body: { es_admin: true|false }
    """
    body = request.get_json() or {}
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
        return jsonify({"error": error}), 500
    return jsonify(data), 200


# ─────────────────────────────────────────────
# PERMISOS DEL SISTEMA
# ─────────────────────────────────────────────


@erp_bp.route("/permisos", methods=["GET"])
@sudo_erp_required
def list_permissions():
    """Catálogo de todos los permisos del sistema."""
    data, error = erp_service.get_all_permissions()
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200


@erp_bp.route("/permisos", methods=["POST"])
@sudo_erp_required
def create_permission():
    """Agregar un nuevo permiso al catálogo."""
    body = request.get_json() or {}

    if not body.get("clave") or not body.get("nombre"):
        return jsonify({"error": "Los campos 'clave' y 'nombre' son obligatorios"}), 400

    data, error = erp_service.create_permission(
        clave=body.get("clave"),
        nombre=body.get("nombre"),
        modulo=body.get("modulo"),
        descripcion=body.get("descripcion"),
    )
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 201


# ─────────────────────────────────────────────
# AUDITORÍA
# ─────────────────────────────────────────────


@erp_bp.route("/auditoria", methods=["GET"])
@sudo_erp_required
def get_audit():
    """
    Log de auditoría del sistema.
    Query params opcionales: ?limit=100&entidad=empresa
    """
    limit = int(request.args.get("limit", 100))
    entidad = request.args.get("entidad", None)

    data, error = erp_service.get_audit_log(limit=limit, entidad=entidad)
    if error:
        return jsonify({"error": error}), 500
    return jsonify(data), 200
