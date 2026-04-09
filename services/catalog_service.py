from db.connection import get_db_connection


def get_operators(search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id_operador, nombre
            FROM t_operadores
        """
        params = []
        if search:
            query += "WHERE AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        return [{"id_operador": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_unit_groups(search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id_grupo_unidades, nombre
            FROM t_grupos_unidades
            WHERE 1 = 1
        """
        params = []
        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        return [{"id_grupo_unidades": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_avl_models():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id_modelo_avl, modelo
            FROM t_modelos_avl
            ORDER BY modelo ASC
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        return [{"id_modelo_avl": row[0], "modelo": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_protocols(tipo):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Asumiendo que la tabla t_protocolos tiene columnas: id_protocolo, nombre, tipo
        query = """
            SELECT id_protocolo, nombre
            FROM t_protocolos
            WHERE tipo = %s
            ORDER BY nombre ASC
        """
        cursor.execute(query, (tipo,))
        rows = cursor.fetchall()

        return [{"id_protocolo": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()