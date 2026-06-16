"""
operator_group_service.py — CRUD de grupos de operadores.

Sigue el patrón de itinerary_group_service: with conn.cursor(), COUNT real
para el conteo de miembros (sin columna denormalizada), soft-delete con
status, y _sync_group_members para la relación N:M.
"""

import logging
from db.connection import get_db_connection, release_db_connection
from services.telemetry_service import to_app_iso

logger = logging.getLogger(__name__)


def get_operator_groups(id_empresa: int, search: str = "") -> list[dict]:
    """
    Lista grupos de operadores activos (status=1) con el conteo de miembros.

    El total de operadores se calcula con COUNT en vez de una columna
    denormalizada (misma decisión que grupos de itinerarios): evita
    desincronización al agregar/quitar miembros.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT
                    g.id_grupo_operadores,
                    g.id_empresa,
                    g.nombre,
                    g.observaciones,
                    g.fecha_registro,
                    g.id_usuario_registro,
                    g.fecha_cambio,
                    g.id_usuario_cambio,
                    COUNT(r.id_operador) AS total_operadores
                FROM t_grupos_operadores g
                LEFT JOIN r_grupo_operadores_operadores r
                    ON r.id_grupo_operadores = g.id_grupo_operadores
                WHERE g.id_empresa = %s
                  AND g.status     = 1
            """
            params = [id_empresa]
            if search:
                query += " AND LOWER(g.nombre) LIKE LOWER(%s)"
                params.append(f"%{search}%")
            query += """
                GROUP BY g.id_grupo_operadores
                ORDER BY g.id_grupo_operadores DESC
            """
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            return [
                {
                    "id_grupo_operadores": r[0],
                    "id_empresa": r[1],
                    "nombre": r[2],
                    "observaciones": r[3],
                    "fecha_registro": to_app_iso(r[4]) if r[4] else None,
                    "id_usuario_registro": r[5],
                    "fecha_cambio": to_app_iso(r[6]) if r[6] else None,
                    "id_usuario_cambio": r[7],
                    "total_operadores": r[8],
                }
                for r in rows
            ]
    finally:
        release_db_connection(conn)


def get_operator_group_by_id(id_grupo: int, id_empresa: int) -> dict | None:
    """Obtiene un grupo con la lista de IDs de sus operadores miembros."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id_grupo_operadores, id_empresa, nombre, observaciones,
                    fecha_registro, id_usuario_registro, fecha_cambio, id_usuario_cambio
                FROM t_grupos_operadores
                WHERE id_grupo_operadores = %s AND id_empresa = %s AND status = 1
                """,
                (id_grupo, id_empresa),
            )
            row = cur.fetchone()
            if row is None:
                return None

            cur.execute(
                """
                SELECT id_operador
                FROM r_grupo_operadores_operadores
                WHERE id_grupo_operadores = %s
                """,
                (id_grupo,),
            )
            miembros = [m[0] for m in cur.fetchall()]

            return {
                "id_grupo_operadores": row[0],
                "id_empresa": row[1],
                "nombre": row[2],
                "observaciones": row[3],
                "fecha_registro": to_app_iso(row[4]) if row[4] else None,
                "id_usuario_registro": row[5],
                "fecha_cambio": to_app_iso(row[6]) if row[6] else None,
                "id_usuario_cambio": row[7],
                "id_operadores": miembros,
            }
    finally:
        release_db_connection(conn)


def create_operator_group(payload: dict, id_empresa: int, id_usuario: int) -> int:
    """Crea un grupo y asigna los operadores iniciales si vienen en el payload."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO t_grupos_operadores
                    (id_empresa, nombre, observaciones, id_usuario_registro)
                VALUES (%s, %s, %s, %s)
                RETURNING id_grupo_operadores
                """,
                (
                    id_empresa,
                    payload["nombre"],
                    payload.get("observaciones") or None,
                    id_usuario,
                ),
            )
            id_grupo = cur.fetchone()[0]
            _sync_group_members(cur, id_grupo, payload.get("id_operadores", []))
            conn.commit()
            return id_grupo
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def update_operator_group(
    id_grupo: int, payload: dict, id_empresa: int, id_usuario: int
) -> bool:
    """Actualiza un grupo. Si viene id_operadores, reemplaza la lista de miembros."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_grupos_operadores SET
                    nombre            = COALESCE(%s, nombre),
                    observaciones     = %s,
                    fecha_cambio      = NOW(),
                    id_usuario_cambio = %s
                WHERE id_grupo_operadores = %s
                  AND id_empresa = %s
                  AND status = 1
                """,
                (
                    payload.get("nombre"),
                    payload.get("observaciones") or None,
                    id_usuario,
                    id_grupo,
                    id_empresa,
                ),
            )
            if cur.rowcount == 0:
                return False

            if "id_operadores" in payload:
                cur.execute(
                    "DELETE FROM r_grupo_operadores_operadores WHERE id_grupo_operadores = %s",
                    (id_grupo,),
                )
                _sync_group_members(cur, id_grupo, payload["id_operadores"])

            conn.commit()
            return True
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def delete_operator_group(id_grupo: int, id_empresa: int) -> bool:
    """Soft-delete del grupo (status=0). No toca a los operadores miembros."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_grupos_operadores SET status = 0
                WHERE id_grupo_operadores = %s
                  AND id_empresa = %s AND status = 1
                """,
                (id_grupo, id_empresa),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        release_db_connection(conn)


def _sync_group_members(cur, id_grupo: int, ids_operadores: list[int]) -> None:
    """
    Helper privado: inserta los operadores miembros de un grupo.
    Asume que la tabla ya está limpia (el caller hizo DELETE antes si aplica).
    """
    for id_operador in ids_operadores or []:
        cur.execute(
            """
            INSERT INTO r_grupo_operadores_operadores
                (id_grupo_operadores, id_operador)
            VALUES (%s, %s)
            ON CONFLICT (id_grupo_operadores, id_operador) DO NOTHING
            """,
            (id_grupo, id_operador),
        )
