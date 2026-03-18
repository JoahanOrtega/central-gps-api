import hashlib
from db.connection import get_db_connection


def authenticate_user(username, password):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id, usuario, clave
            FROM t_usuarios
            WHERE usuario = %s;
        """
        cursor.execute(query, (username,))
        row = cursor.fetchone()

        if not row:
            return None, "Usuario no encontrado"

        user_id, db_username, stored_password_hash = row
        calculated_password_hash = hashlib.md5(
            password.encode("utf-8")
        ).hexdigest()

        if calculated_password_hash != stored_password_hash:
            return None, "Credenciales inválidas"

        return {
            "id": user_id,
            "username": db_username
        }, None

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()