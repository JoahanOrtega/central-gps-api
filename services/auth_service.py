import hashlib
from db.connection import get_db_connection
from utils.jwt_handler import generate_jwt


def authenticate_user(username: str, password: str):
    """
    Autentica a un usuario y genera su JWT.

    Retorna una tupla: (user_data, token, error_message)
    Si hay error, user_data y token son None.
    Si hay éxito, error_message es None.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Buscar usuario activo y obtener su rol normalizado
        query = """
            SELECT
                u.id,
                u.usuario,
                u.clave,
                u.nombre,
                u.perfil,
                r.clave     AS rol
            FROM t_usuarios u
            LEFT JOIN t_roles r ON r.id_rol = u.id_rol
            WHERE u.usuario = %s
              AND u.status  = 1
        """
        cursor.execute(query, (username,))
        row = cursor.fetchone()

        if not row:
            return None, None, "Usuario no encontrado"

        user_id, db_username, stored_hash, nombre, perfil, rol = row

        # 2. Validar contraseña con MD5 (legacy del sistema PHP)
        calculated_hash = hashlib.md5(password.encode("utf-8")).hexdigest()
        if calculated_hash != stored_hash:
            return None, None, "Credenciales inválidas"

        # 3. Obtener empresa(s) del usuario
        #    El sudo_erp no tiene empresa asignada en r_empresa_usuarios
        id_empresa = None
        nombre_empresa = None
        es_admin_empresa = False

        if rol != "sudo_erp":
            company_query = """
                SELECT
                    e.id_empresa,
                    e.nombre,
                    reu.es_admin_empresa
                FROM t_empresas e
                INNER JOIN r_empresa_usuarios reu ON reu.id_empresa = e.id_empresa
                WHERE reu.id_usuario = %s
                  AND reu.status     = 1
                  AND e.status       = 1
                ORDER BY reu.es_admin_empresa DESC, e.nombre
                LIMIT 1
            """
            cursor.execute(company_query, (user_id,))
            company_row = cursor.fetchone()

            if company_row:
                id_empresa = company_row[0]
                nombre_empresa = company_row[1]
                es_admin_empresa = bool(company_row[2])

        # 4. Construir payload del usuario para el JWT
        user = {
            "id": user_id,
            "username": db_username,
            "nombre": nombre,
            "perfil": perfil,  # Legacy, se mantiene por compatibilidad
            "rol": rol,  # Nuevo: 'sudo_erp', 'admin_empresa', 'usuario'
            "id_empresa": id_empresa,
            "nombre_empresa": nombre_empresa,
            "es_admin_empresa": es_admin_empresa,
        }

        token = generate_jwt(user)
        return user, token, None

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
