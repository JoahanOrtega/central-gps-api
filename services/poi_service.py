import logging
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def get_pois(id_empresa, search=None):
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
                    "fecha_registro": row[22].isoformat() if row[22] else None,
                    "id_usuario_registro": row[23],
                    "fecha_cambio": row[24].isoformat() if row[24] else None,
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
            user_id=id_usuario_registro,
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


def get_poi_groups(id_empresa, search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            SELECT
                id_grupo_pois,
                id_empresa,
                id_cliente,
                nombre,
                pois,
                observaciones,
                fecha_registro,
                id_usuario_registro,
                fecha_cambio,
                id_usuario_cambio,
                is_default
            FROM t_grupos_pois
            WHERE id_empresa = %s
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY nombre ASC"
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
                    "fecha_registro": row[6].isoformat() if row[6] else None,
                    "id_usuario_registro": row[7],
                    "fecha_cambio": row[8].isoformat() if row[8] else None,
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
                pois,
                observaciones,
                fecha_registro,
                id_usuario_registro,
                is_default
            )
            VALUES (%s, %s, %s, %s, %s, NOW(), %s, %s)
            RETURNING id_grupo_pois
        """
        values = (
            id_empresa,
            payload.get("id_cliente"),
            payload.get("nombre"),
            0,
            payload.get("observaciones"),
            id_usuario_registro,
            payload.get("is_default", False),
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


def save_poi_groups(cursor, id_poi, group_ids, user_id):
    if not group_ids:
        return
    query = """
        INSERT INTO t_poi_grupos_rel (
            id_poi,
            id_grupo_pois,
            id_usuario_registro
        )
        VALUES (%s, %s, %s)
        ON CONFLICT (id_poi, id_grupo_pois) DO NOTHING
    """
    values = [(id_poi, group_id, user_id) for group_id in group_ids]
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
