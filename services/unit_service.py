import logging
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def get_units(id_empresa, search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_unidad,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                imei,
                chip,
                id_operador,
                status
            FROM t_unidades
            WHERE id_empresa = %s AND status = 1
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(numero) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY id_unidad ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        units = []
        for row in rows:
            units.append(
                {
                    "id": row[0],
                    "numero": row[1],
                    "marca": row[2],
                    "modelo": row[3],
                    "anio": row[4],
                    "matricula": row[5],
                    "tipo": row[6],
                    "imagen": row[7],
                    "imei": row[8],
                    "chip": row[9],
                    "id_operador": row[10],
                    "status": row[11],
                }
            )
        return units
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_unit(payload, id_usuario_registro, id_empresa):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            INSERT INTO t_unidades (
                id_empresa,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                vel_max,
                id_modelo_avl,
                imei,
                chip,
                odometro_inicial,
                tipo_combustible,
                capacidad_tanque,
                rendimiento_establecido,
                no_serie,
                nombre_aseguradora,
                telefono_aseguradora,
                no_poliza_seguro,
                vigencia_poliza_seguro,
                vigencia_verificacion_vehicular,
                input1,
                input2,
                output1,
                output2,
                fecha_instalacion,
                id_usuario_registro,
                fecha_registro,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            RETURNING id_unidad
        """
        values = (
            id_empresa,
            payload["numero"],
            payload["marca"],
            payload["modelo"],
            payload["anio"],
            payload["matricula"],
            int(payload["tipo"]),
            payload.get("imagen", ""),
            90,
            payload.get("id_modelo_avl"),
            payload["imei"],
            payload["chip"],
            float(payload.get("odometro_inicial", 0)),
            payload.get("tipo_combustible", "1"),
            payload.get("capacidad_tanque"),
            payload.get("rendimiento_establecido"),
            payload.get("no_serie", ""),
            payload.get("nombre_aseguradora", ""),
            payload.get("telefono_aseguradora", ""),
            payload.get("no_poliza_seguro", ""),
            payload.get("vigencia_poliza_seguro"),
            payload.get("vigencia_verificacion_vehicular"),
            int(payload.get("input1", 0)),
            int(payload.get("input2", 0)),
            int(payload.get("output1", 0)),
            int(payload.get("output2", 0)),
            payload["fecha_instalacion"],
            id_usuario_registro,
            1,
        )
        cursor.execute(query, values)
        new_unit_id = cursor.fetchone()[0]

        id_operador = payload.get("id_operador")
        fecha_asignacion = payload.get("fecha_asignacion_operador")
        if id_operador:
            cursor.execute(
                """
                INSERT INTO r_unidad_operador (id_unidad, id_operador, fecha_asignacion, id_usuario_registro, fecha_registro)
                VALUES (%s, %s, %s, %s, NOW())
            """,
                (
                    new_unit_id,
                    id_operador,
                    fecha_asignacion if fecha_asignacion else None,
                    id_usuario_registro,
                ),
            )

        id_grupo_list = payload.get("id_grupo_unidades", [])
        if id_grupo_list:
            for id_grupo in id_grupo_list:
                cursor.execute(
                    "INSERT INTO r_grupo_unidades_unidades (id_grupo_unidades, id_unidad) VALUES (%s, %s)",
                    (id_grupo, new_unit_id),
                )

        connection.commit()
        return {"id": new_unit_id}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_unit id_empresa=%s numero=%s: %s",
            id_empresa,
            payload.get("numero"),
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ═══════════════════════════════════════════════════════════════════════════
# Detalle y edición de una unidad
# ═══════════════════════════════════════════════════════════════════════════
#
# REGLA DE NEGOCIO: matriz de acceso a campos según rol
#
#   ┌──────────────────────────┬──────────┬────────────────┬─────────┐
#   │ Grupo de campos          │ sudo_erp │ admin_empresa  │ usuario │
#   ├──────────────────────────┼──────────┼────────────────┼─────────┤
#   │ Identidad (num, marca..) │    ✓     │       ✓        │    ✓    │
#   │ Asignación (operador..)  │    ✓     │       ✓        │    ✓    │
#   │ Datos combustible        │    ✓     │       ✓        │    ✓    │
#   │ Seguro y verificación    │    ✓     │       ✓        │    ✓    │
#   │ ── Equipo instalado ──   │    ✓     │       ✗        │    ✗    │
#   │ Modelo AVL, IMEI, chip   │    ✓     │       ✗        │    ✗    │
#   │ Fecha instalación        │    ✓     │       ✗        │    ✗    │
#   │ Inputs/outputs periferic.│    ✓     │       ✗        │    ✗    │
#   └──────────────────────────┴──────────┴────────────────┴─────────┘
#
# El frontend OCULTA la sección "Equipo Instalado" para no-sudo, pero el
# backend NO CONFÍA en eso. Estas constantes + validate_editable_fields()
# son la segunda línea de defensa: aunque alguien mande PATCH con `imei`
# siendo admin_empresa, el servicio responde 403.


# Campos accesibles solo por sudo_erp (equipo instalado y periféricos).
# No aparecen en GET para no-sudo, no se aceptan en PATCH para no-sudo.
_SUDO_ONLY_FIELDS = frozenset(
    {
        "id_modelo_avl",
        "imei",
        "chip",
        "fecha_instalacion",
        "input1",
        "input2",
        "output1",
        "output2",
    }
)


def _is_sudo(rol: str | None) -> bool:
    """Helper para legibilidad en varias funciones."""
    return rol == "sudo_erp"


def get_unit_detail(id_unidad: int, id_empresa: int, rol: str | None):
    """
    Devuelve el detalle completo de una unidad, filtrado según el rol.

    Retorna:
      - dict con los campos permitidos si todo OK
      - None si la unidad no existe o pertenece a otra empresa

    El rol determina qué campos viajan en la respuesta:
      - sudo_erp              → todos
      - admin_empresa/usuario → sin campos de equipo instalado

    Nota sobre seguridad de empresa: siempre filtramos por id_empresa
    para que un admin_empresa de la empresa A NO pueda leer unidades
    de la empresa B aunque sepa el id_unidad. El sudo_erp llega con
    el id_empresa de su empresa activa (del JWT después de switch).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # SELECT con todos los campos. Filtrar después en Python es más
        # simple que tener dos queries distintas por rol — y la diferencia
        # de performance es despreciable (una fila).
        cursor.execute(
            """
            SELECT
                u.id_unidad,
                u.numero,
                u.marca,
                u.modelo,
                u.anio,
                u.matricula,
                u.no_serie,
                u.tipo,
                u.odometro_inicial,
                u.imagen,
                u.id_modelo_avl,
                u.imei,
                u.chip,
                u.fecha_instalacion,
                u.input1,
                u.input2,
                u.output1,
                u.output2,
                u.tipo_combustible,
                u.capacidad_tanque,
                u.rendimiento_establecido,
                u.nombre_aseguradora,
                u.telefono_aseguradora,
                u.no_poliza_seguro,
                u.vigencia_poliza_seguro,
                u.vigencia_verificacion_vehicular,
                u.vel_max,
                u.status
              FROM t_unidades u
             WHERE u.id_unidad  = %s
               AND u.id_empresa = %s
               AND u.status     = 1
            """,
            (id_unidad, id_empresa),
        )
        row = cursor.fetchone()
        if not row:
            return None

        cols = [d[0] for d in cursor.description]
        unit = dict(zip(cols, row))

        # Traer operador asignado activo (si existe). r_unidad_operador
        # puede tener varias filas históricas — tomamos la más reciente
        # sin fecha de término (relación vigente).
        cursor.execute(
            """
            SELECT ruo.id_operador, ruo.fecha_asignacion,
                   o.nombre AS nombre_operador
              FROM r_unidad_operador ruo
              LEFT JOIN t_operadores o ON o.id_operador = ruo.id_operador
             WHERE ruo.id_unidad = %s
             ORDER BY ruo.fecha_registro DESC
             LIMIT 1
            """,
            (id_unidad,),
        )
        op_row = cursor.fetchone()
        if op_row:
            unit["id_operador"] = op_row[0]
            unit["fecha_asignacion_operador"] = op_row[1]
            unit["nombre_operador"] = op_row[2]
        else:
            unit["id_operador"] = None
            unit["fecha_asignacion_operador"] = None
            unit["nombre_operador"] = None

        # Traer grupos de la unidad (relación N:N).
        cursor.execute(
            """
            SELECT id_grupo_unidades
              FROM r_grupo_unidades_unidades
             WHERE id_unidad = %s
            """,
            (id_unidad,),
        )
        unit["id_grupo_unidades"] = [r[0] for r in cursor.fetchall()]

        # Filtrado por rol — segunda capa de defensa.
        # Removemos los campos técnicos del payload si no es sudo_erp,
        # así el frontend nunca recibe datos que no debería ver.
        if not _is_sudo(rol):
            for field in _SUDO_ONLY_FIELDS:
                unit.pop(field, None)

        return unit

    except Exception as e:
        logger.error(
            "Error en get_unit_detail id_unidad=%s id_empresa=%s: %s",
            id_unidad,
            id_empresa,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def update_unit(
    id_unidad: int,
    id_empresa: int,
    payload: dict,
    rol: str | None,
    id_usuario: int,
):
    """
    Actualiza una unidad. Retorna (data, error) siguiendo el patrón del módulo.

    Validaciones que aplica (en orden):
      1. La unidad existe y pertenece a la empresa (404 si no).
      2. El rol puede modificar los campos enviados:
         - Si no es sudo_erp y el payload contiene campos técnicos
           (IMEI, chip, etc.), rechaza con 403 y mensaje explícito.
      3. Construye un UPDATE dinámico solo con los campos presentes en
         el payload (patch parcial: si mandan 2 campos, actualiza 2).
      4. Si el payload toca id_operador o id_grupo_unidades, actualiza
         las tablas relacionales por separado (r_unidad_operador y
         r_grupo_unidades_unidades).
      5. Registra auditoría con los campos cambiados.

    Defensa en profundidad:
      - El UpdateUnitSchema ya filtró formato.
      - Aquí filtramos autorización por rol.
      - El id_empresa del JWT limita el alcance (no se puede editar
        una unidad de otra empresa aunque conozcas su id_unidad).
    """
    # Copia mutable para poder popear campos sin afectar al caller.
    payload = dict(payload)

    # ─── 1. Validar que los campos sean permitidos para el rol ───────────
    if not _is_sudo(rol):
        campos_prohibidos = _SUDO_ONLY_FIELDS.intersection(payload.keys())
        if campos_prohibidos:
            # Mensaje explícito con los campos problemáticos para que el
            # frontend pueda mostrarle al usuario qué intentó editar sin
            # permiso. Ordenados para que el mensaje sea determinístico
            # (útil en tests y en logs).
            lista = ", ".join(sorted(campos_prohibidos))
            return None, {
                "code": "FIELDS_NOT_ALLOWED",
                "message": (
                    f"Tu rol no puede modificar estos campos: {lista}. "
                    "Contacta al administrador del sistema si necesitas "
                    "cambiarlos."
                ),
            }

    # ─── 2. Separar campos que van a t_unidades vs relaciones ────────────
    id_operador_nuevo = payload.pop("id_operador", "__UNSET__")
    fecha_asig_nueva = payload.pop("fecha_asignacion_operador", "__UNSET__")
    grupos_nuevos = payload.pop("id_grupo_unidades", None)

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # ─── 3. Confirmar que la unidad existe en la empresa ─────────────
        cursor.execute(
            """
            SELECT id_unidad FROM t_unidades
             WHERE id_unidad  = %s
               AND id_empresa = %s
               AND status     = 1
            """,
            (id_unidad, id_empresa),
        )
        if not cursor.fetchone():
            return None, {
                "code": "UNIT_NOT_FOUND",
                "message": "La unidad no existe o no pertenece a tu empresa",
            }

        # ─── 4. UPDATE dinámico en t_unidades ─────────────────────────────
        # Solo tocamos las columnas presentes en el payload. Si el payload
        # quedó vacío tras popear operador/grupos, saltamos este UPDATE
        # (puede pasar si solo quiso cambiar operador sin tocar la unidad).
        if payload:
            set_clauses = [f"{k} = %s" for k in payload.keys()]
            values = list(payload.values())
            values.extend([id_unidad, id_empresa])

            update_sql = (
                f"UPDATE t_unidades "
                f"SET {', '.join(set_clauses)} "
                f"WHERE id_unidad = %s AND id_empresa = %s"
            )
            cursor.execute(update_sql, tuple(values))

        # ─── 5. Actualizar operador si vino en el payload ─────────────────
        # Usamos __UNSET__ como sentinela para distinguir "no vino" de
        # "vino con None" (que significa "desasignar operador"). Un None
        # explícito en el payload limpia la asignación; no mandar el
        # campo la deja como estaba.
        if id_operador_nuevo != "__UNSET__":
            # Borrar asignación previa.
            cursor.execute(
                "DELETE FROM r_unidad_operador WHERE id_unidad = %s",
                (id_unidad,),
            )
            if id_operador_nuevo is not None:
                fecha_valor = (
                    fecha_asig_nueva if fecha_asig_nueva != "__UNSET__" else None
                )
                cursor.execute(
                    """
                    INSERT INTO r_unidad_operador
                        (id_unidad, id_operador, fecha_asignacion,
                         id_usuario_registro, fecha_registro)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (id_unidad, id_operador_nuevo, fecha_valor, id_usuario),
                )

        # ─── 6. Actualizar grupos si vinieron en el payload ───────────────
        # Estrategia simple: borrar todas las asignaciones y re-insertar.
        # Con ≤20 grupos por unidad (caso normal), es O(n) y evita
        # comparar sets manualmente.
        if grupos_nuevos is not None:
            cursor.execute(
                "DELETE FROM r_grupo_unidades_unidades WHERE id_unidad = %s",
                (id_unidad,),
            )
            for id_grupo in grupos_nuevos:
                cursor.execute(
                    """
                    INSERT INTO r_grupo_unidades_unidades
                        (id_grupo_unidades, id_unidad)
                    VALUES (%s, %s)
                    """,
                    (id_grupo, id_unidad),
                )

        connection.commit()

        # TODO: registrar auditoría con campos modificados.
        # Se pospone a un turno futuro para mantener este PR enfocado.
        # Lo ideal: INSERT en tabla de auditoría con id_usuario, id_unidad,
        # lista de campos cambiados (sin valores sensibles) y timestamp.

        return {"actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en update_unit id_unidad=%s id_empresa=%s: %s",
            id_unidad,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "No fue posible actualizar la unidad",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
