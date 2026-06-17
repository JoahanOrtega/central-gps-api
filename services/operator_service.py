import logging
from db.connection import get_db_connection, release_db_connection
from services.telemetry_service import to_app_iso
from services.poi_service import insert_poi, update_poi_fields

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Campos editables vía PATCH /operadores/<id>
# ─────────────────────────────────────────────────────────────────────────────
# Set explícito (defensa en profundidad, igual que en poi_service): aunque el
# schema marshmallow ya filtra campos no declarados, este set bloquea cualquier
# campo nuevo hasta que se decida explícitamente que es editable. id_empresa,
# fecha_registro e id_usuario_registro NUNCA se editan tras la creación.
#
# NOTA: la columna rfid_tag existe en t_operadores (heredada del esquema v2.5)
# pero NO se gestiona desde este catálogo. El RFID se usa en el módulo de
# aforos, no para identificar operadores. La columna se deja intacta en la BD.
_UPDATABLE_OPERATOR_FIELDS = frozenset(
    {
        "id_poi",
        "id_unidad_operador",
        "clave",
        "nombre",
        "imagen",
        "direccion",
        "telefono",
        "fecha_nacimiento",
        "licencia",
        "tipo_licencia",
        "vencimiento_licencia",
        "erp_link",
    }
)


def _map_operator_row(row):
    """Convierte una fila de t_operadores al dict que espera el frontend."""
    return {
        "id_operador": row[0],
        "id_empresa": row[1],
        "id_poi": row[2],
        "id_unidad_operador": row[3],
        "clave": row[4],
        "nombre": row[5],
        "imagen": row[6],
        "direccion": row[7],
        "telefono": row[8],
        "fecha_nacimiento": row[9].isoformat() if row[9] else None,
        "licencia": row[10],
        "tipo_licencia": row[11],
        "vencimiento_licencia": row[12].isoformat() if row[12] else None,
        "erp_link": row[13],
        "fecha_registro": to_app_iso(row[14]) if row[14] else None,
        "id_usuario_registro": row[15],
        "fecha_cambio": to_app_iso(row[16]) if row[16] else None,
        "id_usuario_cambio": row[17],
    }


# Columnas en el orden exacto que consume _map_operator_row.
_OPERATOR_COLUMNS = """
    id_operador,
    id_empresa,
    id_poi,
    id_unidad_operador,
    clave,
    nombre,
    imagen,
    direccion,
    telefono,
    fecha_nacimiento,
    licencia,
    tipo_licencia,
    vencimiento_licencia,
    erp_link,
    fecha_registro,
    id_usuario_registro,
    fecha_cambio,
    id_usuario_cambio
"""


def get_operators(id_empresa, search=None):
    """
    Lista operadores activos (status=1) de una empresa.

    Los operadores eliminados (soft-delete, status=0) nunca aparecen en el
    catálogo. La búsqueda filtra por nombre o clave.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = f"""
            SELECT {_OPERATOR_COLUMNS}
            FROM t_operadores
            WHERE id_empresa = %s
              AND status     = 1
        """
        params = [id_empresa]
        if search:
            query += """
                AND (
                    LOWER(nombre)   LIKE LOWER(%s)
                    OR LOWER(clave) LIKE LOWER(%s)
                )
            """
            like = f"%{search}%"
            params.extend([like, like])
        query += " ORDER BY id_operador DESC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        operadores = [_map_operator_row(r) for r in rows]

        # Adjuntar los grupos de cada operador en una sola consulta extra,
        # evitando N+1: traemos todas las relaciones de la empresa y las
        # agrupamos en memoria.
        _attach_groups(cursor, operadores)
        return operadores
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def _attach_poi(cursor, operador):
    """
    Adjunta los datos de la geocerca (POI) al detalle de un operador.

    Si el operador tiene id_poi, consulta t_pois y agrega un objeto "poi" con
    los campos que el frontend necesita para repintar la geocerca en el mapa
    (GeoFenceTab). Si no tiene domicilio, "poi" queda en None.

    Solo se usa en el detalle (get_operator), no en el listado — el catálogo
    no necesita la geometría de cada operador.
    """
    id_poi = operador.get("id_poi")
    if not id_poi:
        operador["poi"] = None
        return

    cursor.execute(
        """
        SELECT tipo_poi, direccion, lat, lng, radio, bounds, area,
               polygon_path, polygon_color, radio_color
        FROM t_pois
        WHERE id_poi = %s AND status = 1
        """,
        (id_poi,),
    )
    row = cursor.fetchone()
    if row is None:
        operador["poi"] = None
        return

    operador["poi"] = {
        "tipo_poi": row[0],
        "direccion": row[1],
        "lat": float(row[2]) if row[2] is not None else None,
        "lng": float(row[3]) if row[3] is not None else None,
        "radio": row[4],
        "bounds": row[5],
        "area": row[6],
        "polygon_path": row[7],
        "polygon_color": row[8],
        "radio_color": row[9],
    }


def get_operator(id_operador, id_empresa):
    """Obtiene un operador individual por id, validando pertenencia a empresa."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            f"""
            SELECT {_OPERATOR_COLUMNS}
            FROM t_operadores
            WHERE id_operador = %s
              AND id_empresa  = %s
              AND status      = 1
            """,
            (id_operador, id_empresa),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        operador = _map_operator_row(row)
        _attach_groups(cursor, [operador])
        _attach_poi(cursor, operador)
        return operador
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_operator(payload, id_empresa, id_usuario_registro):
    """Crea un operador y sus relaciones con grupos."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Domicilio (geocerca): si el payload trae un objeto "poi" con coordenadas,
        # creamos el POI DENTRO de esta misma transacción usando insert_poi (que no
        # hace commit propio). Así el POI y el operador se crean atómicamente: si
        # falla el INSERT del operador, el rollback deshace también el POI — sin
        # POIs huérfanos. El id_poi resultante se liga en la columna id_poi.
        poi_data = payload.get("poi")
        id_poi = payload.get("id_poi")
        if (
            poi_data
            and poi_data.get("lat") is not None
            and poi_data.get("lng") is not None
        ):
            id_poi = insert_poi(
                cursor=cursor,
                payload={
                    "tipo_elemento": "operador",
                    "nombre": payload.get("nombre"),
                    "direccion": poi_data.get("direccion"),
                    "tipo_poi": poi_data.get("tipo_poi"),
                    "lat": poi_data.get("lat"),
                    "lng": poi_data.get("lng"),
                    "radio": poi_data.get("radio"),
                    "bounds": poi_data.get("bounds"),
                    "area": poi_data.get("area"),
                    "radio_color": poi_data.get("radio_color"),
                    "polygon_path": poi_data.get("polygon_path"),
                    "polygon_color": poi_data.get("polygon_color"),
                },
                id_empresa=id_empresa,
                id_usuario_registro=id_usuario_registro,
            )

        cursor.execute(
            """
            INSERT INTO t_operadores (
                id_empresa,
                id_poi,
                id_unidad_operador,
                clave,
                nombre,
                imagen,
                direccion,
                telefono,
                fecha_nacimiento,
                licencia,
                tipo_licencia,
                vencimiento_licencia,
                erp_link,
                fecha_registro,
                id_usuario_registro,
                status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, 1)
            RETURNING id_operador
            """,
            (
                id_empresa,
                id_poi,
                payload.get("id_unidad_operador"),
                payload.get("clave"),
                payload.get("nombre"),
                payload.get("imagen"),
                payload.get("direccion"),
                payload.get("telefono"),
                payload.get("fecha_nacimiento") or None,
                payload.get("licencia"),
                payload.get("tipo_licencia"),
                payload.get("vencimiento_licencia") or None,
                payload.get("erp_link"),
                id_usuario_registro,
            ),
        )
        operador_id = cursor.fetchone()[0]
        _save_operator_groups(
            cursor=cursor,
            id_operador=operador_id,
            group_ids=payload.get("id_grupo_operadores", []),
        )
        connection.commit()
        return {"id_operador": operador_id}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en create_operator id_empresa=%s nombre=%s: %s",
            id_empresa,
            payload.get("nombre"),
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def update_operator(id_operador, id_empresa, payload, id_usuario_cambio):
    """
    Actualiza un operador. Solo aplica campos del set _UPDATABLE_OPERATOR_FIELDS.
    Si el payload trae id_grupo_operadores, resincroniza las relaciones.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Verificar pertenencia antes de tocar nada. Traemos id_poi para saber
        # si el operador ya tiene domicilio (geocerca) y decidir crear vs actualizar.
        cursor.execute(
            "SELECT id_poi FROM t_operadores WHERE id_operador = %s AND id_empresa = %s AND status = 1",
            (id_operador, id_empresa),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        id_poi_actual = row[0]

        # Construir SET dinámico solo con campos editables presentes en payload.
        fields = []
        values = []
        for key in _UPDATABLE_OPERATOR_FIELDS:
            if key in payload:
                value = payload[key]
                # Fechas vacías → NULL para no romper el tipo date.
                if key in ("fecha_nacimiento", "vencimiento_licencia") and not value:
                    value = None
                fields.append(f"{key} = %s")
                values.append(value)

        if fields:
            fields.append("fecha_cambio = NOW()")
            fields.append("id_usuario_cambio = %s")
            values.append(id_usuario_cambio)
            values.extend([id_operador, id_empresa])
            cursor.execute(
                f"""
                UPDATE t_operadores
                SET {", ".join(fields)}
                WHERE id_operador = %s AND id_empresa = %s
                """,
                tuple(values),
            )

        # Resincronizar grupos solo si vienen en el payload (None = no tocar).
        if "id_grupo_operadores" in payload:
            _save_operator_groups(
                cursor=cursor,
                id_operador=id_operador,
                group_ids=payload.get("id_grupo_operadores") or [],
                replace=True,
            )

        # Domicilio (geocerca): si llega el objeto "poi" con coordenadas, lo
        # gestionamos dentro de esta misma transacción (atómico):
        #   - Si el operador YA tiene id_poi → UPDATE de ese POI (no deja basura).
        #   - Si NO tiene → creamos uno nuevo y ligamos su id_poi al operador.
        poi_data = payload.get("poi")
        if (
            poi_data
            and poi_data.get("lat") is not None
            and poi_data.get("lng") is not None
        ):
            # Campos de geocerca comunes a crear y actualizar. NO incluimos
            # "nombre" aquí: en el update no debe sobrescribir el nombre del POI
            # (t_pois.nombre es NOT NULL y al editar solo el domicilio llegaría
            # null). El nombre se fija al crear el POI.
            geo_payload = {
                "tipo_poi": poi_data.get("tipo_poi"),
                "direccion": poi_data.get("direccion"),
                "lat": poi_data.get("lat"),
                "lng": poi_data.get("lng"),
                "radio": poi_data.get("radio"),
                "bounds": poi_data.get("bounds"),
                "area": poi_data.get("area"),
                "radio_color": poi_data.get("radio_color"),
                "polygon_path": poi_data.get("polygon_path"),
                "polygon_color": poi_data.get("polygon_color"),
            }
            if id_poi_actual:
                # Actualiza el POI existente conservando su id_poi y su nombre.
                update_poi_fields(
                    cursor=cursor,
                    id_poi=id_poi_actual,
                    id_empresa=id_empresa,
                    payload=geo_payload,
                    id_usuario_cambio=id_usuario_cambio,
                )
            else:
                # Crea un POI nuevo y lo liga al operador. Aquí sí ponemos nombre
                # (el del operador) porque es un INSERT y t_pois.nombre es NOT NULL.
                nuevo_id_poi = insert_poi(
                    cursor=cursor,
                    payload={
                        "tipo_elemento": "operador",
                        "nombre": payload.get("nombre"),
                        **geo_payload,
                    },
                    id_empresa=id_empresa,
                    id_usuario_registro=id_usuario_cambio,
                )
                cursor.execute(
                    "UPDATE t_operadores SET id_poi = %s WHERE id_operador = %s AND id_empresa = %s",
                    (nuevo_id_poi, id_operador, id_empresa),
                )

        connection.commit()
        return {"id_operador": id_operador}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en update_operator id_operador=%s id_empresa=%s: %s",
            id_operador,
            id_empresa,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def delete_operator(id_operador, id_empresa, id_usuario_cambio):
    """
    Soft-delete: marca status=0. No elimina físicamente para preservar
    historial de asignaciones (r_unidad_operador) y auditoría.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE t_operadores
            SET status = 0,
                fecha_cambio = NOW(),
                id_usuario_cambio = %s
            WHERE id_operador = %s
              AND id_empresa  = %s
              AND status      = 1
            """,
            (id_usuario_cambio, id_operador, id_empresa),
        )
        affected = cursor.rowcount
        connection.commit()
        return affected > 0
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en delete_operator id_operador=%s id_empresa=%s: %s",
            id_operador,
            id_empresa,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de grupos (relación N:M)
# ─────────────────────────────────────────────────────────────────────────────


def _attach_groups(cursor, operadores):
    """
    Adjunta la lista id_grupo_operadores a cada operador del listado.
    Una sola consulta para todos (evita N+1).
    """
    if not operadores:
        return
    ids = [o["id_operador"] for o in operadores]
    cursor.execute(
        """
        SELECT id_operador, id_grupo_operadores
        FROM r_grupo_operadores_operadores
        WHERE id_operador = ANY(%s)
        """,
        (ids,),
    )
    by_operator = {}
    for id_op, id_grupo in cursor.fetchall():
        by_operator.setdefault(id_op, []).append(id_grupo)
    for o in operadores:
        o["id_grupo_operadores"] = by_operator.get(o["id_operador"], [])


def _save_operator_groups(cursor, id_operador, group_ids, replace=False):
    """
    Inserta las relaciones operador↔grupo. Si replace=True (edición), primero
    borra las existentes para resincronizar. La PK compuesta + ON CONFLICT
    evita duplicados.
    """
    if replace:
        cursor.execute(
            "DELETE FROM r_grupo_operadores_operadores WHERE id_operador = %s",
            (id_operador,),
        )
    for id_grupo in group_ids or []:
        cursor.execute(
            """
            INSERT INTO r_grupo_operadores_operadores (id_grupo_operadores, id_operador)
            VALUES (%s, %s)
            ON CONFLICT (id_grupo_operadores, id_operador) DO NOTHING
            """,
            (id_grupo, id_operador),
        )
