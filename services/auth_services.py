from db.connection import get_db_connection


def authenticate_user(username, password):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = 'SELECT id, "user", password FROM t_users WHERE "user" = %s;'
        cursor.execute(query, (username,))
        row = cursor.fetchone()

        if not row:
            return None, "Credenciales incorrectas"

        user_id, db_user, db_password = row

        if password != db_password:
            return None, "Credenciales incorrectas"

        return {
            "id": user_id,
            "username": db_user
        }, None

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()