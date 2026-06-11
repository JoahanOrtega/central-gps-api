"""
itinerary_group_service.py — Lógica de negocio de Grupos y Roles de Itinerarios.

Grupos:
  - get_groups()          → listado con conteo de itinerarios
  - get_group_by_id()     → detalle con ids de itinerarios miembros
  - create_group()        → crear grupo opcionalmente con itinerarios
  - update_group()        → actualizar nombre/observaciones y miembros
  - delete_group()        → soft-delete

Roles:
  - get_roles()           → listado con conteo de itinerarios y asignaciones
  - get_role_by_id()      → detalle con secuencia de días completa
  - create_role()         → crear rol con su secuencia de días
  - update_role()         → actualizar reemplazando la secuencia
  - delete_role()         → soft-delete
"""

import logging
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers internos
# ══════════════════════════════════════════════════════════════════════════════


def _row_to_dict(cur, row: tuple) -> dict:
    """Convierte tupla de BD a dict usando cur.description."""
    return dict(zip([d[0] for d in cur.description], row))


def _serialize_dates(record: dict) -> dict:
    """Convierte DATE/TIMESTAMP a ISO string para la respuesta JSON."""
    for campo in (
        "fecha_inicio_rol",
        "fecha_asignacion",
        "fecha_baja",
        "fecha_registro",
        "fecha_cambio",
    ):
        v = record.get(campo)
        if v is not None and hasattr(v, "isoformat"):
            record[campo] = v.strftime("%Y-%m-%dT%H:%M:%S-06:00")
    return record


# ══════════════════════════════════════════════════════════════════════════════
# GRUPOS DE ITINERARIOS
# ══════════════════════════════════════════════════════════════════════════════


def get_groups(id_empresa: int, search: str = "") -> list[dict]:
    """
    Listado de grupos de itinerarios con conteo de itinerarios miembros.

    Respuesta:
    [
      {
        id_grupo_itinerarios, nombre, observaciones,
        total_itinerarios,    ← conteo real (no denormalizado)
        id_cliente, status
      },
      ...
    ]
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa]
            search_clause = ""
            if search:
                search_clause = "AND (g.nombre ILIKE %s OR g.observaciones ILIKE %s)"
                like = f"%{search}%"
                params += [like, like]

            cur.execute(
                f"""
                SELECT
                    g.id_grupo_itinerarios,
                    g.nombre,
                    g.observaciones,
                    g.id_cliente,
                    c.nombre AS cliente,
                    g.status,
                    COUNT(r.id_itinerario) AS total_itinerarios
                FROM t_grupos_itinerarios g
                LEFT JOIN t_clientes c ON c.id_cliente = g.id_cliente
                LEFT JOIN r_grupo_itinerarios_itinerarios r
                       ON r.id_grupo_itinerarios = g.id_grupo_itinerarios
                WHERE g.id_empresa = %s
                  AND g.status = 1
                  {search_clause}
                GROUP BY g.id_grupo_itinerarios, c.nombre
                ORDER BY g.nombre
                """,
                params,
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        release_db_connection(conn)


def get_group_by_id(id_grupo: int, id_empresa: int) -> dict | None:
    """
    Detalle de un grupo con la lista de ids de itinerarios miembros.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.id_grupo_itinerarios, g.nombre, g.observaciones,
                       g.id_cliente, g.status
                FROM t_grupos_itinerarios g
                WHERE g.id_grupo_itinerarios = %s
                  AND g.id_empresa = %s
                  AND g.status = 1
                """,
                (id_grupo, id_empresa),
            )
            row = cur.fetchone()
            if not row:
                return None

            grupo = _row_to_dict(cur, row)

            # IDs de itinerarios miembros
            cur.execute(
                """
                SELECT r.id_itinerario
                FROM r_grupo_itinerarios_itinerarios r
                INNER JOIN t_itinerarios i ON i.id_itinerario = r.id_itinerario
                WHERE r.id_grupo_itinerarios = %s AND i.status = 1
                ORDER BY i.turno, i.hora_inicio
                """,
                (id_grupo,),
            )
            grupo["id_itinerarios"] = [r[0] for r in cur.fetchall()]
            return grupo
    finally:
        release_db_connection(conn)


def create_group(payload: dict, id_empresa: int, id_usuario: int) -> int:
    """
    Crea un grupo y opcionalmente asigna itinerarios iniciales.

    Returns:
        id_grupo_itinerarios del registro creado.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO t_grupos_itinerarios
                    (id_empresa, id_cliente, nombre, observaciones, id_usuario_registro)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id_grupo_itinerarios
                """,
                (
                    id_empresa,
                    payload.get("id_cliente") or None,
                    payload["nombre"],
                    payload.get("observaciones") or None,
                    id_usuario,
                ),
            )
            id_grupo = cur.fetchone()[0]

            # Asignar itinerarios iniciales si vienen en el payload
            _sync_group_members(cur, id_grupo, payload.get("id_itinerarios", []))

            conn.commit()
            return id_grupo
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def update_group(
    id_grupo: int, payload: dict, id_empresa: int, id_usuario: int
) -> bool:
    """
    Actualiza un grupo. Reemplaza su lista de itinerarios miembros.

    Returns:
        True si se actualizó, False si no existe o no pertenece a la empresa.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_grupos_itinerarios SET
                    nombre            = COALESCE(%s, nombre),
                    observaciones     = %s,
                    id_cliente        = %s,
                    fecha_cambio      = CURRENT_TIMESTAMP,
                    id_usuario_cambio = %s
                WHERE id_grupo_itinerarios = %s
                  AND id_empresa = %s
                  AND status = 1
                """,
                (
                    payload.get("nombre"),
                    payload.get("observaciones") or None,
                    payload.get("id_cliente") or None,
                    id_usuario,
                    id_grupo,
                    id_empresa,
                ),
            )
            if cur.rowcount == 0:
                return False

            # Reemplazar miembros si vienen en el payload
            if "id_itinerarios" in payload:
                cur.execute(
                    "DELETE FROM r_grupo_itinerarios_itinerarios WHERE id_grupo_itinerarios = %s",
                    (id_grupo,),
                )
                _sync_group_members(cur, id_grupo, payload["id_itinerarios"])

            conn.commit()
            return True
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def delete_group(id_grupo: int, id_empresa: int) -> bool:
    """Soft-delete del grupo (status=0). No toca los itinerarios miembros."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_grupos_itinerarios SET status = 0
                WHERE id_grupo_itinerarios = %s
                  AND id_empresa = %s AND status = 1
                """,
                (id_grupo, id_empresa),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        release_db_connection(conn)


def _sync_group_members(cur, id_grupo: int, ids_itinerarios: list[int]) -> None:
    """
    Helper privado: inserta los itinerarios miembros de un grupo.
    Asume que la tabla ya está limpia (el caller hizo DELETE antes si aplica).
    """
    for id_itinerario in ids_itinerarios:
        cur.execute(
            """
            INSERT INTO r_grupo_itinerarios_itinerarios
                (id_grupo_itinerarios, id_itinerario)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (id_grupo, id_itinerario),
        )


# ══════════════════════════════════════════════════════════════════════════════
# ROLES DE ITINERARIOS
# ══════════════════════════════════════════════════════════════════════════════


def get_roles(id_empresa: int, search: str = "") -> list[dict]:
    """
    Listado de roles con conteo real de itinerarios y asignaciones activas.

    Respuesta:
    [
      {
        id_rol_itinerarios, clave, nombre, fecha_inicio_rol, dias_duracion,
        total_itinerarios,   ← COUNT real (excluye descansos)
        total_asignaciones,  ← unidades actualmente asignadas
        status
      },
      ...
    ]
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa]
            search_clause = ""
            if search:
                search_clause = "AND (r.clave ILIKE %s OR r.nombre ILIKE %s)"
                like = f"%{search}%"
                params += [like, like]

            cur.execute(
                f"""
                SELECT
                    r.id_rol_itinerarios,
                    r.clave,
                    r.nombre,
                    r.fecha_inicio_rol,
                    r.dias_duracion,
                    r.observaciones,
                    r.status,
                    COUNT(DISTINCT ri.id_itinerario)
                        FILTER (WHERE ri.es_descanso = FALSE) AS total_itinerarios,
                    COUNT(DISTINCT ra.id_asignacion)
                        FILTER (WHERE ra.status = 1 AND ra.fecha_baja IS NULL) AS total_asignaciones
                FROM t_roles_itinerarios r
                LEFT JOIN r_rol_itinerarios ri
                       ON ri.id_rol_itinerarios = r.id_rol_itinerarios
                LEFT JOIN r_rol_asignacion_unidades ra
                       ON ra.id_rol_itinerarios = r.id_rol_itinerarios
                WHERE r.id_empresa = %s
                  AND r.status = 1
                  {search_clause}
                GROUP BY r.id_rol_itinerarios
                ORDER BY r.clave, r.nombre
                """,
                params,
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

        result = []
        for row in rows:
            record = dict(zip(cols, row))
            _serialize_dates(record)
            result.append(record)
        return result
    finally:
        release_db_connection(conn)


def get_role_by_id(id_rol: int, id_empresa: int) -> dict | None:
    """
    Detalle completo de un rol con su secuencia de días.

    Respuesta:
    {
      id_rol_itinerarios, clave, nombre, fecha_inicio_rol, dias_duracion, ...
      "dias": [
        {
          "dia_rol": 1, "orden": 1, "es_descanso": false,
          "id_itinerario": 5, "turno": "1", "hora_inicio": "06:00",
          "hora_fin": "07:30", "nombre_ruta": "Ruta Centro"
        },
        ...
      ]
    }
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Datos del rol
            cur.execute(
                """
                SELECT id_rol_itinerarios, clave, nombre, fecha_inicio_rol,
                       dias_duracion, observaciones, status
                FROM t_roles_itinerarios
                WHERE id_rol_itinerarios = %s
                  AND id_empresa = %s
                  AND status = 1
                """,
                (id_rol, id_empresa),
            )
            row = cur.fetchone()
            if not row:
                return None

            rol = _serialize_dates(_row_to_dict(cur, row))

            # Secuencia de días con detalle del itinerario
            cur.execute(
                """
                SELECT
                    ri.dia_rol,
                    ri.orden,
                    ri.es_descanso,
                    ri.id_itinerario,
                    i.turno,
                    i.hora_inicio,
                    i.hora_fin,
                    i.dias,
                    i.total_paradas,
                    r.nombre AS nombre_ruta,
                    r.clave  AS clave_ruta,
                    l.tipo_logistica
                FROM r_rol_itinerarios ri
                LEFT JOIN t_itinerarios i     ON i.id_itinerario = ri.id_itinerario
                LEFT JOIN t_rutas r           ON r.id_ruta = i.id_ruta
                LEFT JOIN t_logisticas_ruta l ON l.id_logistica_ruta = i.id_logistica_ruta
                WHERE ri.id_rol_itinerarios = %s
                ORDER BY ri.dia_rol, ri.orden
                """,
                (id_rol,),
            )
            dcols = [d[0] for d in cur.description]
            dias = []
            for drow in cur.fetchall():
                dia = dict(zip(dcols, drow))
                # Convertir TIME a string
                for campo_hora in ("hora_inicio", "hora_fin"):
                    v = dia.get(campo_hora)
                    if v is not None and hasattr(v, "strftime"):
                        dia[campo_hora] = v.strftime("%H:%M")
                dias.append(dia)

            rol["dias"] = dias
            return rol
    finally:
        release_db_connection(conn)


def create_role(payload: dict, id_empresa: int, id_usuario: int) -> int:
    """
    Crea un rol con su secuencia de días en una sola transacción.

    Returns:
        id_rol_itinerarios del registro creado.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO t_roles_itinerarios
                    (id_empresa, clave, nombre, fecha_inicio_rol,
                     dias_duracion, observaciones, id_usuario_registro)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id_rol_itinerarios
                """,
                (
                    id_empresa,
                    payload.get("clave") or None,
                    payload["nombre"],
                    payload.get("fecha_inicio_rol") or None,
                    payload.get("dias_duracion", 0),
                    payload.get("observaciones") or None,
                    id_usuario,
                ),
            )
            id_rol = cur.fetchone()[0]

            _insert_rol_dias(cur, id_rol, payload.get("dias", []))

            conn.commit()
            return id_rol
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def update_role(id_rol: int, payload: dict, id_empresa: int, id_usuario: int) -> bool:
    """
    Actualiza un rol. Reemplaza su secuencia de días completa.

    Returns:
        True si se actualizó, False si no existe o no pertenece a la empresa.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_roles_itinerarios SET
                    clave             = COALESCE(%s, clave),
                    nombre            = COALESCE(%s, nombre),
                    fecha_inicio_rol  = %s,
                    dias_duracion     = COALESCE(%s, dias_duracion),
                    observaciones     = %s,
                    fecha_cambio      = CURRENT_TIMESTAMP,
                    id_usuario_cambio = %s
                WHERE id_rol_itinerarios = %s
                  AND id_empresa = %s
                  AND status = 1
                """,
                (
                    payload.get("clave"),
                    payload.get("nombre"),
                    payload.get("fecha_inicio_rol") or None,
                    payload.get("dias_duracion"),
                    payload.get("observaciones") or None,
                    id_usuario,
                    id_rol,
                    id_empresa,
                ),
            )
            if cur.rowcount == 0:
                return False

            # Reemplazar secuencia de días si viene en el payload
            if "dias" in payload:
                cur.execute(
                    "DELETE FROM r_rol_itinerarios WHERE id_rol_itinerarios = %s",
                    (id_rol,),
                )
                _insert_rol_dias(cur, id_rol, payload["dias"])

            conn.commit()
            return True
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def delete_role(id_rol: int, id_empresa: int) -> bool:
    """
    Soft-delete del rol (status=0).
    El CASCADE de la FK elimina r_rol_itinerarios y r_rol_asignacion_unidades.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_roles_itinerarios SET status = 0
                WHERE id_rol_itinerarios = %s
                  AND id_empresa = %s AND status = 1
                """,
                (id_rol, id_empresa),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        release_db_connection(conn)


def _insert_rol_dias(cur, id_rol: int, dias: list[dict]) -> None:
    """
    Helper privado: inserta la secuencia de días de un rol.
    Cada elemento de `dias` puede ser un itinerario normal o un descanso.

    Estructura esperada de cada elemento:
    {
      "dia_rol":       1,       ← día del ciclo (obligatorio)
      "orden":         1,       ← posición dentro del día (default 1)
      "es_descanso":   false,   ← si es día de descanso (default false)
      "id_itinerario": 5        ← requerido si es_descanso=false
    }
    """
    for dia in dias:
        cur.execute(
            """
            INSERT INTO r_rol_itinerarios
                (id_rol_itinerarios, id_itinerario, dia_rol, orden, es_descanso)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                id_rol,
                dia.get("id_itinerario") or None,
                dia["dia_rol"],
                dia.get("orden", 1),
                dia.get("es_descanso", False),
            ),
        )
