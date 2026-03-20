from db.connection import get_db_connection


def get_units(search=None):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            SELECT
                id_unidad,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                imei,
                chip,
                id_operador,
                status
            FROM t_unidades
            WHERE status = 1
        """

        params = []

        if search:
            query += " AND LOWER(numero) LIKE LOWER(%s)"
            params.append(f"%{search}%")

        query += " ORDER BY id_unidad ASC;"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        units = []
        for row in rows:
            units.append({
                "id": row[0],
                "numero": row[1],
                "marca": row[2],
                "modelo": row[3],
                "anio": row[4],
                "matricula": row[5],
                "tipo": row[6],
                "imagen": row[7],
                "imei": row[8],
                "chip": row[9],
                "id_operador": row[10],
                "status": row[11],
            })

        return units

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
            
def create_unit(payload):
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            INSERT INTO t_unidades (
                id_empresa,
                id_operador,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                id_modelo_avl,
                imei,
                chip,
                odometro_inicial,
                tipo_combustible,
                capacidad_tanque,
                rendimiento_establecido,
                no_serie,
                nombre_aseguradora,
                telefono_aseguradora,
                no_poliza_seguro,
                vigencia_poliza_seguro,
                vigencia_verificacion_vehicular,
                input1,
                input2,
                input3,
                input4,
                output1,
                output2,
                output3,
                output4,
                rs232,
                fecha_instalacion,
                id_usuario_registro,
                fecha_registro,
                status
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, NOW(), %s
            )
            RETURNING id_unidad;
        """

        values = (
            payload["id_empresa"],
            payload.get("id_operador"),
            payload["numero"],
            payload["marca"],
            payload["modelo"],
            payload["anio"],
            payload["matricula"],
            payload["tipo"],
            payload.get("imagen", ""),
            payload.get("id_modelo_avl"),
            payload["imei"],
            payload["chip"],
            payload["odometro_inicial"],
            payload.get("tipo_combustible"),
            payload.get("capacidad_tanque"),
            payload.get("rendimiento_establecido"),
            payload.get("no_serie"),
            payload.get("nombre_aseguradora"),
            payload.get("telefono_aseguradora"),
            payload.get("no_poliza_seguro"),
            payload.get("vigencia_poliza_seguro"),
            payload.get("vigencia_verificacion_vehicular"),
            payload.get("input1", "sin uso"),
            payload.get("input2", "sin uso"),
            payload.get("input3", "sin uso"),
            payload.get("input4", "sin uso"),
            payload.get("output1", "sin uso"),
            payload.get("output2", "sin uso"),
            payload.get("output3", "sin uso"),
            payload.get("output4", "sin uso"),
            payload.get("rs232", "sin uso"),
            payload["fecha_instalacion"],
            payload["id_usuario_registro"],
            payload["status"],
        )

        cursor.execute(query, values)
        new_unit_id = cursor.fetchone()[0]
        connection.commit()

        return {"id": new_unit_id}

    except Exception:
        if connection:
            connection.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()