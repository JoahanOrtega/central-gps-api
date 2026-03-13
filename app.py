from flask import Flask, jsonify, request
from flask_cors import CORS
import psycopg2
from config import Config

app = Flask(__name__)
CORS(app)


def get_db_connection():
    return psycopg2.connect(
        host=Config.DB_HOST,
        dbname=Config.DB_NAME,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        port=Config.DB_PORT
    )


@app.route("/users", methods=["GET"])
def get_users():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = 'SELECT id, "user" FROM t_users;'
        cursor.execute(query)

        rows = cursor.fetchall()

        users = [{"id": row[0], "user": row[1]} for row in rows]

        return jsonify(users), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


@app.route("/login", methods=["POST"])
def login():
    connection = None
    cursor = None

    try:
        data = request.get_json()

        username = data.get("username")
        password = data.get("password")

        if not username or not password:
            return jsonify({"error": "Usuario y contraseña son requeridos"}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        query = 'SELECT id, "user", password FROM t_users WHERE "user" = %s;'
        cursor.execute(query, (username,))
        row = cursor.fetchone()

        if not row:
            return jsonify({"error": "Credenciales incorrectas"}), 401

        user_id, db_user, db_password = row

        if password != db_password:
            return jsonify({"error": "Credenciales incorrectas"}), 401

        return jsonify({
            "message": "Login correcto",
            "user": {
                "id": user_id,
                "username": db_user
            }
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    app.run(debug=True)