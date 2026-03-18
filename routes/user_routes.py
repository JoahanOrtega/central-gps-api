from flask import Blueprint, jsonify
from db.connection import get_db_connection

users_bp = Blueprint("users", __name__)


@users_bp.route("/users", methods=["GET"])
def get_users():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = "SELECT id, usuario FROM t_usuarios;"
        cursor.execute(query)

        rows = cursor.fetchall()

        users = [{"id": row[0], "username": row[1]} for row in rows]

        return jsonify(users), 200

    except Exception as error:
        return jsonify({"error": str(error)}), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()