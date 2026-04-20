from db.connection import get_db_connection, release_db_connection


def get_user_companies(user_id: int) -> list[dict]:
    """
    Retorna las empresas a las que tiene acceso un usuario.

    Lógica (modelo 1:N):
      - sudo_erp → todas las empresas activas del sistema
      - cualquier otro rol → SU empresa (una sola), leída desde
        t_usuarios.id_empresa. No se consulta r_empresa_usuarios:
        la fuente de verdad es t_usuarios.

    Esta función la usa:
      1. /auth/switch-company para validar que el sudo_erp accede a la empresa
      2. El companyStore del frontend para poblar el selector (solo sudo)

    Para clientes, siempre retorna una lista de 0 o 1 elementos.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Obtener el rol y la empresa del usuario en una sola query.
        cursor.execute(
            """
            SELECT r.clave, u.id_empresa
            FROM t_usuarios u
            LEFT JOIN t_roles r ON r.id_rol = u.id_rol
            WHERE u.id     = %s
              AND u.status = 1
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            # Usuario no encontrado o inactivo: sin empresas
            return []

        rol, user_id_empresa = row

        # 2. sudo_erp ve todas las empresas activas
        if rol == "sudo_erp":
            cursor.execute("""
                SELECT id_empresa, nombre
                FROM t_empresas
                WHERE status = 1
                ORDER BY nombre
                """)
            rows = cursor.fetchall()
            return [{"id_empresa": row[0], "nombre": row[1]} for row in rows]

        # 3. Cualquier otro rol: solo su empresa, si está activa.
        if user_id_empresa is None:
            # Dato inconsistente (validado también por trigger 002),
            # pero defensa en profundidad: retornar lista vacía en vez
            # de fallar.
            return []

        cursor.execute(
            """
            SELECT id_empresa, nombre
            FROM t_empresas
            WHERE id_empresa = %s
              AND status     = 1
            """,
            (user_id_empresa,),
        )
        row = cursor.fetchone()
        if not row:
            # Empresa del usuario fue suspendida: lista vacía.
            return []

        return [{"id_empresa": row[0], "nombre": row[1]}]

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


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
            release_db_connection(connection)
