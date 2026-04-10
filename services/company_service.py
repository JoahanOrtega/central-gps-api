from db.connection import get_db_connection


def get_user_companies(user_id):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Verificar si el usuario es administrador
        cursor.execute("SELECT permisos FROM t_usuarios WHERE id = %s", (user_id,))
        row = cursor.fetchone()
        is_admin = False
        if row:
            permisos = row[0] or ""
            perm_list = [p.strip() for p in permisos.split(",")]
            if "777" in perm_list or "*" in perm_list:
                is_admin = True

        # 2. Si es admin, devolver todas las empresas activas
        if is_admin:
            query = """
                SELECT id_empresa, nombre
                FROM t_empresas
                ORDER BY nombre
            """
            cursor.execute(query)
        else:
            # Usuario normal: solo empresas asignadas en r_empresa_usuarios
            query = """
                SELECT e.id_empresa, e.nombre
                FROM t_empresas e
                INNER JOIN r_empresa_usuarios ru ON e.id_empresa = ru.id_empresa
                WHERE ru.id_usuario = %s AND e.status = 1
                ORDER BY e.nombre
            """
            cursor.execute(query, (user_id,))

        rows = cursor.fetchall()
        return [{"id_empresa": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
