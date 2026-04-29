"""
Service del módulo Catálogos > Usuarios.

Operaciones expuestas:
  - list_users_by_empresa: lista usuarios activos de una empresa.
  - get_user_detail:       lee un usuario completo (para pre-llenar el wizard).
  - create_user:           crea un usuario nuevo (reusa lógica de erp_service).
  - update_user:           edita parcialmente datos/restricciones/permisos.
  - inhabilitar_user:      soft-delete (status=0).

Reuso de lógica:
  create_user es una fachada delgada que llama a erp_service.create_usuario_completo,
  porque la lógica transaccional ya está implementada y probada allí. Lo
  único que cambia es de DÓNDE se llama y QUIÉN tiene permiso. Mantenerla
  en erp_service evita duplicar el INSERT en t_usuarios + r_empresa_usuarios
  + r_usuario_permisos + auditoría en dos lugares.
"""

import logging
import bcrypt

from db.connection import get_db_connection, release_db_connection
from services.auth_service import BCRYPT_ROUNDS  # noqa: F401 — reusable si se necesita
from services.erp_service import (
    create_usuario_completo,
    _empresa_exists_and_active,
    _get_rol_id_by_clave,
    _registrar_auditoria,
    _ROL_ADMIN_EMPRESA_CLAVE,
    _ROL_USUARIO_CLAVE,
    _PERFIL_ADMIN_EMPRESA,
)

logger = logging.getLogger(__name__)


# Perfil legacy para usuarios normales. Convención del PHP:
#   777 → sudo_erp / 1 → admin_empresa / 0 → usuario.
# Constante propia (no la importamos de erp_service porque allí es privada
# con underscore) para evitar acoplamiento innecesario entre módulos.
_PERFIL_USUARIO = 0


# ─── Set de campos editables por sección ──────────────────────────────────────
# Whitelist explícita por SECCIÓN — defensa en profundidad además del schema.
# Si en el futuro se agrega un campo al schema y se olvida considerarlo aquí,
# este set lo bloquea hasta hacerlo explícito.

_EDITABLE_DATOS_FIELDS = frozenset({"nombre", "rol", "email", "telefono"})

_EDITABLE_RESTRICCIONES_FIELDS = frozenset(
    {
        "dias_acceso",
        "hora_inicio_acceso",
        "hora_fin_acceso",
        "id_grupo_unidades",
        "id_cliente",
        "dias_consulta",
    }
)


# ─── Listar usuarios de una empresa ───────────────────────────────────────────


def list_users_by_empresa(id_empresa: int):
    """
    Lista usuarios ACTIVOS de una empresa.

    Filtra por status=1 — los usuarios inhabilitados no aparecen en el
    catálogo. Mantenerlos en BD permite auditoría histórica y posible
    reactivación desde el Panel ERP.

    El email se usa como `usuario` en este sistema (no hay columna email
    separada en t_usuarios). El frontend lo mostrará como tal.

    Returns:
        Lista de dicts con campos:
          {id, nombre, usuario, telefono, rol, dias_acceso,
           hora_inicio_acceso, hora_fin_acceso, id_grupo_unidades,
           id_cliente, dias_consulta, fecha_registro}
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                u.id,
                u.nombre,
                u.usuario,
                u.telefono,
                u.id_rol,
                r.clave AS rol,
                u.dias_acceso,
                u.hora_inicio_acceso,
                u.hora_fin_acceso,
                u.id_grupo_unidades,
                gu.nombre AS nombre_grupo_unidades,
                u.id_cliente,
                c.nombre AS nombre_cliente,
                u.dias_consulta,
                u.fecha_registro
            FROM t_usuarios u
            LEFT JOIN t_roles            r ON r.id_rol             = u.id_rol
            LEFT JOIN t_grupos_unidades gu ON gu.id_grupo_unidades = u.id_grupo_unidades
            LEFT JOIN t_clientes         c ON c.id_cliente         = u.id_cliente
            WHERE u.id_empresa = %s
              AND u.status     = 1
            ORDER BY u.nombre ASC
            """,
            (id_empresa,),
        )

        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row[0],
                    "nombre": row[1],
                    "usuario": row[2],
                    "telefono": row[3],
                    "id_rol": row[4],
                    "rol": row[5],
                    "dias_acceso": row[6] or "",
                    "hora_inicio_acceso": str(row[7]) if row[7] else None,
                    "hora_fin_acceso": str(row[8]) if row[8] else None,
                    "id_grupo_unidades": row[9],
                    "nombre_grupo_unidades": row[10],
                    "id_cliente": row[11],
                    "nombre_cliente": row[12],
                    "dias_consulta": row[13] or 0,
                    "fecha_registro": row[14].isoformat() if row[14] else None,
                }
            )

        return result, None

    except Exception as e:
        logger.error(
            "Error en list_users_by_empresa id_empresa=%s: %s",
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible obtener los usuarios",
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── Detalle de un usuario (para pre-llenar el wizard de edición) ─────────────


def get_user_detail(id_usuario: int, id_empresa: int):
    """
    Retorna el detalle completo de un usuario para edición.

    Incluye:
      - Datos del usuario (nombre, rol, email/usuario, telefono).
      - Restricciones (dias_acceso, horas, grupos, cliente, dias_consulta).
      - Lista de id_permiso asignados explícitamente (de r_usuario_permisos).

    Filtra por id_empresa para asegurar que el usuario pertenece a la
    empresa que tiene contexto. Sin esto, un admin_empresa podría leer
    detalles de usuarios de otra empresa pasando un id arbitrario.

    Returns:
        Tupla (data, error). En éxito retorna estructura idéntica a la
        que el wizard espera para pre-llenar el form.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Datos básicos del usuario
        cursor.execute(
            """
            SELECT
                u.id, u.nombre, u.usuario, u.telefono,
                u.id_rol, r.clave AS rol,
                u.dias_acceso,
                u.hora_inicio_acceso, u.hora_fin_acceso,
                u.id_grupo_unidades, u.id_cliente, u.dias_consulta,
                u.fecha_registro, u.fecha_cambio
            FROM t_usuarios u
            LEFT JOIN t_roles r ON r.id_rol = u.id_rol
            WHERE u.id         = %s
              AND u.id_empresa = %s
              AND u.status     = 1
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe o no pertenece a tu empresa",
            }

        # 2. Permisos asignados explícitamente al usuario
        # Se filtran por id_empresa: aunque el modelo es 1:N, mantenemos
        # el filtro por seguridad — si un día se cambia el modelo a M:N
        # esta query sigue siendo correcta sin tocarla.
        cursor.execute(
            """
            SELECT id_permiso
              FROM r_usuario_permisos
             WHERE id_usuario = %s
               AND id_empresa = %s
            """,
            (id_usuario, id_empresa),
        )
        id_permisos = [r[0] for r in cursor.fetchall()]

        # Estructura espejo del shape que el wizard espera para pre-llenar.
        return (
            {
                "id": row[0],
                "datos": {
                    "nombre": row[1],
                    "usuario": row[2],
                    "telefono": row[3],
                    "rol": row[5],
                    # email no se almacena por separado — el "usuario" funciona
                    # como email en este sistema. El frontend lo mostrará como
                    # tal en el campo email read-only del wizard de edición.
                    "email": row[2],
                },
                "restricciones": {
                    "dias_acceso": row[6] or "",
                    "hora_inicio_acceso": str(row[7]) if row[7] else "",
                    "hora_fin_acceso": str(row[8]) if row[8] else "",
                    "id_grupo_unidades": row[9],
                    "id_cliente": row[10],
                    "dias_consulta": row[11] or 0,
                },
                "permisos": {
                    "id_permisos": id_permisos,
                },
                "fecha_registro": row[12].isoformat() if row[12] else None,
                "fecha_cambio": row[13].isoformat() if row[13] else None,
            },
            None,
        )

    except Exception as e:
        logger.error(
            "Error en get_user_detail id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible obtener el usuario",
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── Crear usuario (fachada de create_usuario_completo) ───────────────────────


def create_user(id_empresa: int, payload: dict, id_usuario_registro: int):
    """
    Crea un usuario nuevo en la empresa.

    Esta función es una FACHADA de erp_service.create_usuario_completo.
    Toda la lógica transaccional (validar empresa, hashear, INSERT
    múltiple en 3 tablas, auditoría, rollback) ya está allí. Reusarla
    evita duplicar 100+ líneas de SQL y mantiene un solo punto de
    cambio si la lógica evoluciona.

    Por qué no hacemos un alias directo:
      Esta capa nos da un punto donde podríamos agregar lógica
      específica de catálogos en el futuro (notificación al usuario
      creado, hooks de bienvenida, etc.) sin tocar erp_service.
    """
    return create_usuario_completo(
        id_empresa=id_empresa,
        payload=payload,
        id_usuario_registro=id_usuario_registro,
    )


# ─── Actualizar usuario (PATCH parcial) ───────────────────────────────────────


def update_user(
    id_usuario: int,
    id_empresa: int,
    payload: dict,
    id_usuario_cambio: int,
):
    """
    Actualiza parcialmente un usuario.

    Acepta el shape del wizard de edición:
      payload = {
        "datos":         { nombre?, rol?, email?, telefono? },          # opcional
        "restricciones": { dias_acceso?, hora_inicio?, ... },           # opcional
        "permisos":      { id_permisos: [int, ...] }                    # opcional
      }

    Cada sección es independiente:
      - Si "datos" no viene → no se toca t_usuarios (datos).
      - Si "restricciones" no viene → no se tocan campos de restricción.
      - Si "permisos" no viene → no se toca r_usuario_permisos.
      - Si "permisos" viene con [] → DESASIGNA todos los permisos
        granulares (el usuario queda solo con los heredados del rol).

    Flujo transaccional (todo o nada):
      1. Validar usuario existe y pertenece a la empresa.
      2. UPDATE en t_usuarios para los campos de "datos" + "restricciones".
      3. Si vinieron permisos: DELETE+INSERT batch en r_usuario_permisos.
      4. Auditoría con el diff.
      5. Commit.

    Por qué reemplazo total de permisos en lugar de diff:
      Es más predecible. El cliente envía la lista FINAL que debe quedar;
      el backend la materializa. Hacer diff requiere comparar con el
      estado actual y abre puertos para race conditions (alguien edita
      al mismo tiempo). El costo (DELETE+INSERT en una tabla con pocas
      filas por usuario) es marginal.

    Args:
        id_usuario:        ID del usuario a editar.
        id_empresa:        Empresa del contexto (validación de pertenencia).
        payload:           Dict ya validado por UpdateUserSchema.
        id_usuario_cambio: ID del usuario que ejecuta (para auditoría).

    Returns:
        Tupla (data, error). Mismo patrón que create_usuario_completo.
    """
    connection = None
    cursor = None

    datos = payload.get("datos") or {}
    restricciones = payload.get("restricciones") or {}
    permisos_seccion = payload.get("permisos")  # None si no vino

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # ─── 1. Validar que el usuario existe y pertenece a la empresa ──────
        # Hacemos SELECT con FOR UPDATE para lockear la fila y prevenir
        # race conditions con otra request concurrent que esté también
        # editando este mismo usuario.
        cursor.execute(
            """
            SELECT id, id_rol, dias_acceso, hora_inicio_acceso,
                   hora_fin_acceso, id_grupo_unidades, id_cliente, dias_consulta,
                   nombre, telefono
              FROM t_usuarios
             WHERE id         = %s
               AND id_empresa = %s
               AND status     = 1
             FOR UPDATE
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()
        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe o no pertenece a tu empresa",
            }

        # Snapshot del estado anterior (para auditoría)
        estado_anterior = {
            "id_rol": row[1],
            "dias_acceso": row[2],
            "hora_inicio_acceso": str(row[3]) if row[3] else None,
            "hora_fin_acceso": str(row[4]) if row[4] else None,
            "id_grupo_unidades": row[5],
            "id_cliente": row[6],
            "dias_consulta": row[7],
            "nombre": row[8],
            "telefono": row[9],
        }

        # ─── 2. Construir SET dinámico para t_usuarios ──────────────────────
        # Solo campos que vinieron Y están en la whitelist.
        set_clauses = []
        values = []

        # Procesar sección "datos"
        for field, value in datos.items():
            if field not in _EDITABLE_DATOS_FIELDS:
                continue  # filtrar campo no permitido (defensa en profundidad)

            if field == "rol":
                # Resolver id_rol dinámicamente — no hardcodear IDs
                id_rol_nuevo = _get_rol_id_by_clave(cursor, value)
                if id_rol_nuevo is None:
                    return None, {
                        "code": "ROL_NOT_CONFIGURED",
                        "message": f"El rol '{value}' no está configurado",
                    }
                set_clauses.append("id_rol = %s")
                values.append(id_rol_nuevo)

                # También actualizar el campo legacy 'perfil' para mantener consistencia
                perfil = (
                    _PERFIL_ADMIN_EMPRESA
                    if value == _ROL_ADMIN_EMPRESA_CLAVE
                    else _PERFIL_USUARIO
                )
                set_clauses.append("perfil = %s")
                values.append(perfil)

            elif field == "email":
                # En este sistema "usuario" funciona como email login.
                # NO se actualiza el "usuario" porque es identificador inmutable.
                # Si el cliente manda email, lo persistimos en telefono o lo
                # ignoramos. Por simplicidad lo ignoramos — no hay columna
                # email separada en t_usuarios (decisión del legacy).
                continue

            else:
                set_clauses.append(f"{field} = %s")
                values.append(value)

        # Procesar sección "restricciones"
        for field, value in restricciones.items():
            if field not in _EDITABLE_RESTRICCIONES_FIELDS:
                continue
            set_clauses.append(f"{field} = %s")
            values.append(value)

        # Si hay cambios de datos/restricciones, ejecutar el UPDATE
        if set_clauses:
            set_clauses.append("fecha_cambio = NOW()")
            set_clauses.append("id_usuario_cambio = %s")
            values.extend([id_usuario_cambio, id_usuario, id_empresa])

            cursor.execute(
                f"""
                UPDATE t_usuarios
                   SET {', '.join(set_clauses)}
                 WHERE id         = %s
                   AND id_empresa = %s
                """,
                tuple(values),
            )

        # ─── 3. Reemplazar permisos si vino la sección ─────────────────────
        # None = no tocar. [] = desasignar todos. [1,2,3] = reemplazar.
        if permisos_seccion is not None:
            id_permisos_nuevos = permisos_seccion.get("id_permisos", [])

            # Validar existencia de los permisos antes de tocar nada.
            # Si alguno no existe, abortar la operación entera.
            if id_permisos_nuevos:
                cursor.execute(
                    """
                    SELECT id_permiso FROM t_permisos
                     WHERE id_permiso = ANY(%s)
                       AND status     = 1
                    """,
                    (id_permisos_nuevos,),
                )
                existentes = {r[0] for r in cursor.fetchall()}
                inexistentes = set(id_permisos_nuevos) - existentes
                if inexistentes:
                    return None, {
                        "code": "INVALID_PERMISSIONS",
                        "message": (
                            f"Los siguientes permisos no existen o están inactivos: "
                            f"{sorted(inexistentes)}"
                        ),
                    }

            # DELETE + INSERT batch (reemplazo total)
            cursor.execute(
                "DELETE FROM r_usuario_permisos WHERE id_usuario = %s AND id_empresa = %s",
                (id_usuario, id_empresa),
            )

            if id_permisos_nuevos:
                cursor.executemany(
                    """
                    INSERT INTO r_usuario_permisos
                        (id_usuario, id_empresa, id_permiso,
                         id_usuario_registro, fecha_registro)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    [
                        (id_usuario, id_empresa, id_perm, id_usuario_cambio)
                        for id_perm in id_permisos_nuevos
                    ],
                )

        # ─── 4. Auditoría ────────────────────────────────────────────────────
        # Acción "UPDATE_USUARIO" (15 chars — cabe en VARCHAR(20)).
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion="UPDATE_USUARIO",
            datos_anteriores=estado_anterior,
            datos_nuevos={
                "datos_modificados": list(datos.keys()) if datos else [],
                "restricciones_modificadas": (
                    list(restricciones.keys()) if restricciones else []
                ),
                "permisos_actualizados": permisos_seccion is not None,
                "id_empresa": id_empresa,
            },
        )

        # _registrar_auditoria hace commit internamente.

        return (
            {
                "id_usuario": id_usuario,
                "actualizado": True,
            },
            None,
        )

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en update_user id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible actualizar el usuario",
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── Inhabilitar usuario (soft-delete) ────────────────────────────────────────


def inhabilitar_user(id_usuario: int, id_empresa: int, id_usuario_cambio: int):
    """
    Inhabilita (soft-delete) un usuario activo: status=1 → status=0.

    El usuario:
      - Desaparece del listado del catálogo (que filtra status=1).
      - NO podrá iniciar sesión (auth_service filtra por status=1).
      - Sus datos permanecen en BD para auditoría.
      - Sus permisos en r_usuario_permisos NO se borran — al reactivar,
        recupera la configuración exacta que tenía.

    Reglas de seguridad:
      - El usuario debe pertenecer a la empresa del contexto.
      - NO se puede auto-inhabilitar (un sudo_erp/admin cerraría su
        propia sesión por accidente). El frontend debe ocultar la opción
        para uno mismo, pero el backend valida también.
      - NO se puede inhabilitar a un sudo_erp desde catálogos. Si por
        alguna razón un sudo_erp termina en t_usuarios.id_empresa = X
        (raro pero posible), no debe ser inhabilitable desde el catálogo
        de su misma empresa.

    Reactivar (status 0 → 1) NO se hace desde aquí — es exclusivo del
    Panel ERP porque el catálogo no muestra usuarios inhabilitados.

    Returns:
        Tupla (data, error).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Verificar que el usuario existe, está activo y pertenece a la empresa
        cursor.execute(
            """
                SELECT u.id, r.clave AS rol
                FROM t_usuarios u
                LEFT JOIN t_roles r ON r.id_rol = u.id_rol
                WHERE u.id         = %s
                AND u.id_empresa = %s
                AND u.status     = 1
                FOR UPDATE OF u
            """,
            (id_usuario, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None, {
                "code": "USER_NOT_FOUND",
                "message": "El usuario no existe, no pertenece a tu empresa o ya está inhabilitado",
            }

        rol_objetivo = row[1]

        # 2. Reglas de seguridad
        if id_usuario == id_usuario_cambio:
            return None, {
                "code": "CANNOT_INHABILITAR_SELF",
                "message": "No puedes inhabilitarte a ti mismo",
            }

        if rol_objetivo == "sudo_erp":
            # Defensa en profundidad: el sudo_erp NO debería estar en
            # t_usuarios.id_empresa con datos normales, pero por si acaso.
            return None, {
                "code": "CANNOT_INHABILITAR_SUDO",
                "message": "No se puede inhabilitar al administrador del sistema",
            }

        # 3. Soft-delete
        cursor.execute(
            """
            UPDATE t_usuarios
               SET status            = 0,
                   fecha_cambio      = NOW(),
                   id_usuario_cambio = %s
             WHERE id         = %s
               AND id_empresa = %s
               AND status     = 1
            """,
            (id_usuario_cambio, id_usuario, id_empresa),
        )

        # 4. Auditoría — "INHABILITAR" (12 chars, cabe holgado).
        _registrar_auditoria(
            cursor=cursor,
            connection=connection,
            id_usuario=id_usuario_cambio,
            entidad="usuario",
            id_entidad=id_usuario,
            accion="INHABILITAR",
            datos_anteriores={"status": 1, "rol": rol_objetivo},
            datos_nuevos={"status": 0, "id_empresa": id_empresa},
        )

        return (
            {
                "id_usuario": id_usuario,
                "inhabilitado": True,
            },
            None,
        )

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en inhabilitar_user id_usuario=%s id_empresa=%s: %s",
            id_usuario,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible inhabilitar el usuario",
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
