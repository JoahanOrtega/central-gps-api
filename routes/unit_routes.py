import logging
from flask import Blueprint, jsonify, request
from services.unit_service import (
    get_units,
    create_unit,
    get_unit_detail,
    update_unit,
    delete_unit,
)
from utils.auth_guard import jwt_required, permiso_required, validate_empresa_access
from utils.validation import validate_payload
from validators import CreateUnitSchema, UpdateUnitSchema

units_bp = Blueprint("units", __name__)

logger = logging.getLogger(__name__)


@units_bp.route("/units", methods=["GET"])
@jwt_required
def list_units():
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        if not id_empresa:
            return jsonify({"error": "Empresa no definida"}), 400

        search = request.args.get("search", "").strip()
        units = get_units(id_empresa, search if search else None)
        return jsonify(units), 200
    except Exception as error:
        logger.error(
            "Error en GET /units id_empresa=%s: %s",
            request.args.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units", methods=["POST"])
@permiso_required("unidades.crear")
def create_new_unit():
    """
    Crea una nueva unidad.

    Autorización (unidades.crear = "Crear unidades"):
      - sudo_erp       → acceso por bypass de rol.
      - admin_empresa  → denegado por diseño (el rol NO hereda unidades.crear).
      - usuario        → permitido solo si el admin_empresa le asigna unidades.crear
                         explícitamente en r_usuario_permisos.

    Validación (marshmallow):
      - numero, marca, tipo, imei, chip, fecha_instalacion: obligatorios
      - imei: exactamente 10 dígitos numéricos
      - odometro_inicial: >= 0
      - fecha_instalacion: no futura
      - tipo: valor del catálogo [1-7]

    Respuesta en error de validación:
      HTTP 422 { "error": "Datos inválidos", "fields": { "campo": ["mensaje"] } }
    """
    data = request.get_json(silent=True)

    # Validar antes de tocar la BD — si el payload es inválido, fallar rápido.
    # `data` queda filtrado: solo campos declarados en CreateUnitSchema.
    data, validation_error = validate_payload(CreateUnitSchema(), data)
    if validation_error:
        return validation_error

    try:
        id_usuario = request.user.get("sub")
        id_empresa = data.get("id_empresa") or request.user.get("id_empresa")

        if not id_usuario or not id_empresa:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result = create_unit(data, id_usuario, id_empresa)
        return jsonify({"message": "Unidad creada correctamente", "unit": result}), 201

    except Exception as error:
        logger.error(
            "Error en POST /units id_empresa=%s: %s",
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Detalle y edición de una unidad
# ═══════════════════════════════════════════════════════════════════════════


@units_bp.route("/units/<int:id_unidad>", methods=["GET"])
@permiso_required("unidades.editar")
def get_unit_full_detail(id_unidad: int):
    """
    Devuelve el detalle completo de una unidad.

    Autorización (unidades.editar = "Editar unidades"):
      - sudo_erp       → acceso por bypass de rol. Ve TODOS los campos,
                         incluido el equipo instalado (IMEI, chip, inputs).
      - admin_empresa  → tiene unidades.editar por defecto (ver seed). Ve los
                         campos operativos SIN el equipo instalado.
      - usuario        → solo si el admin_empresa le asignó unidades.editar en
                         r_usuario_permisos. Mismas restricciones que arriba.

    Nota: usamos unidades.editar para LEER el detalle también (no solo para
    editarlo). El listado público sigue protegido solo con jwt_required
    (cund1 en el legacy) — este endpoint es para la pantalla de edición
    y comparte su permiso.

    Respuestas:
      200 → { ...campos de la unidad filtrados por rol }
      403 → sin permiso unidades.editar (lo devuelve el decorador)
      404 → unidad no existe o pertenece a otra empresa
    """
    try:
        # Patrón: el query param ?id_empresa=X permite al sudo_erp operar
        # sobre una empresa específica (su JWT no tiene id_empresa fijo).
        # Para admin_empresa/usuario, validate_empresa_access confirma que
        # el id coincide con su JWT — si intentan pasar otra empresa,
        # responde 403.
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        rol = request.user.get("rol")

        if not id_empresa:
            return jsonify({"error": "Empresa no definida en la sesión"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        unit = get_unit_detail(id_unidad, id_empresa, rol)
        if unit is None:
            return jsonify({"error": "Unidad no encontrada"}), 404

        return jsonify(unit), 200

    except Exception as error:
        logger.error(
            "Error en GET /units/%s id_empresa=%s: %s",
            id_unidad,
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>", methods=["PATCH"])
@permiso_required("unidades.editar")
def patch_unit(id_unidad: int):
    """
    Actualiza parcialmente una unidad.

    Autorización por campo:
      - sudo_erp       → puede editar todos los campos.
      - admin_empresa  → puede editar datos operativos (número, marca,
                         modelo, matrícula, operador, combustible, seguro,
                         verificación). NO puede editar equipo instalado
                         (IMEI, chip, modelo AVL, inputs/outputs, fecha
                         instalación). El servicio rechaza con 403.
      - usuario        → mismas reglas que admin_empresa (si tiene el
                         permiso asignado).

    Validación:
      - Schema UpdateUnitSchema: valida formato de los campos presentes.
        Todos opcionales — se actualiza solo lo que viene en el body.

    Respuestas:
      200 → { "message": "...", "actualizado": true }
      403 → { "code": "FIELDS_NOT_ALLOWED", "message": "..." } (servicio)
      404 → { "code": "UNIT_NOT_FOUND", "message": "..." }
      422 → errores de validación de schema
    """
    data = request.get_json(silent=True)

    # Validar formato antes de ir a la BD.
    # `data` queda filtrado: solo campos declarados en UpdateUnitSchema,
    # incluido id_empresa como campo de contexto. Cualquier otro campo
    # que el cliente intente enviar (status, id_rol, etc.) se descarta.
    data, validation_error = validate_payload(UpdateUnitSchema(), data)
    if validation_error:
        return validation_error

    try:
        # id_empresa es un campo de CONTEXTO, no de actualización. Lo
        # separamos del payload antes de pasarlo al service para que no
        # termine en el UPDATE SQL (cambiar la empresa de una unidad no
        # es una operación permitida).
        #
        # Fuentes en orden de prioridad:
        #   1. Query param ?id_empresa=X  (estándar REST para contexto)
        #   2. Body (compatibilidad con clientes que lo envían dentro del JSON)
        #   3. JWT (admin_empresa/usuario tienen empresa fija en el token)
        #
        # dict.pop() remueve y retorna — si no existe, retorna el default.
        id_empresa_body = data.pop("id_empresa", None)
        id_empresa = (
            request.args.get("id_empresa", type=int)
            or id_empresa_body
            or request.user.get("id_empresa")
        )
        rol = request.user.get("rol")
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        # Tras sacar id_empresa, el body podría quedar vacío (ej: cliente
        # que solo quería hacer "switch de contexto"). Es un no-op desde
        # la perspectiva del UPDATE — devolvemos un 400 claro.
        if not data:
            return jsonify({"error": "No hay campos para actualizar"}), 400

        result, error = update_unit(
            id_unidad=id_unidad,
            id_empresa=id_empresa,
            payload=data,
            rol=rol,
            id_usuario=id_usuario,
        )

        if error:
            status = {
                "UNIT_NOT_FOUND": 404,
                "FIELDS_NOT_ALLOWED": 403,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "Unidad actualizada correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as error:
        logger.error(
            "Error en PATCH /units/%s id_empresa=%s: %s",
            id_unidad,
            request.user.get("id_empresa"),
            repr(error),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500


@units_bp.route("/units/<int:id_unidad>", methods=["DELETE"])
@permiso_required("unidades.eliminar")
def remove_unit(id_unidad: int):
    """
    Elimina (soft-delete) una unidad.

    Autorización:
      - sudo_erp: permiso bypass.
      - admin_empresa: permiso heredado del rol si está activo.
      - usuario: solo si tiene 'unidades.eliminar' asignado vía
        r_usuario_permisos.

    El frontend debe ocultar el botón "Eliminar" si el usuario no tiene
    el permiso — esto es solo UX. Si el botón se muestra erróneamente
    y se hace click, el backend rechaza con 403.

    Por qué soft-delete:
      Mantener el registro permite:
        1. Auditoría histórica (qué unidad ejecutó qué viaje, etc.).
        2. Restauración futura sin re-ingresar IMEI, chip, vel_max...
        3. Consistencia con el patrón del resto de tablas del sistema.

    Respuestas:
      200 → { "message": "...", "eliminado": true, "id_unidad": N }
      403 → empresa no autorizada para el usuario
      404 → { "code": "UNIT_NOT_FOUND", "message": "..." }

    No usa request body — el id viene en la URL y el id_empresa del JWT
    o query param. Esto sigue la convención REST: DELETE no debería
    requerir body.
    """
    try:
        id_empresa = request.args.get("id_empresa", type=int) or request.user.get(
            "id_empresa"
        )
        id_usuario = request.user.get("sub")

        if not id_empresa or not id_usuario:
            return jsonify({"error": "Datos de autenticación incompletos"}), 400

        if not validate_empresa_access(id_empresa, request.user):
            return jsonify({"error": "Acceso no autorizado a esta empresa"}), 403

        result, error = delete_unit(
            id_unidad=id_unidad,
            id_empresa=id_empresa,
            id_usuario_cambio=int(id_usuario),
        )

        if error:
            status = {
                "UNIT_NOT_FOUND": 404,
                "DATABASE_ERROR": 500,
            }.get(error["code"], 500)
            return jsonify(error), status

        return (
            jsonify(
                {
                    "message": "Unidad eliminada correctamente",
                    **result,
                }
            ),
            200,
        )

    except Exception as exc:
        logger.error(
            "Error en DELETE /units/%s: %s",
            id_unidad,
            repr(exc),
            exc_info=True,
        )
        return jsonify({"error": "Error interno del servidor"}), 500
