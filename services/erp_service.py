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

        cursor.execute("""
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
        """)
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
    """
    Devuelve todos los usuarios activos de una empresa con sus roles.

    Modelo 1:N (tras migración 001):
      La empresa del usuario vive en t_usuarios.id_empresa, no en
      r_empresa_usuarios. Esta función consulta directamente t_usuarios +
      t_roles + t_empresas para no depender del estado de la vista
      v_erp_usuarios_empresa (que puede requerir actualización por separado).

    Campos retornados — mismos nombres que la vista anterior para
    compatibilidad con el frontend:
      - id_empresa, empresa
      - id_usuario, email_login, nombre_usuario
      - rol (clave técnica), nombre_rol (nombre legible)
      - status_usuario, fecha_asignacion
      - total_permisos (específicos en r_usuario_permisos; excluye heredados)

    Se eliminaron los campos:
      - es_admin_empresa → ahora se infiere de rol == 'admin_empresa'
      - status_relacion, autenticacion_2f → no se usan en la UI actual;
        si hacen falta, se agregan explícitamente.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                e.id_empresa                             AS id_empresa,
                e.nombre                                 AS empresa,
                u.id                                     AS id_usuario,
                u.usuario                                AS email_login,
                u.nombre                                 AS nombre_usuario,
                COALESCE(r.clave,  'sin_rol')            AS rol,
                COALESCE(r.nombre, 'Sin rol asignado')   AS nombre_rol,
                u.status                                 AS status_usuario,
                u.fecha_registro                         AS fecha_asignacion,
                COALESCE(perm.total, 0)                  AS total_permisos
            FROM t_usuarios u
            INNER JOIN t_empresas e ON e.id_empresa = u.id_empresa
            LEFT  JOIN t_roles    r ON r.id_rol     = u.id_rol
            -- Conteo de permisos específicos del usuario en su empresa.
            -- No incluye heredados del rol (r_rol_permiso) — solo los
            -- asignados individualmente desde el panel del admin de empresa.
            LEFT JOIN (
                SELECT id_usuario, id_empresa, COUNT(*) AS total
                FROM r_usuario_permisos
                GROUP BY id_usuario, id_empresa
            ) perm ON perm.id_usuario = u.id
                  AND perm.id_empresa = u.id_empresa
            WHERE u.id_empresa = %s
              AND u.status     = 1
            ORDER BY u.nombre ASC
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
    Promueve o revoca el rol admin_empresa de un usuario.

    Cambio de diseño (modelo 1:N):
        Antes: alternaba un flag en r_empresa_usuarios.es_admin_empresa.
        Ahora: cambia el id_rol en t_usuarios entre 'admin_empresa' y
        'usuario'. El rol es la fuente única de verdad; la columna
        es_admin_empresa fue eliminada por redundante.

    Precondiciones:
        - El usuario debe pertenecer a la empresa (u.id_empresa = id_empresa).
        - El usuario no puede ser sudo_erp (solo hay un sudo_erp por sistema
          y no se promueve/degrada por este endpoint).

    Args:
        id_usuario:         ID del usuario a modificar.
        id_empresa:         Empresa del usuario (validación de pertenencia).
        es_admin:           True para promover, False para degradar.
        id_usuario_cambio:  ID del sudo_erp que ejecuta (auditoría).

    Returns:
        Tupla (data, error_dict|None) igual al patrón del resto del módulo.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Validar que el usuario pertenece a la empresa y obtener su rol actual.
        cursor.execute(
            """
            SELECT u.id_empresa, r.clave AS rol_actual
              FROM t_usuarios u
              LEFT JOIN t_roles r ON r.id_rol = u.id_rol
             WHERE u.id     = %s
               AND u.status = 1
            """,
            (id_usuario,),
        )
        row = cursor.fetchone()
        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "Usuario no encontrado o inactivo",
            }

        id_empresa_actual, rol_actual = row

        if id_empresa_actual != id_empresa:
            return None, {
                "code": "USER_NOT_IN_EMPRESA",
                "message": "El usuario no pertenece a la empresa indicada",
            }

        if rol_actual == _ROL_SUDO_ERP_CLAVE:
            return None, {
                "code": "CANNOT_MODIFY_SUDO",
                "message": "No es posible cambiar el rol del sudo_erp",
            }

        # 2. Resolver el id_rol destino según la acción.
        nuevo_rol = _ROL_ADMIN_EMPRESA_CLAVE if es_admin else _ROL_USUARIO_CLAVE
        nuevo_id_rol = _get_rol_id_by_clave(cursor, nuevo_rol)
        if nuevo_id_rol is None:
            logger.error("Rol '%s' no configurado en t_roles", nuevo_rol)
            return None, {"code": "ROL_NOT_CONFIGURED", "message": "Rol no configurado"}

        # 3. Si ya tiene el rol objetivo, no hacer nada.
        if rol_actual == nuevo_rol:
            return {
                "actualizado": False,
                "mensaje": "El usuario ya tenía ese rol",
            }, None

        # 4. Aplicar el cambio en t_usuarios. El trigger de BD
        #    valida la invariante rol ↔ id_empresa.
        cursor.execute(
            "UPDATE t_usuarios SET id_rol = %s WHERE id = %s",
            (nuevo_id_rol, id_usuario),
        )

        accion = "PROMOTE_ADMIN" if es_admin else "REVOKE_ADMIN"
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion=accion,
            datos_anteriores={"rol": rol_actual},
            datos_nuevos={"rol": nuevo_rol, "id_empresa": id_empresa},
        )
        # _registrar_auditoria hace commit interno.

        return {"actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en set_admin_empresa id_usuario=%s: %s", id_usuario, repr(e)
        )
        return None, {"code": "DATABASE_ERROR", "message": str(e)}
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────
# Creación de usuario admin de empresa
# ─────────────────────────────────────────────


# Claves de roles en t_roles. Centralizar evita hardcodear IDs numéricos
# (que pueden variar entre ambientes) y documenta las intenciones.
_ROL_SUDO_ERP_CLAVE = "sudo_erp"
_ROL_ADMIN_EMPRESA_CLAVE = "admin_empresa"
_ROL_USUARIO_CLAVE = "usuario"

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
      5. INSERT en t_usuarios con id_empresa, id_rol=admin_empresa, perfil=1, status=1.
      6. INSERT en r_empresa_usuarios (registro histórico; el flag de
         admin se infiere del rol, no se guarda aquí).
      7. Registrar auditoría (entidad='usuario', acción='CREATE_ADMIN_EMPRESA').
      8. Commit.

    Tras el login, la lógica de authenticate_user detectará el id_rol y
    cargará los permisos heredados de r_rol_permiso para admin_empresa —
    todos EXCEPTO cund3 (crear unidades), por la política de Fase A.

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

        # 5. INSERT en t_usuarios incluyendo id_empresa.
        #    Con el modelo 1:N, la empresa vive directamente en t_usuarios.
        #    El trigger trg_validar_usuario_empresa garantiza que para el
        #    rol admin_empresa id_empresa sea no-NULL.
        cursor.execute(
            """
            INSERT INTO t_usuarios
                (usuario, clave, nombre, perfil, id_rol, id_empresa, status)
            VALUES (%s, %s, %s, %s, %s, %s, 1)
            RETURNING id
            """,
            (
                usuario,
                clave_hasheada,
                nombre,
                _PERFIL_ADMIN_EMPRESA,
                id_rol,
                id_empresa,
            ),
        )
        new_user_id = cursor.fetchone()[0]

        # 6. INSERT en r_empresa_usuarios — asociación histórica.
        #    La tabla ya no contiene es_admin_empresa (eliminada en
        #    migración 003b). Se mantiene como relación simple con
        #    auditoría, hasta su deprecación completa en una fase futura.
        cursor.execute(
            """
            INSERT INTO r_empresa_usuarios
                (id_usuario, id_empresa, status,
                 id_usuario_registro, fecha_registro)
            VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP)
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

        cursor.execute("""
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
        """)
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


def get_audit_log(
    limit: int = 100,
    entidad: str | None = None,
    id_usuario: int | None = None,
    accion: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
):
    """
    Devuelve el log de auditoría del sistema con filtros opcionales.

    Filtros (todos opcionales, se combinan con AND):
        entidad:     'empresa', 'usuario', 'session', etc.
        id_usuario:  ID del usuario que ejecutó la acción.
        accion:      'LOGIN', 'CREATE_USUARIO', 'UPDATE_USUARIO', etc.
        fecha_desde: 'YYYY-MM-DD' — fecha inicial INCLUSIVA.
        fecha_hasta: 'YYYY-MM-DD' — fecha final INCLUSIVA (cubre hasta 23:59:59).

    Construimos la query dinámicamente con WHERE acumulativo. Cada filtro
    añade su condición usando placeholders %s — sin concatenación de
    strings (defensa contra SQL injection).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Construcción dinámica del WHERE.
        # Lista paralela: condiciones SQL + valores para los placeholders.
        conditions: list[str] = []
        params: list = []

        if entidad:
            conditions.append("entidad = %s")
            params.append(entidad)

        if id_usuario is not None:
            # NOTA: la vista v_erp_auditoria NO expone el id_usuario crudo,
            # solo email_usuario y nombre_usuario. Tenemos que filtrar por
            # email para mantener compatibilidad con la vista, o consultar
            # contra t_auditoria directo. Vamos por la primera opción —
            # menos riesgo, mismos índices.
            cursor.execute(
                "SELECT usuario FROM t_usuarios WHERE id = %s LIMIT 1",
                (id_usuario,),
            )
            row = cursor.fetchone()
            if row:
                conditions.append("email_usuario = %s")
                params.append(row[0])
            else:
                # Usuario no existe: forzar resultado vacío (1=0) en lugar
                # de devolver TODO el log. Heurística: fallar silencioso
                # con resultado vacío es menos peligroso que filtrar mal.
                conditions.append("1 = 0")

        if accion:
            # Aceptamos accion exacta (LOGIN, CREATE_USUARIO, etc.).
            # Comparación case-insensitive para tolerar variaciones del frontend.
            conditions.append("UPPER(accion) = UPPER(%s)")
            params.append(accion)

        if fecha_desde:
            # fecha_desde a las 00:00:00 — incluye todo el día desde.
            conditions.append("fecha_registro >= %s")
            params.append(f"{fecha_desde} 00:00:00")

        if fecha_hasta:
            # fecha_hasta a las 23:59:59 — incluye todo el día final.
            conditions.append("fecha_registro <= %s")
            params.append(f"{fecha_hasta} 23:59:59")

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
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
              {where_clause}
              ORDER BY fecha_registro DESC
              LIMIT %s
          """
        params.append(limit)

        cursor.execute(query, tuple(params))

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


def get_users_with_audit_activity(limit: int = 200):
    """
    Devuelve la lista de usuarios que tienen al menos UN registro en
    t_auditoria. Sirve para popular el dropdown filtrable del frontend
    en la página de Auditoría.

    Por qué NO devolver TODOS los usuarios del sistema:
        - Muchos nunca generan eventos auditables (ej. perfiles legacy)
        - El dropdown sería innecesariamente largo
        - El UX correcto es mostrar solo usuarios "relevantes" para
          auditoría (los que aparecen en la tabla)

    Returns:
        Tupla (lista, error). Lista de dicts con:
            { id, usuario (email), nombre, rol, total_eventos }
        Ordenado por total_eventos DESC para que los más activos
        aparezcan primero.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # JOIN con t_usuarios para resolver el id (la vista solo trae el
        # email). count(*) por usuario para mostrar actividad relativa.
        cursor.execute(
            """
              SELECT
                  u.id,
                  u.usuario,
                  u.nombre,
                  r.clave AS rol,
                  COUNT(a.id_auditoria) AS total_eventos
              FROM t_auditoria a
              JOIN t_usuarios u ON u.id = a.id_usuario
              LEFT JOIN t_roles r ON r.id_rol = u.id_rol
              GROUP BY u.id, u.usuario, u.nombre, r.clave
              ORDER BY total_eventos DESC, u.nombre ASC
              LIMIT %s
              """,
            (limit,),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error("Error en get_users_with_audit_activity: %s", repr(e))
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


"""
create_usuario_completo
─────────────────────────────────────────────────────────────────────
"""


# Perfil legacy para usuarios normales (no admin). Convención del PHP:
#   777 → sudo_erp
#   1   → admin_empresa
#   0   → usuario normal
_PERFIL_USUARIO = 0


def create_usuario_completo(
    id_empresa: int,
    payload: dict,
    id_usuario_registro: int,
):
    """
    Crea un usuario completo desde el wizard del Panel ERP.

    Acepta una estructura con 3 secciones:
      payload = {
        "datos":         {usuario, clave, nombre, rol, email?, telefono?},
        "restricciones": {dias_acceso?, hora_inicio?, hora_fin?,
                          id_grupo_unidades?, id_cliente?, dias_consulta?},
        "permisos":      {id_permisos: [int, ...]}
      }

    Flujo transaccional (todo o nada):
      1. Validar empresa activa.
      2. Validar usuario disponible.
      3. Resolver id_rol según el rol pedido.
      4. Si vienen permisos, validar que existan en t_permisos.
      5. Hashear contraseña.
      6. INSERT en t_usuarios con todos los campos (datos + restricciones).
      7. INSERT en r_empresa_usuarios (asociación histórica).
      8. INSERT batch en r_usuario_permisos por cada id_permiso.
      9. Auditoría.
     10. Commit.

    Si CUALQUIER paso falla, rollback completo — el usuario nunca queda
    parcialmente creado (ej. usuario sin permisos asignados).

    Decisiones de diseño:

    - Por qué usar dict.get() con defaults en lugar de payload["x"]:
      Marshmallow ya garantiza la presencia de los campos requeridos,
      pero los opcionales pueden no venir. dict.get(...) con default
      evita KeyError ante un schema más laxo en el futuro.

    - Por qué validar permisos contra t_permisos:
      Si el frontend manda un id_permiso que no existe (race condition
      con un permiso desactivado, o cliente buggy), el INSERT en
      r_usuario_permisos fallaría por la FK con un error genérico.
      Validarlo arriba permite responder con un error claro al cliente.

    - Por qué se separa de create_empresa_admin:
      create_empresa_admin tiene un contrato más simple (rol fijo, sin
      restricciones, sin permisos granulares) usado por la UI de "agregar
      admin desde EmpresasPage". Romper ese contrato para soportar todo
      esto sería un cambio mayor. Mantener ambas funciones permite que
      cada UI use la que necesita.

    Args:
        id_empresa:           Empresa a la que pertenecerá el usuario.
        payload:              Dict ya validado por CreateUsuarioCompletoSchema.
        id_usuario_registro:  ID del sudo_erp que ejecuta la acción (auditoría).

    Returns:
        Tupla (data, error). En éxito:
          ({"id_usuario": int, "usuario": str, "nombre": str,
            "rol": str, "id_empresa": int,
            "permisos_asignados": int}, None)
        En error de negocio:
          (None, {"code": "...", "message": "..."})
        En errores de BD inesperados se lanza la excepción al caller
        para que el endpoint la capture y retorne 500.
    """
    connection = None
    cursor = None

    # Extraer las 3 secciones del payload validado
    datos = payload["datos"]
    restricciones = payload.get("restricciones", {}) or {}
    permisos_seccion = payload.get("permisos", {}) or {}

    usuario = datos["usuario"].strip()
    clave_plana = datos["clave"]  # nunca strip — las contraseñas no se recortan
    nombre = datos["nombre"].strip()
    rol_solicitado = datos["rol"]
    email = (datos.get("email") or "").strip() or None
    telefono = (datos.get("telefono") or "").strip() or None

    # Lista de permisos a asignar — puede ser vacía.
    id_permisos = permisos_seccion.get("id_permisos", []) or []

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # ─── 1. Validar que la empresa exista y esté activa ──────────────────
        if not _empresa_exists_and_active(cursor, id_empresa):
            return None, {
                "code": "EMPRESA_NOT_FOUND",
                "message": "La empresa no existe o está inactiva",
            }

        # ─── 2. Validar unicidad del nombre de usuario ───────────────────────
        if _username_is_taken(cursor, usuario):
            return None, {
                "code": "USERNAME_TAKEN",
                "message": "El nombre de usuario ya está en uso",
            }

        # ─── 3. Resolver id_rol según el rol solicitado ──────────────────────
        # El schema ya validó que rol_solicitado esté en ROLES_PERMITIDOS_CREACION,
        # pero igual hacemos el lookup dinámico para evitar hardcodear IDs.
        id_rol = _get_rol_id_by_clave(cursor, rol_solicitado)
        if id_rol is None:
            logger.error(
                "Rol '%s' no encontrado en t_roles al crear usuario completo",
                rol_solicitado,
            )
            return None, {
                "code": "ROL_NOT_CONFIGURED",
                "message": f"El rol '{rol_solicitado}' no está configurado",
            }

        # ─── 4. Validar que todos los id_permiso existan ─────────────────────
        # Hacemos UN solo SELECT con IN (...) en lugar de N consultas.
        # Si la lista trae IDs inválidos, devolvemos un error claro con
        # los IDs problemáticos para facilitar debugging del cliente.
        if id_permisos:
            cursor.execute(
                """
                SELECT id_permiso FROM t_permisos
                 WHERE id_permiso = ANY(%s)
                   AND status     = 1
                """,
                (id_permisos,),
            )
            existentes = {row[0] for row in cursor.fetchall()}
            inexistentes = set(id_permisos) - existentes

            if inexistentes:
                return None, {
                    "code": "INVALID_PERMISSIONS",
                    "message": (
                        f"Los siguientes permisos no existen o están inactivos: "
                        f"{sorted(inexistentes)}"
                    ),
                }

        # ─── 5. Hashear contraseña con bcrypt ────────────────────────────────
        # Mismo BCRYPT_ROUNDS que usa auth_service para mantener consistencia
        # de costo computacional al hacer login.
        clave_hasheada = bcrypt.hashpw(
            clave_plana.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
        ).decode("utf-8")

        # ─── 6. INSERT en t_usuarios ──────────────────────────────────────────
        # Incluye datos + restricciones en una sola query.
        # NULL para hora_inicio/hora_fin permite que postgres use el default
        # de la columna (00:00:00 / 23:59:59).
        # Para perfil legacy: 1 = admin_empresa, 0 = usuario.
        perfil = (
            _PERFIL_ADMIN_EMPRESA
            if rol_solicitado == _ROL_ADMIN_EMPRESA_CLAVE
            else _PERFIL_USUARIO
        )

        cursor.execute(
            """
            INSERT INTO t_usuarios (
                usuario,
                clave,
                nombre,
                telefono,
                perfil,
                id_rol,
                id_empresa,
                status,
                dias_acceso,
                hora_inicio_acceso,
                hora_fin_acceso,
                id_grupo_unidades,
                id_cliente,
                dias_consulta,
                id_usuario_registro,
                fecha_registro
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, 1,
                %s, COALESCE(%s, '00:00:00'::time), COALESCE(%s, '23:59:59'::time),
                %s, %s, %s, %s, CURRENT_TIMESTAMP
            )
            RETURNING id
            """,
            (
                usuario,
                clave_hasheada,
                nombre,
                telefono,
                perfil,
                id_rol,
                id_empresa,
                # Restricciones (todos opcionales — pueden ser None)
                restricciones.get("dias_acceso") or "",
                restricciones.get("hora_inicio_acceso"),
                restricciones.get("hora_fin_acceso"),
                restricciones.get("id_grupo_unidades"),
                restricciones.get("id_cliente"),
                restricciones.get("dias_consulta", 0),
                id_usuario_registro,
            ),
        )
        new_user_id = cursor.fetchone()[0]

        # Nota sobre el campo `email`: la tabla t_usuarios NO tiene columna
        # email — el legacy usa `usuario` como email para login. Si quieres
        # almacenar un email separado, hay que agregar la columna en otra
        # migración. Por ahora aceptamos el campo en el payload pero NO se
        # persiste (queda en el log de auditoría como histórico).

        # ─── 7. INSERT en r_empresa_usuarios ──────────────────────────────────
        # Asociación histórica usuario ↔ empresa. Misma lógica que
        # create_empresa_admin para mantener consistencia.
        cursor.execute(
            """
            INSERT INTO r_empresa_usuarios
                (id_usuario, id_empresa, status,
                 id_usuario_registro, fecha_registro)
            VALUES (%s, %s, 1, %s, CURRENT_TIMESTAMP)
            """,
            (new_user_id, id_empresa, id_usuario_registro),
        )

        # ─── 8. INSERT batch en r_usuario_permisos ────────────────────────────
        # executemany para todos los permisos en una sola operación.
        # No lanzamos error si la lista está vacía — el usuario hereda
        # solo del rol y eso está bien.
        if id_permisos:
            cursor.executemany(
                """
                INSERT INTO r_usuario_permisos
                    (id_usuario, id_empresa, id_permiso,
                     id_usuario_registro, fecha_registro)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                """,
                [
                    (new_user_id, id_empresa, id_permiso, id_usuario_registro)
                    for id_permiso in id_permisos
                ],
            )

        # ─── 9. Auditoría ─────────────────────────────────────────────────────
        # Registramos los datos creados SIN la contraseña. El email del
        # payload (que no se persiste en t_usuarios) sí lo guardamos en
        # auditoría para que quede el histórico de que fue ingresado.
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_registro,
            entidad="usuario",
            id_entidad=new_user_id,
            accion="CREATE_USUARIO",
            datos_nuevos={
                "usuario": usuario,
                "nombre": nombre,
                "rol": rol_solicitado,
                "id_empresa": id_empresa,
                "email": email,
                "telefono": telefono,
                "restricciones": {
                    "dias_acceso": restricciones.get("dias_acceso") or "",
                    "hora_inicio_acceso": str(
                        restricciones.get("hora_inicio_acceso") or ""
                    ),
                    "hora_fin_acceso": str(restricciones.get("hora_fin_acceso") or ""),
                    "id_grupo_unidades": restricciones.get("id_grupo_unidades"),
                    "id_cliente": restricciones.get("id_cliente"),
                    "dias_consulta": restricciones.get("dias_consulta", 0),
                },
                "permisos_asignados": len(id_permisos),
            },
        )

        # _registrar_auditoria hace commit internamente. Si llegamos aquí,
        # toda la transacción se persistió.

        return (
            {
                "id_usuario": new_user_id,
                "usuario": usuario,
                "nombre": nombre,
                "rol": rol_solicitado,
                "id_empresa": id_empresa,
                "permisos_asignados": len(id_permisos),
            },
            None,
        )

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_usuario_completo id_empresa=%s usuario=%s: %s",
            id_empresa,
            usuario,
            repr(e),
        )
        # Re-lanzar para que el endpoint responda 500 con mensaje genérico.
        # Errores de negocio se devuelven como (None, {code, message})
        # antes de llegar aquí.
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


import secrets
import string

# ─── Constantes para password temporal ──────────────────────────────────────
# Charset sin caracteres confusos (0/O, 1/l/I) para evitar errores de
# transcripción cuando el sudo_erp dicta la temporal por teléfono.
_TEMP_PASSWORD_CHARSET = (
    string.ascii_uppercase.replace("O", "").replace("I", "")
    + string.ascii_lowercase.replace("l", "")
    + string.digits.replace("0", "").replace("1", "")
)
# Longitud lo suficientemente larga para resistir brute-force pero
# manejable para que el usuario la teclee al primer login.
_TEMP_PASSWORD_LENGTH = 12


def _generate_temp_password() -> str:
    """
    Genera una contraseña temporal aleatoria criptográficamente segura.

    Usa secrets.choice (no random) para garantizar entropía adecuada.
    El charset excluye caracteres visualmente ambiguos.
    """
    return "".join(
        secrets.choice(_TEMP_PASSWORD_CHARSET) for _ in range(_TEMP_PASSWORD_LENGTH)
    )


# ─── 1. Reactivar usuario inhabilitado ────────────────────────────────────────


def reactivar_usuario(
    id_empresa: int,
    id_usuario: int,
    id_usuario_cambio: int,
):
    """
    Reactiva un usuario inhabilitado (status 0 → 1) en la empresa indicada.

    Solo se llama desde el endpoint exclusivo del Panel ERP
    (sudo_erp_required en la capa de routes).

    Conserva todos los permisos y restricciones que el usuario tenía
    antes de inhabilitarse — no se borran al inhabilitar, solo se
    "esconden" del listado por status=0.

    Returns:
        Tupla (data, error). En éxito: ({id_usuario, reactivado: True}, None).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Verificar existencia y status actual
        cursor.execute(
            """
            SELECT u.id, u.status, r.clave AS rol
              FROM t_usuarios u
              LEFT JOIN t_roles r ON r.id_rol = u.id_rol
             WHERE u.id         = %s
               AND u.id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe o no pertenece a esta empresa",
            }

        if row[1] == 1:
            return None, {
                "code": "USER_ALREADY_ACTIVE",
                "message": "El usuario ya está activo",
            }

        # 2. UPDATE status=1
        cursor.execute(
            """
            UPDATE t_usuarios
               SET status            = 1,
                   fecha_cambio      = NOW(),
                   id_usuario_cambio = %s
             WHERE id         = %s
               AND id_empresa = %s
            """,
            (id_usuario_cambio, id_usuario, id_empresa),
        )

        # 3. Auditoría — "REACTIVAR" (9 chars, cabe holgado).
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion="REACTIVAR",
            datos_anteriores={"status": 0},
            datos_nuevos={"status": 1, "id_empresa": id_empresa},
        )

        return ({"id_usuario": id_usuario, "reactivado": True}, None)

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en reactivar_usuario id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible reactivar el usuario",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── 2. Eliminar usuario permanente (HARD DELETE) ────────────────────────────


def eliminar_usuario_permanente(
    id_empresa: int,
    id_usuario: int,
    id_usuario_cambio: int,
):
    """
    Elimina PERMANENTEMENTE un usuario: DELETE FROM t_usuarios.

    OPERACIÓN IRREVERSIBLE. Borra:
      - Fila en t_usuarios
      - Filas en r_usuario_permisos (con DELETE explícito en este service)
      - Filas en r_empresa_usuarios (con DELETE explícito)

    NO borra:
      - Filas en t_auditoria (preservación de trazabilidad histórica).

    Reglas de seguridad:
      - No auto-eliminarse.
      - No eliminar a otro sudo_erp.
      - El usuario debe pertenecer a la empresa indicada.

    Returns:
        Tupla (data, error).
    """
    if id_usuario == id_usuario_cambio:
        return None, {
            "code": "CANNOT_DELETE_SELF",
            "message": "No puedes eliminarte a ti mismo",
        }

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Verificar existencia + obtener rol del target
        cursor.execute(
            """
            SELECT u.id, u.usuario, u.nombre, r.clave AS rol
              FROM t_usuarios u
              LEFT JOIN t_roles r ON r.id_rol = u.id_rol
             WHERE u.id         = %s
               AND u.id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe o no pertenece a esta empresa",
            }

        if row[3] == "sudo_erp":
            return None, {
                "code": "CANNOT_DELETE_SUDO",
                "message": "No se puede eliminar al administrador del sistema",
            }

        # Snapshot para auditoría (antes de borrar)
        datos_eliminados = {
            "id_usuario": row[0],
            "usuario": row[1],
            "nombre": row[2],
            "rol": row[3],
            "id_empresa": id_empresa,
        }

        # 2. Borrar relaciones primero (defensivo aunque haya CASCADE en FK)
        cursor.execute(
            "DELETE FROM r_usuario_permisos WHERE id_usuario = %s",
            (id_usuario,),
        )
        cursor.execute(
            "DELETE FROM r_empresa_usuarios WHERE id_usuario = %s",
            (id_usuario,),
        )

        # 3. Borrar el usuario
        cursor.execute(
            "DELETE FROM t_usuarios WHERE id = %s AND id_empresa = %s",
            (id_usuario, id_empresa),
        )

        # 4. Auditoría — "DELETE_PERM" (11 chars).
        # IMPORTANTE: en t_auditoria.id_entidad guardamos el id del usuario
        # ya borrado. La FK no aplica (auditoría no tiene FK a t_usuarios
        # por diseño). El id queda como referencia histórica del usuario
        # que existía.
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion="DELETE_PERM",
            datos_anteriores=datos_eliminados,
            datos_nuevos={"eliminado": True},
        )

        return ({"id_usuario": id_usuario, "eliminado": True}, None)

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en eliminar_usuario_permanente id_usuario=%s: %s",
            id_usuario,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible eliminar el usuario",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── 3. Resetear contraseña ──────────────────────────────────────────────────


def resetear_clave_usuario(
    id_empresa: int,
    id_usuario: int,
    id_usuario_cambio: int,
):
    """
    Resetea la contraseña del usuario a una temporal generada aleatoriamente.

    La password se devuelve EN CLARO en la respuesta una sola vez.
    El sudo_erp es responsable de comunicarla al usuario por canal seguro.

    Reglas:
      - Solo usuarios ACTIVOS (status=1). Si está inhabilitado, primero
        reactivar y luego resetear.

    Returns:
        Tupla (data, error). En éxito incluye 'password_temporal'.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Verificar usuario existe + pertenece + está activo
        cursor.execute(
            """
            SELECT id, usuario, status
              FROM t_usuarios
             WHERE id         = %s
               AND id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe o no pertenece a esta empresa",
            }

        if row[2] == 0:
            return None, {
                "code": "USER_ALREADY_INACTIVE",
                "message": (
                    "El usuario está inhabilitado. Reactívalo antes de "
                    "resetear su contraseña."
                ),
            }

        # 2. Generar password temporal y hashearla
        temp_password = _generate_temp_password()
        clave_hasheada = bcrypt.hashpw(
            temp_password.encode("utf-8"),
            bcrypt.gensalt(rounds=BCRYPT_ROUNDS),
        ).decode("utf-8")

        # 3. UPDATE
        cursor.execute(
            """
            UPDATE t_usuarios
               SET clave             = %s,
                   fecha_cambio      = NOW(),
                   id_usuario_cambio = %s
             WHERE id         = %s
               AND id_empresa = %s
            """,
            (clave_hasheada, id_usuario_cambio, id_usuario, id_empresa),
        )

        # 4. Auditoría — "RESET_CLAVE" (11 chars).
        # NUNCA loguear la password en claro ni el hash. Solo el evento.
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion="RESET_CLAVE",
            datos_anteriores=None,
            datos_nuevos={
                "id_empresa": id_empresa,
                "usuario": row[1],
                # No guardamos la password ni hash. Solo señalamos el evento.
                "metodo": "temporal_generada",
            },
        )

        return (
            {
                "id_usuario": id_usuario,
                "password_temporal": temp_password,  # se devuelve UNA SOLA VEZ
            },
            None,
        )

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en resetear_clave_usuario id_usuario=%s: %s",
            id_usuario,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible resetear la contraseña",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────────────────────────────────────
# MÓDULO: Gestión de permisos por usuario (PR feat/erp-permissions-management)
# ─────────────────────────────────────────────────────────────────────────────
#
# Después de la migración 004, los permisos se gestionan a nivel de usuario
# (r_usuario_permisos) en lugar de a nivel de rol. Estas funciones soportan:
#   1. Listar usuarios con conteo de permisos por empresa
#   2. Obtener permisos específicos de un usuario en una empresa
#   3. Reemplazar el set completo de permisos de un usuario en una empresa
#
# El sudo_erp es el único que puede usar estas funciones (validado en routes).


def list_users_with_permissions_count():
    """
    Devuelve la lista de usuarios activos con su rol y conteo de permisos
    por empresa.

    Cada fila representa una "asignación usuario-empresa": un usuario que
    pertenece a varias empresas aparecerá una vez por cada empresa.

    Útil para popular la tabla principal de la página /admin-erp/permisos
    donde el sudo elige qué usuario+empresa editar.

    Returns:
        Tupla (lista, error). Lista de dicts con:
            {
                id_usuario, usuario, nombre, rol_clave, rol_nombre,
                id_empresa, empresa,
                total_permisos
            }
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # JOIN con t_roles para resolver el rol (etiqueta).
        # JOIN con r_empresa_usuarios + t_empresas para listar las empresas
        # asociadas al usuario.
        # LEFT JOIN con r_usuario_permisos para contar; los usuarios sin
        # permisos asignados igualmente aparecen (con count=0).
        #
        # NO se incluye sudo_erp (id_rol=1) porque su modelo de permisos es
        # distinto (heredan TODO via r_rol_permisos + bypass en código).
        cursor.execute("""
            SELECT
                u.id                        AS id_usuario,
                u.usuario                   AS usuario,
                u.nombre                    AS nombre,
                r.clave                     AS rol_clave,
                r.nombre                    AS rol_nombre,
                e.id_empresa                AS id_empresa,
                e.nombre                   AS empresa,
                COALESCE(perm.total, 0)     AS total_permisos
            FROM t_usuarios u
            JOIN t_roles r ON r.id_rol = u.id_rol
            JOIN r_empresa_usuarios eu ON eu.id_usuario = u.id
            JOIN t_empresas e ON e.id_empresa = eu.id_empresa
            LEFT JOIN (
                SELECT id_usuario, id_empresa, COUNT(*) AS total
                FROM r_usuario_permisos
                GROUP BY id_usuario, id_empresa
            ) perm ON perm.id_usuario = u.id AND perm.id_empresa = e.id_empresa
            WHERE u.status = 1
              AND r.clave IN ('admin_empresa', 'usuario')
              AND e.status = 1
            ORDER BY r.id_rol ASC, u.nombre ASC, e.nombre ASC
            """)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error("Error en list_users_with_permissions_count: %s", repr(e))
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_user_permissions_in_company(id_usuario: int, id_empresa: int):
    """
    Devuelve los permisos asignados a un usuario en una empresa específica.

    Útil para popular el modal de edición que muestra "qué tiene este
    usuario en esta empresa". El frontend marca los checkboxes según
    los permisos que vengan en este array.

    Args:
        id_usuario:  ID del usuario.
        id_empresa:  ID de la empresa.

    Returns:
        Tupla (lista, error). Lista de dicts con todos los permisos del
        catálogo, cada uno con un flag `asignado` indicando si el usuario
        lo tiene o no:
            {
                id_permiso, clave, nombre, modulo, descripcion,
                asignado: bool
            }
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # LEFT JOIN para incluir TODOS los permisos del catálogo, incluso
        # los que el usuario NO tiene asignados. La columna 'asignado'
        # resultante es true si hay match en r_usuario_permisos.
        cursor.execute(
            """
            SELECT
                p.id_permiso,
                p.clave,
                p.nombre,
                p.modulo,
                p.descripcion,
                (rup.id_usuario_permiso IS NOT NULL) AS asignado
            FROM t_permisos p
            LEFT JOIN r_usuario_permisos rup
                   ON rup.id_permiso  = p.id_permiso
                  AND rup.id_usuario  = %s
                  AND rup.id_empresa  = %s
            WHERE p.status = 1
            ORDER BY p.modulo ASC, p.clave ASC
            """,
            (id_usuario, id_empresa),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows], None

    except Exception as e:
        logger.error(
            "Error en get_user_permissions_in_company id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def replace_user_permissions(
    id_usuario: int,
    id_empresa: int,
    permisos_claves: list[str],
    id_usuario_actor: int,
):
    """
    Reemplaza COMPLETAMENTE el set de permisos de un usuario en una empresa.

    Estrategia: DELETE-then-INSERT en una transacción única.
      1. DELETE todos los permisos actuales del usuario en esa empresa
      2. INSERT los permisos nuevos basándose en las claves recibidas
    Si algo falla, ROLLBACK completo — ningún cambio parcial.

    Esta es la operación que el sudo invoca al guardar el modal de edición
    de permisos. El frontend manda el set COMPLETO (no diffs).

    Args:
        id_usuario:        Usuario al que se le reemplazan los permisos.
        id_empresa:        Empresa específica.
        permisos_claves:   Lista de claves (ej. ["clientes.ver", "unidades.editar"]).
                           Lista vacía es válida — significa "quitarle todos los permisos".
        id_usuario_actor:  Usuario que está haciendo el cambio (sudo). Se guarda
                           en id_usuario_registro/id_usuario_cambio para auditoría.

    Returns:
        Tupla (resultado, error). resultado es un dict con:
            {
                permisos_anteriores: int,  # cuántos tenía antes
                permisos_nuevos: int,      # cuántos tiene ahora
            }

    Errores comunes:
        - permisos_claves contiene una clave que no existe en t_permisos:
          el INSERT se hace por id_permiso (resuelto del catálogo); las
          claves desconocidas se ignoran silenciosamente. El resultado
          retornado refleja cuántas se aplicaron realmente.
        - id_usuario o id_empresa no existen: la FK lo rechaza con error
          de integridad → se devuelve en `error`.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Contar lo que había antes (para reporte en datos_anteriores de audit).
        cursor.execute(
            """
            SELECT COUNT(*) FROM r_usuario_permisos
             WHERE id_usuario = %s AND id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        permisos_anteriores = cursor.fetchone()[0]

        # 1. DELETE — borrar todos los permisos actuales del usuario en esa empresa.
        cursor.execute(
            """
            DELETE FROM r_usuario_permisos
             WHERE id_usuario = %s AND id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )

        # 2. INSERT — agregar los permisos nuevos.
        # Resolvemos las claves a id_permiso desde t_permisos en el mismo
        # INSERT (subquery con IN). Las claves que no existan se ignoran.
        if permisos_claves:
            cursor.execute(
                """
                INSERT INTO r_usuario_permisos
                       (id_usuario, id_empresa, id_permiso, id_usuario_registro)
                SELECT %s, %s, p.id_permiso, %s
                FROM t_permisos p
                WHERE p.clave = ANY(%s)
                  AND p.status = 1
                ON CONFLICT (id_usuario, id_empresa, id_permiso) DO NOTHING
                """,
                (id_usuario, id_empresa, id_usuario_actor, permisos_claves),
            )

        # Contar lo que quedó.
        cursor.execute(
            """
            SELECT COUNT(*) FROM r_usuario_permisos
             WHERE id_usuario = %s AND id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        permisos_nuevos = cursor.fetchone()[0]

        # Auditar el cambio en t_auditoria.
        # entidad="usuario_permisos" (entidad nueva, NO confundir con entidad="permiso"
        # que es para CRUD del catálogo).
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_actor,
            entidad="usuario_permisos",
            id_entidad=id_usuario,
            accion="REPLACE_PERMISSIONS",
            datos_anteriores={"total": permisos_anteriores, "id_empresa": id_empresa},
            datos_nuevos={
                "total": permisos_nuevos,
                "id_empresa": id_empresa,
                "claves": permisos_claves,
            },
        )

        connection.commit()

        return {
            "permisos_anteriores": permisos_anteriores,
            "permisos_nuevos": permisos_nuevos,
        }, None

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en replace_user_permissions id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, str(e)
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
