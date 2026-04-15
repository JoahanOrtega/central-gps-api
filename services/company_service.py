from db.connection import get_db_connection


def get_user_companies(user_id: int) -> list[dict]:
    """
    Retorna las empresas a las que tiene acceso un usuario.

    Lógica:
    - sudo_erp  → todas las empresas activas del sistema
    - cualquier otro rol → solo las empresas asignadas en r_empresa_usuarios
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Obtener el rol del usuario desde t_roles (ya normalizado)
        cursor.execute(
            """
            SELECT r.clave
            FROM t_usuarios u
            LEFT JOIN t_roles r ON r.id_rol = u.id_rol
            WHERE u.id = %s
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        rol = row[0] if row else None

        # 2. sudo_erp ve todas las empresas activas
        if rol == "sudo_erp":
            cursor.execute("""
                SELECT id_empresa, nombre
                FROM t_empresas
                WHERE status = 1
                ORDER BY nombre
                """)
        else:
            # Cualquier otro rol: solo empresas asignadas y activas
            cursor.execute(
                """
                SELECT e.id_empresa, e.nombre
                FROM t_empresas e
                INNER JOIN r_empresa_usuarios reu ON reu.id_empresa = e.id_empresa
                WHERE reu.id_usuario = %s
                  AND reu.status     = 1
                  AND e.status       = 1
                ORDER BY e.nombre
                """,
                (user_id,),
            )

        rows = cursor.fetchall()
        return [{"id_empresa": row[0], "nombre": row[1]} for row in rows]

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def get_company_details(id_empresa: int) -> dict | None:
    """
    Retorna los datos completos de una empresa por su ID.
    Usado por switch-company para incluir el nombre en la respuesta.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT id_empresa, nombre, status
            FROM t_empresas
            WHERE id_empresa = %s
            """,
            (id_empresa,),
        )
        row = cursor.fetchone()
        if not row:
            return None

        return {"id_empresa": row[0], "nombre": row[1], "status": row[2]}

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
