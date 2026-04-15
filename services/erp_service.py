from db.connection import get_db_connection

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

        cursor.execute("SELECT * FROM v_erp_resumen_empresas")
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


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
            connection.close()


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
            connection.close()


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
            connection.close()


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
            "SELECT * FROM v_erp_usuarios_empresa WHERE id_empresa = %s", (id_empresa,)
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


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
            connection.close()


# ─────────────────────────────────────────────
# MÓDULO: Catálogo de permisos del sistema
# ─────────────────────────────────────────────


def get_all_permissions():
    """Devuelve el catálogo completo de permisos con conteo de uso."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("SELECT * FROM v_erp_catalogo_permisos")
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


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
            connection.close()


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
                "SELECT * FROM v_erp_auditoria WHERE entidad = %s LIMIT %s",
                (entidad, limit),
            )
        else:
            cursor.execute("SELECT * FROM v_erp_auditoria LIMIT %s", (limit,))

        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]

        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


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
