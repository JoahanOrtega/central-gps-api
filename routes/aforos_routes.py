import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from db.connection import get_db_connection
from psycopg2 import errors

aforos_bp = Blueprint('aforos', __name__)
logger = logging.getLogger(__name__)

def clean_val(data, key):
    val = data.get(key)
    if val is None:
        return None
    if isinstance(val, str) and val.strip() == "":
        return None
    return val

def parse_date_safely(date_str):
    if not date_str:
        return None
    
    date_str = str(date_str).strip()
    if 'T' in date_str:
        date_str = date_str.split('T')[0]
    elif ' ' in date_str:
        date_str = date_str.split(' ')[0]
        
    if not date_str:
        return None

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date().isoformat()
        except ValueError:
            continue
            
    return None

@aforos_bp.route('/routes', methods=['GET'])
def list_routes():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id_ruta, nombre
            FROM t_rutas
            WHERE id_empresa = %s AND status = 1
            ORDER BY nombre
        """, (id_empresa,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = [{'id_ruta': r[0], 'nombre': r[1]} for r in rows]
        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_routes")
        return jsonify({"error": "Error al obtener las rutas"}), 500

@aforos_bp.route('/clients', methods=['GET'])
def list_clients():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id_cliente, nombre
            FROM t_clientes
            WHERE id_empresa = %s
            ORDER BY nombre
        """, (id_empresa,))
        rows = cur.fetchall()
        cur.close()
        conn.close()

        result = [{'id_cliente': r[0], 'nombre': r[1]} for r in rows]
        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_clients")
        return jsonify({"error": "Error al obtener los clientes"}), 500

@aforos_bp.route('', methods=['GET'])
def list_aforos():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        search = request.args.get('search', '', type=str).strip()
        is_blacklist_str = request.args.get('is_blacklist', None)

        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        query = """
            SELECT a.*, g.nombre as grupo_nombre, r.nombre as cliente_ruta
            FROM t_aforos a
            LEFT JOIN t_grupos_aforos g ON a.id_grupo_aforos = g.id_grupo_aforos
            LEFT JOIN t_rutas r ON a.id_ruta = r.id_ruta
            WHERE a.id_empresa = %s
        """
        params = [id_empresa]

        if is_blacklist_str is not None:
            is_blacklist = is_blacklist_str.lower() == 'true'
            query += " AND a.is_blacklist = %s"
            params.append(is_blacklist)

        if search:
            query += " AND (a.nombre ILIKE %s OR a.clave ILIKE %s OR a.rfid ILIKE %s)"
            search_param = f"%{search}%"
            params.extend([search_param, search_param, search_param])

        query += " ORDER BY a.id_aforo DESC"

        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()

        result = []
        for row in rows:
            obj = dict(zip(columns, row))
            if obj.get('fecha_asignacion') and hasattr(obj['fecha_asignacion'], 'isoformat'):
                obj['fecha_asignacion'] = obj['fecha_asignacion'].isoformat()
            if obj.get('blacklist_date') and hasattr(obj['blacklist_date'], 'isoformat'):
                obj['blacklist_date'] = obj['blacklist_date'].isoformat()
            if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
                obj['fecha_registro'] = obj['fecha_registro'].isoformat()
            result.append(obj)

        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_aforos")
        return jsonify({"error": "Error al obtener aforos"}), 500

@aforos_bp.route('/<int:id_aforo>/blacklist', methods=['PATCH'])
def toggle_blacklist(id_aforo):
    try:
        data = request.get_json() or {}
        is_blacklist = data.get('is_blacklist', False)
        blacklist_date = clean_val(data, 'blacklist_date')

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE t_aforos
            SET is_blacklist = %s, blacklist_date = %s
            WHERE id_aforo = %s
            RETURNING id_aforo
        """, (is_blacklist, blacklist_date, id_aforo))
        
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Aforo no encontrado"}), 404

        return jsonify(get_aforo(id_aforo)), 200
    except Exception as e:
        logger.exception("Error en toggle_blacklist")
        return jsonify({"error": "Error interno del servidor"}), 500

@aforos_bp.route('', methods=['POST'])
def create_aforo():
    try:
        data = request.get_json()
        if not data or 'nombre' not in data or 'id_empresa' not in data:
            return jsonify({"error": "Nombre e id_empresa son requeridos"}), 400

        rfid = data.get('rfid')
        if not rfid or str(rfid).strip() == "":
            return jsonify({"error": "El código RFID es requerido"}), 400
        
        rfid_str = str(rfid).strip()
        if not rfid_str.isdigit():
            return jsonify({"error": "El RFID debe ser estrictamente numérico"}), 400
        
        if len(rfid_str) > 50:
            return jsonify({"error": "El RFID excede el límite de 50 caracteres"}), 400

        id_empresa = data['id_empresa']
        clave = clean_val(data, 'clave')
        
        fecha_asig = parse_date_safely(clean_val(data, 'fecha_asignacion'))

        conn = get_db_connection()
        cur = conn.cursor()
        
        query = """
            INSERT INTO t_aforos (
                id_empresa, id_grupo_aforos, rfid, clave, nombre, departamento, 
                direccion, id_ruta, referencia, fecha_asignacion, is_blacklist
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (rfid) 
            DO UPDATE SET
                id_empresa = EXCLUDED.id_empresa,
                id_grupo_aforos = EXCLUDED.id_grupo_aforos,
                clave = EXCLUDED.clave,
                nombre = EXCLUDED.nombre,
                departamento = EXCLUDED.departamento,
                direccion = EXCLUDED.direccion,
                id_ruta = EXCLUDED.id_ruta,
                referencia = EXCLUDED.referencia,
                fecha_asignacion = EXCLUDED.fecha_asignacion,
                is_blacklist = EXCLUDED.is_blacklist
            RETURNING id_aforo;
        """
        
        cur.execute(query, (
            id_empresa, 
            clean_val(data, 'id_grupo_aforos'), 
            rfid_str, 
            clave,
            data['nombre'], 
            clean_val(data, 'departamento'), 
            clean_val(data, 'direccion'),
            clean_val(data, 'id_ruta'), 
            clean_val(data, 'referencia'), 
            fecha_asig,
            data.get('is_blacklist', False)
        ))
        
        id_aforo = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify(get_aforo(id_aforo)), 201
    except errors.UniqueViolation as ue:
        logger.warning(f"Conflicto de duplicado capturado en la BD: {ue}")
        return jsonify({"error": "La clave o RFID ya se encuentra registrado"}), 400
    except Exception as e:
        logger.error(f"ERROR CRÍTICO EN CREATE_AFORO: {str(e)}", exc_info=True)
        return jsonify({"error": f"Error al crear/actualizar aforo: {str(e)}"}), 500

@aforos_bp.route('/<int:id_aforo>', methods=['PUT'])
def update_aforo(id_aforo):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No se enviaron datos para actualizar"}), 400

        rfid = data.get('rfid')
        if not rfid or str(rfid).strip() == "":
            return jsonify({"error": "El código RFID es requerido"}), 400
        
        rfid_str = str(rfid).strip()
        if not rfid_str.isdigit():
            return jsonify({"error": "El RFID debe ser estrictamente numérico"}), 400

        fecha_asig = parse_date_safely(clean_val(data, 'fecha_asignacion'))

        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE t_aforos SET
                id_grupo_aforos = %s, rfid = %s, clave = %s, nombre = %s, departamento = %s,
                direccion = %s, id_ruta = %s, referencia = %s,
                fecha_asignacion = %s, is_blacklist = %s
            WHERE id_aforo = %s
            RETURNING id_aforo
        """, (
            clean_val(data, 'id_grupo_aforos'), 
            rfid_str, 
            clean_val(data, 'clave'), 
            data.get('nombre'),
            clean_val(data, 'departamento'), 
            clean_val(data, 'direccion'),
            clean_val(data, 'id_ruta'), 
            clean_val(data, 'referencia'), 
            fecha_asig, 
            data.get('is_blacklist', False), 
            id_aforo
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Aforo no encontrado"}), 404

        return jsonify(get_aforo(id_aforo)), 200
    except errors.UniqueViolation:
        return jsonify({"error": "La clave o RFID ya está en uso"}), 400
    except Exception as e:
        logger.exception("Error en update_aforo")
        return jsonify({"error": "Error al actualizar aforo"}), 500

@aforos_bp.route('/<int:id_aforo>', methods=['DELETE'])
def delete_aforo(id_aforo):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM t_aforos WHERE id_aforo = %s RETURNING id_aforo", (id_aforo,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Aforo no encontrado"}), 404

        return jsonify({"message": "Aforo eliminado correctamente", "id_aforo": id_aforo}), 200
    except Exception as e:
        logger.exception("Error en delete_aforo")
        return jsonify({"error": "Error al eliminar aforo"}), 500

@aforos_bp.route('/groups', methods=['GET'])
def list_groups():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        search = request.args.get('search', '', type=str).strip()

        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400

        conn = get_db_connection()
        cur = conn.cursor()

        query = "SELECT * FROM t_grupos_aforos WHERE id_empresa = %s"
        params = [id_empresa]

        if search:
            query += " AND (nombre ILIKE %s OR clave ILIKE %s)"
            search_param = f"%{search}%"
            params.extend([search_param, search_param])

        query += " ORDER BY id_grupo_aforos DESC"

        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()

        result = []
        for row in rows:
            obj = dict(zip(columns, row))
            if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
                obj['fecha_registro'] = obj['fecha_registro'].isoformat()
            result.append(obj)

        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_groups")
        return jsonify({"error": "Error al obtener los grupos"}), 500

@aforos_bp.route('/groups', methods=['POST'])
def create_group():
    try:
        data = request.get_json()

        nombre = clean_val(data, 'nombre')
        clave = clean_val(data, 'clave')
        id_cliente = data.get('id_cliente')
        id_empresa = data.get('id_empresa')

        if not nombre or not clave or id_cliente is None or id_empresa is None:
            return jsonify({
                "error": "Nombre, clave, id_cliente e id_empresa son obligatorios y no pueden estar vacíos."
            }), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO t_grupos_aforos (id_empresa, id_cliente, clave, nombre, observaciones, id_ruta)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id_grupo_aforos
        """, (
            data['id_empresa'], 
            clean_val(data, 'id_cliente'), 
            clean_val(data, 'clave'),
            data['nombre'], 
            clean_val(data, 'observaciones'), 
            clean_val(data, 'id_ruta')
        ))
        id_grupo = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify(get_group(id_grupo)), 201
    except Exception as e:
        logger.exception("Error en create_group")
        return jsonify({"error": "Error al crear grupo"}), 500

@aforos_bp.route('/groups/<int:id_grupo_aforos>', methods=['PUT'])
def update_group(id_grupo_aforos):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No se enviaron datos"}), 400

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE t_grupos_aforos SET
                id_cliente = %s, clave = %s, nombre = %s, observaciones = %s, id_ruta = %s
            WHERE id_grupo_aforos = %s
            RETURNING id_grupo_aforos
        """, (
            clean_val(data, 'id_cliente'), 
            clean_val(data, 'clave'), 
            data.get('nombre'),
            clean_val(data, 'observaciones'), 
            clean_val(data, 'id_ruta'), 
            id_grupo_aforos
        ))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Grupo no encontrado"}), 404

        return jsonify(get_group(id_grupo_aforos)), 200
    except Exception as e:
        logger.exception("Error en update_group")
        return jsonify({"error": "Error al actualizar grupo"}), 500

@aforos_bp.route('/groups/<int:id_grupo_aforos>', methods=['DELETE'])
def delete_group(id_grupo_aforos):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("UPDATE t_aforos SET id_grupo_aforos = NULL WHERE id_grupo_aforos = %s", (id_grupo_aforos,))
        cur.execute("DELETE FROM t_grupos_aforos WHERE id_grupo_aforos = %s RETURNING id_grupo_aforos", (id_grupo_aforos,))
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Grupo no encontrado"}), 404

        return jsonify({"message": "Grupo eliminado correctamente", "id_grupo_aforos": id_grupo_aforos}), 200
    except Exception as e:
        logger.exception("Error en delete_group")
        return jsonify({"error": "Error al eliminar grupo"}), 500

def get_group(id_grupo):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM t_grupos_aforos WHERE id_grupo_aforos = %s", (id_grupo,))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
        if not row:
            return None
        obj = dict(zip(columns, row))
        if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
            obj['fecha_registro'] = obj['fecha_registro'].isoformat()
        return obj
    except Exception:
        return None

def get_aforo(id_aforo):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT a.*, g.nombre as grupo_nombre, r.nombre as cliente_ruta
            FROM t_aforos a
            LEFT JOIN t_grupos_aforos g ON a.id_grupo_aforos = g.id_grupo_aforos
            LEFT JOIN t_rutas r ON a.id_ruta = r.id_ruta
            WHERE a.id_aforo = %s
        """, (id_aforo,))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        conn.close()
        if not row:
            return None
        obj = dict(zip(columns, row))
        if obj.get('fecha_asignacion') and hasattr(obj['fecha_asignacion'], 'isoformat'):
            obj['fecha_asignacion'] = obj['fecha_asignacion'].isoformat()
        if obj.get('blacklist_date') and hasattr(obj['blacklist_date'], 'isoformat'):
            obj['blacklist_date'] = obj['blacklist_date'].isoformat()
        if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
            obj['fecha_registro'] = obj['fecha_registro'].isoformat()
        return obj
    except Exception:
        return None