from db.connection import get_db_connection


def get_units(id_empresa, search=None):
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
            WHERE id_empresa = %s AND status = 1
        """
        params = [id_empresa]
        if search:
            query += " AND LOWER(numero) LIKE LOWER(%s)"
            params.append(f"%{search}%")
        query += " ORDER BY id_unidad ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        units = []
        for row in rows:
            units.append(
                {
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
                }
            )
        return units
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


def create_unit(payload, id_usuario_registro, id_empresa):
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        query = """
            INSERT INTO t_unidades (
                id_empresa,
                numero,
                marca,
                modelo,
                anio,
                matricula,
                tipo,
                imagen,
                vel_max,
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
                output1,
                output2,
                temp_min,
                temp_max,
                fecha_instalacion,
                id_usuario_registro,
                fecha_registro,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
            RETURNING id_unidad
        """
        values = (
            id_empresa,
            payload["numero"],
            payload["marca"],
            payload["modelo"],
            payload["anio"],
            payload["matricula"],
            int(payload["tipo"]) if payload.get("tipo") else 1,
            payload.get("imagen", ""),
            90,
            payload.get("id_modelo_avl"),
            payload["imei"],
            payload["chip"],
            float(payload.get("odometro_inicial", 0)),
            payload.get("tipo_combustible", "1"),
            payload.get("capacidad_tanque"),
            payload.get("rendimiento_establecido"),
            payload.get("no_serie", ""),
            payload.get("nombre_aseguradora", ""),
            payload.get("telefono_aseguradora", ""),
            payload.get("no_poliza_seguro", ""),
            payload.get("vigencia_poliza_seguro"),
            payload.get("vigencia_verificacion_vehicular"),
            int(payload.get("input1", 0)),
            int(payload.get("input2", 0)),
            int(payload.get("output1", 0)),
            int(payload.get("output2", 0)),
            float(payload.get("temp_min", -10.0)),
            float(payload.get("temp_max", 5.0)),
            payload["fecha_instalacion"],
            id_usuario_registro,
            1,
        )
        cursor.execute(query, values)
        new_unit_id = cursor.fetchone()[0]

        id_operador = payload.get("id_operador")
        fecha_asignacion = payload.get("fecha_asignacion_operador")
        if id_operador:
            cursor.execute(
                """
                INSERT INTO r_unidad_operador (id_unidad, id_operador, fecha_asignacion, id_usuario_registro, fecha_registro)
                VALUES (%s, %s, %s, %s, NOW())
            """,
                (
                    new_unit_id,
                    id_operador,
                    fecha_asignacion if fecha_asignacion else None,
                    id_usuario_registro,
                ),
            )

        id_grupo_list = payload.get("id_grupo_unidades", [])
        if id_grupo_list:
            for id_grupo in id_grupo_list:
                cursor.execute(
                    "INSERT INTO r_grupo_unidades_unidades (id_grupo_unidades, id_unidad) VALUES (%s, %s)",
                    (id_grupo, new_unit_id),
                )

        connection.commit()
        return {"id": new_unit_id}
    except Exception as e:
        if connection:
            connection.rollback()
        raise e
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()
