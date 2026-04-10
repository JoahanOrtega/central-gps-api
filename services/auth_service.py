import hashlib
from db.connection import get_db_connection
from utils.jwt_handler import generate_jwt


def authenticate_user(username, password):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # 1. Obtener usuario y validar contraseña
        query = """
            SELECT id, usuario, clave, perfil
            FROM t_usuarios
            WHERE usuario = %s AND status = 1
        """
        cursor.execute(query, (username,))
        row = cursor.fetchone()

        if not row:
            return None, None, "Usuario no encontrado"

        user_id, db_username, stored_password_hash, perfil = row
        calculated_password_hash = hashlib.md5(password.encode("utf-8")).hexdigest()

        if calculated_password_hash != stored_password_hash:
            return None, None, "Credenciales inválidas"

        # 2. Obtener la primera empresa del usuario (si tiene)
        company_query = """
            SELECT e.id_empresa, e.nombre
            FROM t_empresas e
            INNER JOIN r_empresa_usuarios ru ON e.id_empresa = ru.id_empresa
            WHERE ru.id_usuario = %s AND e.status = 1
            ORDER BY e.nombre
            LIMIT 1
        """
        cursor.execute(company_query, (user_id,))
        company_row = cursor.fetchone()

        user = {
            "id": user_id,
            "username": db_username,
            "perfil": perfil,
            "id_empresa": company_row[0] if company_row else None,
            "nombre_empresa": company_row[1] if company_row else None,
        }

        token = generate_jwt(user)
        return user, token, None

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
