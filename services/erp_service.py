import logging
import bcrypt
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)

# Número de rondas de bcrypt — alineado con auth_service.BCRYPT_ROUNDS.
# Al crear un usuario desde el ERP, la contraseña se hashea con los mismos
# parámetros que usa el login para que la verificación sea consistente.
BCRYPT_ROUNDS = 12

# ─────────────────────────────────────────────
# MÓDULO: Gestión de empresas
# ─────────────────────────────────────────────


def get_all_companies():
    """Devuelve el resumen de todas las empresas para el dashboard ERP."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id_empresa,
                empresa,
                status,
                total_unidades,
                total_usuarios,
                total_clientes,
                total_admins_empresa,
                email_principal,
                fecha_registro
            FROM v_erp_resumen_empresas
            ORDER BY empresa ASC
        """
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error("Error en get_all_companies: %s", repr(e))
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_company(
    nombre: str,
    direccion: str,
    telefonos: str,
    lat: float,
    lng: float,
    logo: str,
    id_usuario_registro: int,
):
    """Crea una nueva empresa en el sistema."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            INSERT INTO t_empresas (nombre, direccion, telefonos, lat, lng, logo, status)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            RETURNING id_empresa
        """
        cursor.execute(query, (nombre, direccion, telefonos, lat or 0, lng or 0, logo))
        new_id = cursor.fetchone()[0]
        connection.commit()

        # Registrar en auditoría
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_registro,
            entidad="empresa",
            id_entidad=new_id,
            accion="CREATE",
            datos_nuevos={"nombre": nombre, "direccion": direccion},
        )

        return {"id_empresa": new_id}, None

    except Exception as e:
        if connection:
            connection.rollback()
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def update_company(id_empresa: int, datos: dict, id_usuario_cambio: int):
    """Actualiza los datos de una empresa."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Solo actualizar los campos permitidos
        campos_permitidos = {"nombre", "direccion", "telefonos", "lat", "lng", "logo"}
        campos = {k: v for k, v in datos.items() if k in campos_permitidos}

        if not campos:
            return None, "No hay campos válidos para actualizar"

        set_clause = ", ".join([f"{k} = %s" for k in campos])
        valores = list(campos.values()) + [id_usuario_cambio, id_empresa]

        query = f"""
            UPDATE t_empresas
            SET {set_clause},
                id_usuario_cambio = %s,
                fecha_cambio      = CURRENT_TIMESTAMP
            WHERE id_empresa = %s
        """
        cursor.execute(query, valores)
        connection.commit()

        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="empresa",
            id_entidad=id_empresa,
            accion="UPDATE",
            datos_nuevos=campos,
        )

        return {"actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def toggle_company_status(id_empresa: int, status: int, id_usuario_cambio: int):
    """
    Activa (status=1) o suspende (status=0) una empresa.
    Al suspender, todos sus usuarios quedan sin acceso automáticamente
    porque el login valida e.status = 1.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE t_empresas
            SET status            = %s,
                id_usuario_cambio = %s,
                fecha_cambio      = CURRENT_TIMESTAMP
            WHERE id_empresa = %s
            """,
            (status, id_usuario_cambio, id_empresa),
        )
        connection.commit()

        accion = "SUSPEND" if status == 0 else "ACTIVATE"
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="empresa",
            id_entidad=id_empresa,
            accion=accion,
            datos_nuevos={"status": status},
        )

        return {"actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# MÓDULO: Gestión de usuarios y admins
# ─────────────────────────────────────────────


def get_users_by_company(id_empresa: int):
    """Devuelve todos los usuarios de una empresa con sus roles."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id_empresa,
                empresa,
                id_usuario,
                email_login,
                nombre_usuario,
                rol,
                nombre_rol,
                es_admin_empresa,
                status_relacion,
                status_usuario,
                autenticacion_2f,
                fecha_asignacion,
                total_permisos
            FROM v_erp_usuarios_empresa
            WHERE id_empresa = %s
            ORDER BY nombre_usuario ASC
            """,
            (id_empresa,),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error(
            "Error en get_users_by_company id_empresa=%s: %s", id_empresa, repr(e)
        )
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def set_admin_empresa(
    id_usuario: int, id_empresa: int, es_admin: bool, id_usuario_cambio: int
):
    """
    Promueve o revoca el rol de admin de empresa a un usuario.
    Solo el sudo_erp puede hacer esto.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE r_empresa_usuarios
            SET es_admin_empresa  = %s,
                id_usuario_cambio = %s,
                fecha_cambio      = CURRENT_TIMESTAMP
            WHERE id_usuario  = %s
              AND id_empresa   = %s
            """,
            (1 if es_admin else 0, id_usuario_cambio, id_usuario, id_empresa),
        )
        connection.commit()

        accion = "PROMOTE_ADMIN" if es_admin else "REVOKE_ADMIN"
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario_empresa",
            id_entidad=id_usuario,
            accion=accion,
            datos_nuevos={"id_empresa": id_empresa, "es_admin_empresa": es_admin},
        )

        return {"actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# Creación de usuario admin de empresa
# ─────────────────────────────────────────────


# Clave del rol en t_roles. Centralizar la constante evita hardcodear el ID
# numérico (que puede variar entre ambientes) y documenta la intención.
_ROL_ADMIN_EMPRESA_CLAVE = "admin_empresa"

# El campo `perfil` en t_usuarios es legacy del sistema PHP.
# Convención observada en BD:
#   777 → sudo_erp
#   1   → admin_empresa
#   0   → usuario normal
_PERFIL_ADMIN_EMPRESA = 1


def _get_rol_id_by_clave(cursor, clave: str) -> int | None:
    """Obtiene el id_rol a partir de la clave textual. None si no existe."""
    cursor.execute(
        "SELECT id_rol FROM t_roles WHERE clave = %s AND status = 1 LIMIT 1",
        (clave,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _empresa_exists_and_active(cursor, id_empresa: int) -> bool:
    """Valida que la empresa exista y esté activa."""
    cursor.execute(
        "SELECT 1 FROM t_empresas WHERE id_empresa = %s AND status = 1 LIMIT 1",
        (id_empresa,),
    )
    return cursor.fetchone() is not None


def _username_is_taken(cursor, usuario: str) -> bool:
    """
    Verifica si el nombre de usuario ya está en uso.

    Se considera "tomado" incluso si el usuario dueño del nombre está inactivo
    (status = 0) — reutilizar nombres de usuarios desactivados mezclaría
    auditorías y podría habilitar suplantación.
    """
    cursor.execute(
        "SELECT 1 FROM t_usuarios WHERE usuario = %s LIMIT 1",
        (usuario,),
    )
    return cursor.fetchone() is not None


def create_empresa_admin(
    id_empresa: int,
    datos_usuario: dict,
    id_usuario_registro: int,
):
    """
    Crea un nuevo usuario admin de empresa de forma TRANSACCIONAL.

    Flujo (todo o nada — commit al final, rollback ante cualquier fallo):
      1. Validar que la empresa exista y esté activa.
      2. Validar que el nombre de usuario esté disponible.
      3. Resolver id_rol del rol 'admin_empresa' (no hardcodear IDs).
      4. Hashear la contraseña con bcrypt.
      5. INSERT en t_usuarios con perfil=1, status=1.
      6. INSERT en r_empresa_usuarios con es_admin_empresa=1, status=1.
      7. Registrar auditoría (entidad='usuario', acción='CREATE_ADMIN_EMPRESA').
      8. Commit.

    Al tener `es_admin_empresa=1` en r_empresa_usuarios, la lógica de login
    (authenticate_user) lo detectará y le cargará los permisos del rol
    admin_empresa desde r_rol_permisos — incluyendo TODOS los permisos EXCEPTO
    cund3 (crear unidades), por la política definida en Fase A.

    Args:
        id_empresa:           Empresa a la que el nuevo usuario será admin.
        datos_usuario:        Dict validado por CreateEmpresaAdminSchema.
        id_usuario_registro:  ID del sudo_erp que ejecuta la acción (auditoría).

    Returns:
        Tupla (data, error). En éxito: ({id_usuario, usuario, nombre}, None).
        En error de negocio (empresa inexistente, usuario duplicado, etc):
        (None, {"code": "...", "message": "..."}).
    """
    connection = None
    cursor = None

    usuario = datos_usuario["usuario"].strip()
    clave_plana = datos_usuario["clave"]  # no strip — las contraseñas no se recortan
    nombre = datos_usuario["nombre"].strip()

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Validar que la empresa exista y esté activa
        if not _empresa_exists_and_active(cursor, id_empresa):
            return None, {
                "code": "EMPRESA_NOT_FOUND",
                "message": "La empresa no existe o está inactiva",
            }

        # 2. Validar unicidad de nombre de usuario
        if _username_is_taken(cursor, usuario):
            return None, {
                "code": "USERNAME_TAKEN",
                "message": "El nombre de usuario ya está en uso",
            }

        # 3. Resolver id_rol dinámicamente
        id_rol = _get_rol_id_by_clave(cursor, _ROL_ADMIN_EMPRESA_CLAVE)
        if id_rol is None:
            logger.error(
                "No se encontró el rol '%s' en t_roles",
                _ROL_ADMIN_EMPRESA_CLAVE,
            )
            return None, {
                "code": "ROL_NOT_CONFIGURED",
                "message": "El rol admin_empresa no está configurado en el sistema",
            }

        # 4. Hashear contraseña con bcrypt (alineado con auth_service)
        clave_hasheada = bcrypt.hashpw(
            clave_plana.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
        ).decode("utf-8")

        # 5. INSERT en t_usuarios — solo columnas obligatorias.
        #    Los campos opcionales (email, telefono) se omiten intencionalmente
        #    si la BD los acepta como NULL; en caso contrario, agregar aquí.
        cursor.execute(
            """
            INSERT INTO t_usuarios (usuario, clave, nombre, perfil, id_rol, status)
            VALUES (%s, %s, %s, %s, %s, 1)
            RETURNING id
            """,
            (usuario, clave_hasheada, nombre, _PERFIL_ADMIN_EMPRESA, id_rol),
        )
        new_user_id = cursor.fetchone()[0]

        # 6. INSERT en r_empresa_usuarios con la marca de admin.
        #    Incluye id_usuario_registro para auditoría del propio registro.
        cursor.execute(
            """
            INSERT INTO r_empresa_usuarios
                (id_usuario, id_empresa, es_admin_empresa, status,
                 id_usuario_registro, fecha_registro)
            VALUES (%s, %s, 1, 1, %s, CURRENT_TIMESTAMP)
            """,
            (new_user_id, id_empresa, id_usuario_registro),
        )

        # 7. Auditoría — JSON serializable, sin la contraseña (nunca loguear claves).
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_registro,
            entidad="usuario",
            id_entidad=new_user_id,
            accion="CREATE_ADMIN_EMPRESA",
            datos_nuevos={
                "usuario": usuario,
                "nombre": nombre,
                "id_empresa": id_empresa,
                "rol": _ROL_ADMIN_EMPRESA_CLAVE,
                "es_admin_empresa": True,
            },
        )

        # _registrar_auditoria hace commit internamente.

        return (
            {
                "id_usuario": new_user_id,
                "usuario": usuario,
                "nombre": nombre,
                "id_empresa": id_empresa,
                "rol": _ROL_ADMIN_EMPRESA_CLAVE,
                "es_admin_empresa": True,
            },
            None,
        )

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_empresa_admin id_empresa=%s usuario=%s: %s",
            id_empresa,
            usuario,
            repr(e),
        )
        return None, {"code": "DATABASE_ERROR", "message": str(e)}

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# MÓDULO: Catálogo de permisos del sistema
# ─────────────────────────────────────────────


def get_all_permissions():
    """Devuelve el catálogo completo de permisos del sistema."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                id_permiso,
                clave,
                nombre,
                modulo,
                descripcion,
                status,
                usuarios_con_permiso,
                empresas_con_permiso
            FROM v_erp_catalogo_permisos
            ORDER BY modulo ASC, nombre ASC
        """
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error("Error en get_all_permissions: %s", repr(e))
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_permission(clave: str, nombre: str, modulo: str, descripcion: str):
    """Agrega un nuevo permiso al catálogo del sistema."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO t_permisos (clave, nombre, modulo, descripcion)
            VALUES (%s, %s, %s, %s)
            RETURNING id_permiso
            """,
            (clave, nombre, modulo, descripcion),
        )
        new_id = cursor.fetchone()[0]
        connection.commit()

        return {"id_permiso": new_id}, None

    except Exception as e:
        if connection:
            connection.rollback()
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# MÓDULO: Auditoría
# ─────────────────────────────────────────────


def get_audit_log(limit: int = 100, entidad: str = None):
    """
    Devuelve el log de auditoría del sistema.
    Se puede filtrar por entidad (empresa, usuario, permiso...).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if entidad:
            cursor.execute(
                """
                SELECT
                    id_auditoria,
                    email_usuario,
                    nombre_usuario,
                    rol_usuario,
                    entidad,
                    id_entidad,
                    accion,
                    datos_anteriores,
                    datos_nuevos,
                    ip_origen,
                    fecha_registro
                FROM v_erp_auditoria
                WHERE entidad = %s
                ORDER BY fecha_registro DESC
                LIMIT %s
                """,
                (entidad, limit),
            )
        else:
            cursor.execute(
                """
                SELECT
                    id_auditoria,
                    email_usuario,
                    nombre_usuario,
                    rol_usuario,
                    entidad,
                    id_entidad,
                    accion,
                    datos_anteriores,
                    datos_nuevos,
                    ip_origen,
                    fecha_registro
                FROM v_erp_auditoria
                ORDER BY fecha_registro DESC
                LIMIT %s
                """,
                (limit,),
            )

        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error("Error en get_audit_log: %s", repr(e))
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# HELPER INTERNO: Registrar en bitácora
# ─────────────────────────────────────────────


def _registrar_auditoria(
    cursor,
    connection,
    id_usuario: int,
    entidad: str,
    id_entidad: int,
    accion: str,
    datos_anteriores: dict = None,
    datos_nuevos: dict = None,
):
    """
    Inserta un registro en t_auditoria.
    Se llama internamente desde cada operación de escritura.
    No genera su propia conexión; usa la conexión del llamador
    para que todo quede en la misma transacción.
    """
    import json
    from flask import request as flask_request

    # Obtener IP del request actual si existe
    try:
        ip = flask_request.remote_addr
    except Exception:
        ip = None

    cursor.execute(
        """
        INSERT INTO t_auditoria
            (id_usuario, entidad, id_entidad, accion, datos_anteriores, datos_nuevos, ip_origen)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            id_usuario,
            entidad,
            id_entidad,
            accion,
            json.dumps(datos_anteriores) if datos_anteriores else None,
            json.dumps(datos_nuevos) if datos_nuevos else None,
            ip,
        ),
    )
    connection.commit()
