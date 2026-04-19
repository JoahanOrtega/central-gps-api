import logging
from flask import Blueprint, jsonify, request
from db.connection import get_db_connection, release_db_connection
from utils.auth_guard import jwt_required, sudo_erp_required

users_bp = Blueprint("users", __name__)

logger = logging.getLogger(__name__)


@users_bp.route("/", methods=["GET"])
@jwt_required
@sudo_erp_required
def get_users():
    """
    Lista todos los usuarios del sistema.

    Acceso restringido a sudo_erp únicamente — exponer IDs y usernames
    a cualquier rol inferior facilita ataques de enumeración y
    fuerza bruta dirigida contra cuentas específicas.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT id, usuario FROM t_usuarios WHERE status = 1 ORDER BY usuario;"
        )
        rows = cursor.fetchall()
        users = [{"id": row[0], "username": row[1]} for row in rows]

        return jsonify(users), 200

    except Exception as error:
        logger.error("Error en GET /users: %s", repr(error), exc_info=True)
        return jsonify({"error": "Error interno del servidor"}), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
