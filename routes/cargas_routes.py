import logging
from datetime import datetime
from flask import Blueprint, request, jsonify
from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection
)

fuel_cargas_bp = Blueprint('fuel_cargas', __name__)
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
    datetime_formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
        "%m-%d-%Y"
    ]
    for fmt in datetime_formats:
        try:
            return datetime.strptime(date_str, fmt).isoformat()
        except ValueError:
            continue
    return None

def calculate_kms_gps_telemetry(id_unidad, fecha_carga, ext_conn=None, ext_telem_conn=None):
    kms_gps = 0.0
    conn = ext_conn
    conn_telem = ext_telem_conn
    close_conn = False
    close_telem = False
    
    try:
        if not conn:
            conn = get_db_connection()
            close_conn = True
        cur = conn.cursor()
        cur.execute("SELECT imei FROM t_unidades WHERE id_unidad = %s", (id_unidad,))
        row_u = cur.fetchone()
        imei = row_u[0].strip() if row_u and row_u[0] else None
        
        if not imei:
            cur.close()
            if close_conn:
                release_db_connection(conn)
            return kms_gps

        cur.execute("""
            SELECT TO_CHAR(fecha_carga, 'YYYY-MM-DD HH24:MI:SS')
            FROM t_cargas_combustible
            WHERE id_unidad = %s AND fecha_carga < %s::timestamp
            ORDER BY fecha_carga DESC 
            LIMIT 1
        """, (id_unidad, fecha_carga))
        row = cur.fetchone()
        str_anterior = row[0] if row and row[0] else '1970-01-01 00:00:00'
        cur.close()
        if close_conn:
            release_db_connection(conn)
            conn = None

        str_actual = str(fecha_carga).replace('T', ' ').split('.')[0]

        if not conn_telem:
            conn_telem = get_db_telemetry_connection()
            close_telem = True
        cur_telem = conn_telem.cursor()
        
        cur_telem.execute("""
            SELECT odometro 
            FROM t_data 
            WHERE imei = %s AND fecha_hora_gps <= %s::timestamp
            ORDER BY fecha_hora_gps DESC 
            LIMIT 1
        """, (imei, str_actual))
        row_act = cur_telem.fetchone()
        
        cur_telem.execute("""
            SELECT odometro 
            FROM t_data 
            WHERE imei = %s AND fecha_hora_gps <= %s::timestamp
            ORDER BY fecha_hora_gps DESC 
            LIMIT 1
        """, (imei, str_anterior))
        row_ant = cur_telem.fetchone()
        
        cur_telem.close()
        if close_telem:
            release_db_telemetry_connection(conn_telem)
            conn_telem = None

        if row_act and row_ant:
            odo_act = float(row_act[0] or 0.0)
            odo_ant = float(row_ant[0] or 0.0)
            kms_gps = max(0.0, odo_act - odo_ant)
    except Exception:
        logger.exception("Error al calcular kms gps")
        if close_conn and conn:
            release_db_connection(conn)
        if close_telem and conn_telem:
            release_db_telemetry_connection(conn_telem)
    return kms_gps

def run_business_calculations(id_unidad, liters, cost_per_liter, kms_odo_input, is_update=False, old_kms_odo=0.0, kms_gps=0.0, kms_vacio=None, ext_conn=None):
    litros = float(liters or 0.0)
    costo_litro = float(cost_per_liter or 0.0)
    importe = round(litros * costo_litro, 2)
    k_gps = float(kms_gps) if kms_gps is not None else 0.0
    k_vacio = float(kms_vacio) if kms_vacio is not None else 0.0
    k_odo_input = float(kms_odo_input) if kms_odo_input is not None else 0.0
    rend_establecido = 0.0
    ultimo_odo = 0.0
    conn = ext_conn
    close_conn = False
    try:
        if not conn:
            conn = get_db_connection()
            close_conn = True
        cur = conn.cursor()
        cur.execute("""
            SELECT rendimiento_establecido, COALESCE(NULLIF(odometro_fisico, 0.0), odometro_inicial, 0.0) 
            FROM t_unidades 
            WHERE id_unidad = %s
        """, (id_unidad,))
        row = cur.fetchone()
        if row:
            rend_establecido = float(row[0]) if row[0] is not None else 0.0
            ultimo_odo = float(row[1]) if row[1] is not None else 0.0
        cur.close()
        if close_conn:
            release_db_connection(conn)
    except Exception:
        logger.exception("Error al recuperar datos del catalogo de unidades")
        if close_conn and conn:
            release_db_connection(conn)
    if is_update:
        limite_inferior = ultimo_odo - old_kms_odo
        if k_odo_input < limite_inferior:
            raise ValueError(f"El odometro fisico ({k_odo_input}) no puede ser menor al limite inferior ({limite_inferior})")
        kms_recorridos = max(0.0, k_odo_input - limite_inferior)
    else:
        if k_odo_input < ultimo_odo:
            raise ValueError(f"El odometro fisico ({k_odo_input}) no puede ser menor al actual registrado ({ultimo_odo})")
        kms_recorridos = max(0.0, k_odo_input - ultimo_odo)
    if k_vacio > kms_recorridos:
        raise ValueError(f"Los kilometros vacios ({k_vacio}) no pueden ser mayores a los kilometros recorridos reales ({kms_recorridos})")
    porc_vacio = 0.0
    if kms_recorridos > 0:
        porc_vacio = round((k_vacio / kms_recorridos) * 100, 2)
    rend_gps = 0.0
    if litros > 0 and k_gps > 0:
        rend_gps = round(k_gps / litros, 2)
    rend_odo = 0.0
    if litros > 0 and kms_recorridos > 0:
        rend_odo = round(kms_recorridos / litros, 2)
    return {
        "importe": importe,
        "kms_recorridos": kms_recorridos,
        "porc_vacio": porc_vacio,
        "rend_gps": rend_gps,
        "rend_odo": rend_odo,
        "rend_establecido": rend_establecido,
    }

def get_fuel_carga(id_combustible):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            WITH list_cte AS (
                SELECT fc.id_combustible, fc.id_empresa, fc.id_unidad, fc.fecha_carga, 
                       fc.gasolinera, fc.grupo_unidades, fc.folio, fc.litros, fc.costo_litro, 
                       fc.importe, fc.referencia, fc.kms_gps, fc.kms_vacio, fc.porc_vacio, 
                       fc.rend_gps, fc.rend_odo, fc.rendimiento_establecido, fc.fecha_registro,
                       CONCAT_WS(' ', u.numero, u.marca, u.modelo) as unidad,
                       fc.kms_odo as kms_recorridos,
                       (COALESCE(u.odometro_inicial, 0.0) + SUM(fc.kms_odo) OVER (
                           PARTITION BY fc.id_unidad 
                           ORDER BY fc.fecha_carga ASC, fc.id_combustible ASC
                       )) as kms_odo
                FROM t_cargas_combustible fc
                INNER JOIN t_unidades u ON fc.id_unidad = u.id_unidad
                WHERE fc.id_unidad = (SELECT id_unidad FROM t_cargas_combustible WHERE id_combustible = %s)
            )
            SELECT * FROM list_cte WHERE id_combustible = %s
        """, (id_combustible, id_combustible))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        release_db_connection(conn)
        if not row:
            return None
        obj = dict(zip(columns, row))
        if obj.get('fecha_carga') and hasattr(obj['fecha_carga'], 'isoformat'):
            obj['fecha_carga'] = obj['fecha_carga'].isoformat()
        if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
            obj['fecha_registro'] = obj['fecha_registro'].isoformat()
        numeric_cols = [
            'litros', 'costo_litro', 'importe', 'kms_gps', 'kms_vacio', 
            'porc_vacio', 'rend_gps', 'kms_odo', 'kms_recorridos', 'rend_odo', 'rend_establecido'
        ]
        for col in numeric_cols:
            if obj.get(col) is not None:
                obj[col] = float(obj[col])
        return obj
    except Exception:
        logger.exception("Error en consulta interna get_fuel_carga")
        return None

@fuel_cargas_bp.route('', methods=['GET'])
def list_fuel_cargas():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        search = request.args.get('search', '', type=str).strip()
        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            WITH list_cte AS (
                SELECT fc.id_combustible, fc.id_empresa, fc.id_unidad, fc.fecha_carga, 
                       fc.gasolinera, fc.grupo_unidades, fc.folio, fc.litros, fc.costo_litro, 
                       fc.importe, fc.referencia, fc.kms_gps, fc.kms_vacio, fc.porc_vacio, 
                       fc.rend_gps, fc.rend_odo, fc.rendimiento_establecido, fc.fecha_registro,
                       CONCAT_WS(' ', u.numero, u.marca, u.modelo) as unidad,
                       fc.kms_odo as kms_recorridos,
                       (COALESCE(u.odometro_inicial, 0.0) + SUM(fc.kms_odo) OVER (
                           PARTITION BY fc.id_unidad 
                           ORDER BY fc.fecha_carga ASC, fc.id_combustible ASC
                       )) as kms_odo
                FROM t_cargas_combustible fc
                INNER JOIN t_unidades u ON fc.id_unidad = u.id_unidad
                WHERE fc.id_empresa = %s
            )
            SELECT * FROM list_cte WHERE 1=1
        """
        params = [id_empresa]
        if search:
            query += " AND (folio ILIKE %s OR gasolinera ILIKE %s OR referencia ILIKE %s OR unidad ILIKE %s)"
            search_param = f"%{search}%"
            params.extend([search_param, search_param, search_param, search_param])
        query += " ORDER BY id_combustible DESC"
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        cur.close()
        release_db_connection(conn)
        result = []
        for row in rows:
            obj = dict(zip(columns, row))
            if obj.get('fecha_carga') and hasattr(obj['fecha_carga'], 'isoformat'):
                obj['fecha_carga'] = obj['fecha_carga'].isoformat()
            if obj.get('fecha_registro') and hasattr(obj['fecha_registro'], 'isoformat'):
                obj['fecha_registro'] = obj['fecha_registro'].isoformat()
            numeric_cols = [
                'litros', 'costo_litro', 'importe', 'kms_gps', 'kms_vacio', 
                'porc_vacio', 'rend_gps', 'kms_odo', 'kms_recorridos', 'rend_odo', 'rendimiento_establecido'
            ]
            for col in numeric_cols:
                if obj.get(col) is not None:
                    obj[col] = float(obj[col])
            obj['rend_establecido'] = obj.get('rendimiento_establecido')
            obj['rend_optimo'] = obj.get('rendimiento_establecido')
            result.append(obj)
        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_fuel_cargas")
        return jsonify({"error": "Error al obtener listado de combustibles"}), 500

@fuel_cargas_bp.route('/unidades', methods=['GET'])
def list_unidades():
    try:
        id_empresa = request.args.get('id_empresa', type=int)
        if not id_empresa:
            return jsonify({"error": "id_empresa es requerido"}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id_unidad, 
                   CONCAT_WS(' ', numero, marca, modelo) as nombre, 
                   rendimiento_establecido, 
                   COALESCE(NULLIF(odometro_fisico, 0.0), odometro_inicial, 0.0) as odometro_fisico
            FROM t_unidades
            WHERE id_empresa = %s
            ORDER BY numero
        """, (id_empresa,))
        rows = cur.fetchall()
        cur.close()
        release_db_connection(conn)
        result = []
        for r in rows:
            result.append({
                'id_unidad': r[0],
                'nombre': r[1],
                'rendimiento_establecido': float(r[2]) if r[2] is not None else 0.0,
                'odometro_fisico': float(r[3]) if r[3] is not None else 0.0
            })
        return jsonify(result), 200
    except Exception as e:
        logger.exception("Error en list_unidades")
        return jsonify({"error": "Error al obtener las unidades"}), 500

@fuel_cargas_bp.route('', methods=['POST'])
def create_fuel_carga():
    try:
        data = request.get_json()
        if not data or 'folio' not in data or 'id_empresa' not in data or 'id_unidad' not in data or 'kms_odo' not in data or data.get('kms_odo') is None:
            return jsonify({"error": "Los campos folio, id_empresa, id_unidad y kms_odo son requeridos"}), 400
        id_empresa = data['id_empresa']
        id_unidad = data['id_unidad']
        folio = str(data['folio']).strip()
        fecha_carga = parse_date_safely(clean_val(data, 'fecha_carga'))
        kms_odo = float(data.get('kms_odo'))
        if not folio:
            return jsonify({"error": "El folio no puede ser un campo en blanco"}), 400
        if not fecha_carga:
            return jsonify({"error": "La fecha de carga es obligatoria"}), 400
            
        kms_gps_calc = calculate_kms_gps_telemetry(id_unidad, fecha_carga)
        
        try:
            calcs = run_business_calculations(
                id_unidad=id_unidad,
                liters=data.get('litros', 0.0),
                cost_per_liter=data.get('costo_litro', 0.0),
                kms_odo_input=kms_odo,
                is_update=False,
                kms_gps=kms_gps_calc,
                kms_vacio=data.get('kms_vacio')
            )
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
            
        conn = get_db_connection()
        cur = conn.cursor()
        query = """
            INSERT INTO t_cargas_combustible (
                id_empresa, id_unidad, fecha_carga, gasolinera, grupo_unidades, folio,
                litros, costo_litro, importe, referencia, kms_gps, kms_vacio,
                porc_vacio, rend_gps, kms_odo, rend_odo, rendimiento_establecido
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id_combustible;
        """
        cur.execute(query, (
            id_empresa,
            id_unidad,
            fecha_carga,
            clean_val(data, 'gasolinera'),
            clean_val(data, 'grupo_unidades'),
            folio,
            data.get('litros', 0.0),
            data.get('costo_litro', 0.0),
            calcs['importe'],
            clean_val(data, 'referencia'),
            kms_gps_calc,
            data.get('kms_vacio'),
            calcs['porc_vacio'],
            calcs['rend_gps'],
            kms_odo,
            calcs['rend_odo'],
            calcs['rend_establecido']
        ))
        id_combustible = cur.fetchone()[0]
        cur.execute("""
            UPDATE t_unidades 
            SET odometro_fisico = COALESCE(
                (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = %s),
                0.0
            ) + COALESCE(NULLIF(odometro_inicial, 0.0), 0.0)
            WHERE id_unidad = %s
        """, (id_unidad, id_unidad))
        conn.commit()
        cur.close()
        release_db_connection(conn)
        return jsonify(get_fuel_carga(id_combustible)), 201
    except Exception as e:
        logger.exception("Error en create_fuel_carga")
        return jsonify({"error": "Error al registrar la carga de combustible"}), 500

@fuel_cargas_bp.route('/<int:id_combustible>', methods=['PUT'])
def update_fuel_carga(id_combustible):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No se recibieron datos para actualizar"}), 400
        if 'kms_odo' not in data or data.get('kms_odo') is None:
            return jsonify({"error": "El campo odometro fisico es obligatorio"}), 400
        folio = str(data.get('folio', '')).strip()
        fecha_carga = parse_date_safely(clean_val(data, 'fecha_carga'))
        id_unidad = data.get('id_unidad')
        kms_odo = float(data.get('kms_odo'))
        if not folio:
            return jsonify({"error": "El campo folio es obligatorio"}), 400
        if not fecha_carga:
            return jsonify({"error": "La fecha de carga es obligatoria"}), 400
            
        kms_gps_calc = calculate_kms_gps_telemetry(id_unidad, fecha_carga)
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id_unidad, kms_odo FROM t_cargas_combustible WHERE id_combustible = %s", (id_combustible,))
        row_old = cur.fetchone()
        cur.close()
        release_db_connection(conn)
        if not row_old:
            return jsonify({"error": "Registro de combustible no encontrado"}), 404
        old_id_unidad = row_old[0]
        old_kms_odo = float(row_old[1]) if row_old[1] is not None else 0.0
        try:
            calcs = run_business_calculations(
                id_unidad=id_unidad,
                liters=data.get('litros', 0.0),
                cost_per_liter=data.get('costo_litro', 0.0),
                kms_odo_input=kms_odo,
                is_update=True,
                old_kms_odo=old_kms_odo,
                kms_gps=kms_gps_calc,  
                kms_vacio=data.get('kms_vacio')
            )
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE t_cargas_combustible SET
                id_unidad = %s, fecha_carga = %s, gasolinera = %s, grupo_unidades = %s, folio = %s,
                litros = %s, costo_litro = %s, importe = %s, referencia = %s, kms_gps = %s, 
                kms_vacio = %s, porc_vacio = %s, rend_gps = %s, kms_odo = %s, rend_odo = %s, 
                rendimiento_establecido = %s
            WHERE id_combustible = %s
            RETURNING id_combustible
        """, (
            id_unidad,
            fecha_carga,
            clean_val(data, 'gasolinera'),
            clean_val(data, 'grupo_unidades'),
            folio,
            data.get('litros', 0.0),
            data.get('costo_litro', 0.0),
            calcs['importe'],
            clean_val(data, 'referencia'),
            kms_gps_calc,
            data.get('kms_vacio'),
            calcs['porc_vacio'],
            calcs['rend_gps'],
            calcs['kms_recorridos'],
            calcs['rend_odo'],
            calcs['rend_establecido'],
            id_combustible
        ))
        row = cur.fetchone()
        if not row:
            cur.close()
            release_db_connection(conn)
            return jsonify({"error": "Registro de combustible no encontrado"}), 404
        cur.execute("""
            UPDATE t_unidades 
            SET odometro_fisico = COALESCE(
                (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = %s),
                0.0
            ) + COALESCE(NULLIF(odometro_inicial, 0.0), 0.0)
            WHERE id_unidad = %s
        """, (id_unidad, id_unidad))
        if old_id_unidad != id_unidad:
            cur.execute("""
                UPDATE t_unidades 
                SET odometro_fisico = COALESCE(
                    (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = %s),
                    0.0
                ) + COALESCE(NULLIF(odometro_inicial, 0.0), 0.0)
                WHERE id_unidad = %s
            """, (old_id_unidad, old_id_unidad))
        conn.commit()
        cur.close()
        release_db_connection(conn)
        return jsonify(get_fuel_carga(id_combustible)), 200
    except Exception as e:
        logger.exception("Error en update_fuel_carga")
        return jsonify({"error": "Error al actualizar la carga de combustible"}), 500

@fuel_cargas_bp.route('/<int:id_combustible>', methods=['DELETE'])
def delete_fuel_carga(id_combustible):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id_unidad FROM t_cargas_combustible WHERE id_combustible = %s", (id_combustible,))
        row_unit = cur.fetchone()
        cur.execute("DELETE FROM t_cargas_combustible WHERE id_combustible = %s RETURNING id_combustible", (id_combustible,))
        row = cur.fetchone()
        if not row:
            cur.close()
            release_db_connection(conn)
            return jsonify({"error": "Registro no encontrado"}), 404
        if row_unit:
            id_unidad = row_unit[0]
            cur.execute("""
                UPDATE t_unidades 
                SET odometro_fisico = COALESCE(
                    (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = %s),
                    0.0
                ) + COALESCE(NULLIF(odometro_inicial, 0.0), 0.0)
                WHERE id_unidad = %s
            """, (id_unidad, id_unidad))
        conn.commit()
        cur.close()
        release_db_connection(conn)
        return jsonify({"message": "Registro eliminado con exito", "id_combustible": id_combustible}), 200
    except Exception as e:
        logger.exception("Error en delete_fuel_carga")
        return jsonify({"error": "No se pudo eliminar el registro"}), 500

@fuel_cargas_bp.route('/bulk', methods=['POST'])
def bulk_import_fuel_cargas():
    try:
        payload = request.get_json()
        if not payload or 'id_empresa' not in payload or 'items' not in payload:
            return jsonify({"error": "El id_empresa y los elementos a cargar son mandatorios"}), 400
        id_empresa = payload['id_empresa']
        items = payload['items']
        if not items:
            return jsonify({"message": "El lote de importacion se encuentra vacio"}), 400
            
        def get_iso_sort_key(x):
            parsed = parse_date_safely(clean_val(x, 'fecha_carga'))
            return parsed if parsed else "1970-01-01T00:00:00"
            
        items.sort(key=get_iso_sort_key)
            
        conn = get_db_connection()
        conn_telem = get_db_telemetry_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT id_unidad, numero FROM t_unidades WHERE id_empresa = %s", (id_empresa,))
        units_cache = {str(row[1]).strip().upper(): row[0] for row in cur.fetchall()}
        success_count = 0
        skipped_items = []
        affected_units = set()
        
        for idx, it in enumerate(items):
            unidad_str = str(it.get('unidad', '')).strip().upper()
            id_unidad = units_cache.get(unidad_str)
            if not id_unidad:
                skipped_items.append({
                    "fila": idx + 2,
                    "unidad": unidad_str,
                    "razon": f"La unidad '{unidad_str}' no esta registrada en el catalogo de esta empresa."
                })
                continue
            folio = str(it.get('folio', '')).strip()
            fecha_carga = parse_date_safely(clean_val(it, 'fecha_carga'))
            kms_odo = it.get('kms_odo')
            if not folio or not fecha_carga:
                skipped_items.append({
                    "fila": idx + 2,
                    "unidad": unidad_str,
                    "razon": "Falta folio o la fecha de carga es invalida."
                })
                continue
            if kms_odo is None:
                skipped_items.append({
                    "fila": idx + 2,
                    "unidad": unidad_str,
                    "razon": "El odometro fisico es obligatorio."
                })
                continue
                
            kms_gps_calc = calculate_kms_gps_telemetry(id_unidad, fecha_carga, ext_conn=conn, ext_telem_conn=conn_telem)
            
            try:
                calcs = run_business_calculations(
                    id_unidad=id_unidad,
                    liters=it.get('litros', 0.0),
                    cost_per_liter=it.get('costo_litro', 0.0),
                    kms_odo_input=kms_odo,
                    is_update=False,
                    kms_gps=kms_gps_calc,  
                    kms_vacio=it.get('kms_vacio'),
                    ext_conn=conn
                )
            except ValueError as ve:
                skipped_items.append({
                    "fila": idx + 2,
                    "unidad": unidad_str,
                    "razon": str(ve)
                })
                continue
            cur.execute("""
                INSERT INTO t_cargas_combustible (
                    id_empresa, id_unidad, fecha_carga, gasolinera, folio,
                    litros, costo_litro, importe, referencia, kms_gps, kms_vacio, 
                    porc_vacio, rend_gps, kms_odo, rend_odo, rendimiento_establecido
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                id_empresa,
                id_unidad,
                fecha_carga,
                clean_val(it, 'gasolinera'),
                folio,
                it.get('litros', 0.0),
                it.get('costo_litro', 0.0),
                calcs['importe'],
                clean_val(it, 'referencia'),
                kms_gps_calc,
                it.get('kms_vacio'),     
                calcs['porc_vacio'],
                calcs['rend_gps'],
                kms_odo,
                calcs['rend_odo'],
                calcs['rend_establecido']
            ))
            success_count += 1
            affected_units.add(id_unidad)
            
        for unit_id in affected_units:
            cur.execute("""
                UPDATE t_unidades 
                SET odometro_fisico = COALESCE(
                    (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = %s),
                    0.0
                ) + COALESCE(NULLIF(odometro_inicial, 0.0), 0.0)
                WHERE id_unidad = %s
            """, (unit_id, unit_id))
            
        conn.commit()
        cur.close()
        release_db_connection(conn)
        release_db_telemetry_connection(conn_telem)
        return jsonify({
            "message": f"Se procesaron {success_count} cargas correctamente.",
            "exitosos": success_count,
            "omitidos": skipped_items
        }), 201
    except Exception as e:
        logger.exception("Error general en bulk_import_fuel_cargas")
        return jsonify({"error": "Ocurrio un error inesperado al procesar el archivo en el servidor."}), 500