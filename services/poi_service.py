import logging
from db.connection import get_db_connection, release_db_connection
from services.telemetry_service import to_app_iso

logger = logging.getLogger(__name__)


def get_pois(id_empresa, search=None):
    """
    Lista POIs activos de una empresa.

    Filtra por status=1 — los POIs eliminados (status=0 vía soft-delete)
    nunca aparecen en el listado del catálogo. Mantenerlos en BD permite
    auditoría histórica y restauración futura si se decide implementarla.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_poi,
                id_empresa,
                tipo_elemento,
                id_elemento,
                nombre,
                direccion,
                tipo_poi,
                tipo_marker,
                url_marker,
                marker_path,
                marker_color,
                icon,
                icon_color,
                lat,
                lng,
                radio,
                bounds,
                area,
                radio_color,
                polygon_path,
                polygon_color,
                observaciones,
                fecha_registro,
                id_usuario_registro,
                fecha_cambio,
                id_usuario_cambio
            FROM t_pois
            WHERE id_empresa = %s
              AND status     = 1
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY id_poi DESC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id_poi": row[0],
                    "id_empresa": row[1],
                    "tipo_elemento": row[2],
                    "id_elemento": row[3],
                    "nombre": row[4],
                    "direccion": row[5],
                    "tipo_poi": row[6],
                    "tipo_marker": row[7],
                    "url_marker": row[8],
                    "marker_path": row[9],
                    "marker_color": row[10],
                    "icon": row[11],
                    "icon_color": row[12],
                    "lat": float(row[13]) if row[13] is not None else None,
                    "lng": float(row[14]) if row[14] is not None else None,
                    "radio": row[15],
                    "bounds": row[16],
                    "area": row[17],
                    "radio_color": row[18],
                    "polygon_path": row[19],
                    "polygon_color": row[20],
                    "observaciones": row[21],
                    "fecha_registro": to_app_iso(row[22]) if row[22] else None,
                    "id_usuario_registro": row[23],
                    "fecha_cambio": to_app_iso(row[24]) if row[24] else None,
                    "id_usuario_cambio": row[25],
                }
            )
        return result
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_poi(payload, id_empresa, id_usuario_registro):
    # payload debe contener los campos del POI, id_empresa se toma del token
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            INSERT INTO t_pois (
                id_empresa,
                tipo_elemento,
                id_elemento,
                nombre,
                direccion,
                tipo_poi,
                tipo_marker,
                url_marker,
                marker_path,
                marker_color,
                icon,
                icon_color,
                lat,
                lng,
                radio,
                bounds,
                area,
                radio_color,
                polygon_path,
                polygon_color,
                observaciones,
                fecha_registro,
                id_usuario_registro
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            RETURNING id_poi
        """
        values = (
            id_empresa,
            payload.get("tipo_elemento", "poi"),
            payload.get("id_elemento"),
            payload.get("nombre"),
            payload.get("direccion"),
            payload.get("tipo_poi"),
            payload.get("tipo_marker", 1),
            payload.get("url_marker"),
            payload.get("marker_path"),
            payload.get("marker_color", "#000000"),
            payload.get("icon"),
            payload.get("icon_color", "#000000"),
            payload.get("lat"),
            payload.get("lng"),
            payload.get("radio"),
            payload.get("bounds"),
            payload.get("area"),
            payload.get("radio_color", "#000000"),
            payload.get("polygon_path"),
            payload.get("polygon_color", "#000000"),
            payload.get("observaciones"),
            id_usuario_registro,
        )
        cursor.execute(query, values)
        poi_id = cursor.fetchone()[0]
        save_poi_groups(
            cursor=cursor,
            id_poi=poi_id,
            group_ids=payload.get("id_grupo_pois", []),
        )
        connection.commit()
        return {"id_poi": poi_id}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_poi id_empresa=%s nombre=%s: %s",
            id_empresa,
            payload.get("nombre"),
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────────────────────────────────────
# Campos editables vía PATCH /pois/<id>
# ─────────────────────────────────────────────────────────────────────────────
# Set explícito en lugar de "lo que venga". Defensa en profundidad: el
# UpdatePoiSchema ya filtra campos no declarados, pero esta capa hace de
# segunda línea — si alguien agrega un campo nuevo al schema y olvida
# considerar si debe ser editable, este set lo bloquea hasta hacerlo explícito.
_UPDATABLE_POI_FIELDS = frozenset(
    {
        "nombre",
        "direccion",
        "observaciones",
        "tipo_poi",
        "tipo_marker",
        "lat",
        "lng",
        "radio",
        "bounds",
        "area",
        "url_marker",
        "marker_path",
        "marker_color",
        "icon",
        "icon_color",
        "radio_color",
        "polygon_path",
        "polygon_color",
    }
)


def update_poi(id_poi, id_empresa, payload, id_usuario_cambio):
    """
    Actualiza parcialmente un POI. Retorna (data, error) siguiendo el
    mismo patrón que update_unit en unit_service.

    Validaciones en orden:
      1. El POI existe, está activo (status=1) y pertenece a la empresa.
      2. Se filtran los campos del payload contra _UPDATABLE_POI_FIELDS.
      3. Se construye un UPDATE dinámico solo con los campos presentes.
      4. Si el payload trae id_grupo_pois, se reemplaza la asignación
         completa de grupos (DELETE + INSERT en r_grupo_pois_pois).

    Por qué reemplazar grupos en vez de hacer merge:
      Es más predecible. El cliente envía la lista final que debe quedar;
      el backend la materializa. Hacer merge requiere tres listas (agregar,
      quitar, mantener) y abre puertos para inconsistencias si el cliente
      no calcula bien las diferencias. El costo (DELETE+INSERT por POI
      al editarlo) es marginal comparado con la simplicidad ganada.

    No registramos auditoría aquí por ahora — el patrón actual del
    proyecto solo audita en módulos ERP. Si más adelante se decide
    auditar todo, agregar la llamada a _registrar_auditoria importándola
    de erp_service.
    """
    payload = dict(payload)

    # Separar grupos del resto: van a otra tabla, no a t_pois.
    grupos_nuevos = payload.pop("id_grupo_pois", None)

    # Filtrar solo campos editables en BD (defensa en profundidad).
    campos_validos = {k: v for k, v in payload.items() if k in _UPDATABLE_POI_FIELDS}

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # ─── 1. Verificar que el POI existe en la empresa y está activo ─────
        cursor.execute(
            """
            SELECT id_poi FROM t_pois
             WHERE id_poi     = %s
               AND id_empresa = %s
               AND status     = 1
            """,
            (id_poi, id_empresa),
        )
        if not cursor.fetchone():
            return None, {
                "code": "POI_NOT_FOUND",
                "message": "El POI no existe o no pertenece a tu empresa",
            }

        # ─── 2. UPDATE dinámico en t_pois ────────────────────────────────────
        # Si solo cambian grupos, campos_validos puede estar vacío y
        # saltamos este UPDATE. fecha_cambio se actualiza siempre que
        # haya algún campo — el cambio de grupos no cuenta como
        # "modificación del POI" en sí, sino como cambio relacional.
        if campos_validos:
            set_clauses = [f"{k} = %s" for k in campos_validos.keys()]
            set_clauses.append("fecha_cambio = NOW()")
            set_clauses.append("id_usuario_cambio = %s")
            values = list(campos_validos.values())
            values.extend([id_usuario_cambio, id_poi, id_empresa])

            update_sql = (
                f"UPDATE t_pois SET {', '.join(set_clauses)} "
                f"WHERE id_poi = %s AND id_empresa = %s"
            )
            cursor.execute(update_sql, tuple(values))

        # ─── 3. Reemplazar grupos si vinieron en el payload ──────────────────
        # None significa "no vino" — no tocar grupos.
        # Lista vacía [] significa "vino vacío" — desasignar todos los grupos.
        if grupos_nuevos is not None:
            cursor.execute(
                "DELETE FROM r_grupo_pois_pois WHERE id_poi = %s",
                (id_poi,),
            )
            if grupos_nuevos:
                save_poi_groups(cursor, id_poi, grupos_nuevos)

        connection.commit()

        return {"id_poi": id_poi, "actualizado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en update_poi id_poi=%s id_empresa=%s: %s",
            id_poi,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "Error interno al actualizar el POI",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def delete_poi(id_poi, id_empresa, id_usuario_cambio):
    """
    Soft-delete de un POI (status=1 → status=0).

    El POI no se elimina físicamente: queda en BD para auditoría e
    histórico. El listado get_pois() filtra por status=1 así que para
    el usuario es indistinguible de un DELETE real.

    Las relaciones en r_grupo_pois_pois NO se tocan: se conservan para
    permitir restauración futura si se decide implementar. Como el POI
    queda con status=0, los grupos que lo referencian simplemente no
    lo verán al hacer JOIN con WHERE p.status=1.

    Retorna (data, error) siguiendo el patrón de update_poi.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Verificación + UPDATE en una sola query atómica usando RETURNING.
        # Si el POI no existe, no pertenece a la empresa o ya está
        # eliminado, el UPDATE no afecta filas y RETURNING viene vacío.
        cursor.execute(
            """
            UPDATE t_pois
               SET status            = 0,
                   fecha_cambio      = NOW(),
                   id_usuario_cambio = %s
             WHERE id_poi     = %s
               AND id_empresa = %s
               AND status     = 1
            RETURNING id_poi
            """,
            (id_usuario_cambio, id_poi, id_empresa),
        )

        if not cursor.fetchone():
            return None, {
                "code": "POI_NOT_FOUND",
                "message": "El POI no existe o no pertenece a tu empresa",
            }

        connection.commit()
        return {"id_poi": id_poi, "eliminado": True}, None

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en delete_poi id_poi=%s id_empresa=%s: %s",
            id_poi,
            id_empresa,
            repr(e),
        )
        return None, {
            "code": "DATABASE_ERROR",
            "message": "Error interno al eliminar el POI",
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_poi_groups(id_empresa, search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            SELECT
                gp.id_grupo_pois,
                gp.id_empresa,
                gp.id_cliente,
                gp.nombre,
                COALESCE(COUNT(rgp.id_poi), 0) AS pois,
                gp.observaciones,
                gp.fecha_registro,
                gp.id_usuario_registro,
                gp.fecha_cambio,
                gp.id_usuario_cambio,
                gp.is_default
            FROM t_grupos_pois gp
            LEFT JOIN r_grupo_pois_pois rgp ON gp.id_grupo_pois = rgp.id_grupo_pois
            WHERE gp.id_empresa = %s
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(gp.nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += """
            GROUP BY gp.id_grupo_pois
            ORDER BY gp.id_grupo_pois DESC
        """
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id_grupo_pois": row[0],
                    "id_empresa": row[1],
                    "id_cliente": row[2],
                    "nombre": row[3],
                    "pois": row[4],
                    "observaciones": row[5],
                    "fecha_registro": to_app_iso(row[6]) if row[6] else None,
                    "id_usuario_registro": row[7],
                    "fecha_cambio": to_app_iso(row[8]) if row[8] else None,
                    "id_usuario_cambio": row[9],
                    "is_default": row[10],
                }
            )
        return result
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_poi_group(payload, id_empresa, id_usuario_registro):
    # payload contiene los datos del grupo
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            INSERT INTO t_grupos_pois (
                id_empresa,
                id_cliente,
                nombre,
                observaciones,
                is_default,
                fecha_registro,
                id_usuario_registro
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s)
            RETURNING id_grupo_pois
        """
        values = (
            id_empresa,
            payload.get("id_cliente"),
            payload.get("nombre"),
            payload.get("observaciones"),
            payload.get("is_default", False),
            id_usuario_registro,
        )
        cursor.execute(query, values)
        group_id = cursor.fetchone()[0]
        connection.commit()
        return {"id_grupo_pois": group_id}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_poi_group id_empresa=%s nombre=%s: %s",
            id_empresa,
            payload.get("nombre"),
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def save_poi_groups(cursor, id_poi, group_ids):
    """
    Inserta las relaciones POI ↔ grupos en r_grupo_pois_pois
    Esta función se reusa en create_poi y update_poi.
    """
    if not group_ids:
        return
    query = """
        INSERT INTO r_grupo_pois_pois (id_grupo_pois, id_poi)
        VALUES (%s, %s)
        ON CONFLICT (id_grupo_pois, id_poi) DO NOTHING
    """
    values = [(group_id, id_poi) for group_id in group_ids]
    cursor.executemany(query, values)


def get_clients(id_empresa):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT id_cliente, nombre
            FROM t_clientes
            WHERE id_empresa = %s
            ORDER BY nombre ASC
        """,
            (id_empresa,),
        )
        rows = cursor.fetchall()
        return [{"id_cliente": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
