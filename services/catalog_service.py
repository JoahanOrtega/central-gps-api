import logging
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def get_operators(id_empresa, search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id_operador, nombre
            FROM t_operadores
            WHERE id_empresa = %s
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return [{"id_operador": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_unit_groups(id_empresa, search=None):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT id_grupo_unidades, nombre
            FROM t_grupos_unidades
            WHERE id_empresa = %s
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(nombre) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return [{"id_grupo_unidades": row[0], "nombre": row[1]} for row in rows]
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_avl_models():
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            SELECT id_modelo_avl, modelo
            FROM t_modelos_avl
            ORDER BY modelo ASC
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        return [{"id_modelo_avl": row[0], "modelo": row[1]} for row in rows]
    except Exception as e:
        # Loguear el error real para diagnosticar — tabla inexistente, etc.
        logger.error("Error en get_avl_models: %s", repr(e))
        # Retornar lista vacía para no bloquear el formulario de nueva unidad
        return []
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
