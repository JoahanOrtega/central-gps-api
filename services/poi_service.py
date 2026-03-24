from db.connection import get_db_connection


def get_pois(search=None):
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
            WHERE 1 = 1
        """

        params = []

        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")

        query += " ORDER BY id_poi DESC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        result = []
        for row in rows:
            result.append({
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
            })

        return result
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def create_poi(payload):
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
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s
            )
            RETURNING id_poi
        """

        values = (
            payload["id_empresa"],
            payload["tipo_elemento"],
            payload["id_elemento"],
            payload["nombre"],
            payload["direccion"],
            payload["tipo_poi"],
            payload["tipo_marker"],
            payload["url_marker"],
            payload["marker_path"],
            payload["marker_color"],
            payload["icon"],
            payload["icon_color"],
            payload["lat"],
            payload["lng"],
            payload["radio"],
            payload["bounds"],
            payload["area"],
            payload["radio_color"],
            payload["polygon_path"],
            payload["polygon_color"],
            payload["observaciones"],
            payload["id_usuario_registro"],
        )

        cursor.execute(query, values)
        poi_id = cursor.fetchone()[0]

        save_poi_groups(
            cursor=cursor,
            id_poi=poi_id,
            group_ids=payload.get("id_grupo_pois", []),
            user_id=payload["id_usuario_registro"],
        )

        connection.commit()

        return {"id_poi": poi_id}

    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_poi_groups(search=None):
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
            WHERE 1 = 1
        """

        params = []

        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")

        query += " ORDER BY nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        result = []
        for row in rows:
            result.append({
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
            })

        return result
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def create_poi_group(payload):
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
            payload["id_empresa"],
            payload["id_cliente"],
            payload["nombre"],
            0,
            payload["observaciones"],
            payload["id_usuario_registro"],
            payload["is_default"],
        )

        cursor.execute(query, values)
        group_id = cursor.fetchone()[0]
        connection.commit()

        return {"id_grupo_pois": group_id}
    except Exception:
        if connection:
            connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

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



def get_clients():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            SELECT id_cliente, nombre
            FROM t_clientes
            ORDER BY nombre ASC
        """)

        rows = cursor.fetchall()

        return [{"id_cliente": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()