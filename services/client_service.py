import logging
import secrets
from db.connection import get_db_connection, release_db_connection
from utils.date_utils import fmt_dt
from services.poi_service import insert_poi, update_poi_fields

logger = logging.getLogger(__name__)

# Longitud del token generado para cada cliente.
# Se usa en token y token_dashboard al momento de crear.
_TOKEN_LENGTH = 32


def _generate_token() -> str:
    """
    Genera un token único de 64 caracteres hex seguros.
    Usamos secrets.token_hex que genera un string hexadecimal de longitud 2*n.

    Esto es más que suficiente para evitar colisiones incluso con miles de clientes,
    y es seguro para usar como token de autenticación.
    """
    return secrets.token_hex(_TOKEN_LENGTH)


def get_clients(id_empresa: int, search: str | None = None) -> list[dict]:
    """
    Lista todos los clientes de una empresa.

    Hace JOIN con t_pois para traer dirección y coordenadas del cliente.

    El campo 'coordenadas' concatena lat y lng.

    id_empresa : ID de la empresa del usuario autenticado (viene del JWT)
    search     : busca en nombre, contacto, telefono, email y observaciones
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # La búsqueda usa ILIKE para ser case-insensitive en PostgreSQL.
        query = """
            SELECT
                c.id_cliente,
                c.id_empresa,
                c.clave,
                c.nombre,
                c.contacto,
                c.telefono,
                c.email,
                c.imagen,
                c.observaciones,
                c.id_poi,
                c.fecha_registro,
                c.fecha_cambio,
                p.direccion,
                CONCAT(ROUND(p.lat::numeric, 6), ',', ROUND(p.lng::numeric, 6)) AS coordenadas
            FROM t_clientes c
            LEFT JOIN t_pois p ON p.id_poi = c.id_poi
            WHERE c.id_empresa = %s
        """
        params = [id_empresa]

        # aplica en todos los campos de texto relevantes
        if search:
            query += """
                AND (
                    c.nombre        ILIKE %s
                    OR c.contacto   ILIKE %s
                    OR c.telefono   ILIKE %s
                    OR c.email      ILIKE %s
                    OR c.observaciones ILIKE %s
                    OR p.direccion  ILIKE %s
                )
            """
            term = f"%{search}%"
            # Repite el término una vez por cada campo del OR
            params.extend([term] * 6)

        query += " ORDER BY c.nombre ASC"

        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        # Construye la lista de dicts con nombres explícitos
        result = []
        for row in rows:
            result.append(
                {
                    "id_cliente": row[0],
                    "id_empresa": row[1],
                    "clave": row[2],
                    "nombre": row[3],
                    "contacto": row[4],
                    "telefono": row[5],
                    "email": row[6],
                    "imagen": row[7],
                    "observaciones": row[8],
                    "id_poi": row[9],
                    "fecha_registro": fmt_dt(row[10]) if row[10] else None,
                    "fecha_cambio": fmt_dt(row[11]) if row[11] else None,
                    "direccion": row[12],
                    "coordenadas": row[13],
                }
            )
        return result

    finally:
        # garantiza que siempre se libera la conexión al pool,
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def get_client_by_id(id_cliente: int, id_empresa: int) -> dict | None:
    """
    Obtiene un cliente por su ID.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                c.id_cliente,
                c.id_empresa,
                c.clave,
                c.nombre,
                c.contacto,
                c.telefono,
                c.email,
                c.imagen,
                c.observaciones,
                c.id_poi,
                c.acceso_token_rastreo,
                c.token,
                c.token_dashboard,
                c.acceso_dashboard_cmp,
                c.acceso_global,
                c.fecha_registro,
                c.id_usuario_registro,
                c.fecha_cambio,
                c.id_usuario_cambio,
                p.direccion,
                CASE
                    WHEN p.lat IS NULL OR p.lng IS NULL THEN NULL
                    ELSE CONCAT(ROUND(p.lat::numeric, 6), ',', ROUND(p.lng::numeric, 6))
                END AS coordenadas,
                p.tipo_poi,
                p.lat,
                p.lng,
                p.radio,
                p.bounds,
                p.area,
                p.polygon_path,
                p.polygon_color,
                p.radio_color
            FROM t_clientes c
            LEFT JOIN t_pois p ON p.id_poi = c.id_poi
            WHERE c.id_cliente = %s
              AND c.id_empresa  = %s
            """,
            (id_cliente, id_empresa),
        )
        row = cursor.fetchone()

        if not row:
            return None

        # Objeto poi completo para que el GeoFenceTab repinte la geocerca al
        # editar (igual que en operadores). Solo se arma si el cliente tiene
        # POI ligado (row[21]=tipo_poi presente y lat/lng no nulos).
        poi = None
        if row[9] is not None and row[22] is not None and row[23] is not None:
            poi = {
                "tipo_poi": row[21],
                "direccion": row[19],
                "lat": float(row[22]) if row[22] is not None else None,
                "lng": float(row[23]) if row[23] is not None else None,
                "radio": row[24],
                "bounds": row[25],
                "area": row[26],
                "polygon_path": row[27],
                "polygon_color": row[28],
                "radio_color": row[29],
            }

        return {
            "id_cliente": row[0],
            "id_empresa": row[1],
            "clave": row[2],
            "nombre": row[3],
            "contacto": row[4],
            "telefono": row[5],
            "email": row[6],
            "imagen": row[7],
            "observaciones": row[8],
            "id_poi": row[9],
            "acceso_token_rastreo": row[10],
            "token": row[11],
            "token_dashboard": row[12],
            "acceso_dashboard_cmp": row[13],
            "acceso_global": row[14],
            "fecha_registro": fmt_dt(row[15]) if row[15] else None,
            "id_usuario_registro": row[16],
            "fecha_cambio": fmt_dt(row[17]) if row[17] else None,
            "id_usuario_cambio": row[18],
            "direccion": row[19],
            "coordenadas": row[20],
            "poi": poi,
        }

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def is_clave_taken(clave: str, id_empresa: int, exclude_id: int | None = None) -> bool:
    """
    Comprueba si una clave ya está en uso dentro de la empresa.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        query = "SELECT 1 FROM t_clientes WHERE LOWER(clave) = LOWER(%s) AND id_empresa = %s"
        params = [clave, id_empresa]

        if exclude_id:
            query += " AND id_cliente <> %s"
            params.append(exclude_id)

        cursor.execute(query, tuple(params))
        return cursor.fetchone() is not None

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def create_client(payload: dict, id_empresa: int, id_usuario: int) -> dict:
    """
    Crea un nuevo cliente en t_clientes.

    Genera token y token_dashboard automáticamente — el usuario no los
    configura al crear, solo se exponen en la vista de detalle/configuración.

    Retorna el cliente recién creado completo (con id_cliente asignado).

    Lanza ValueError si la clave ya está en uso en la empresa.
    """
    # Validar clave única antes de abrir transacción
    if is_clave_taken(payload["clave"], id_empresa):
        raise ValueError(
            f"La clave '{payload['clave']}' ya está en uso en esta empresa"
        )

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Domicilio (geocerca): si el payload trae un objeto "poi" con coordenadas,
        # creamos el POI DENTRO de esta misma transacción usando insert_poi (que no
        # hace commit propio). Así el POI y el cliente se crean atómicamente: si
        # falla el INSERT del cliente, el rollback deshace también el POI. Si no
        # viene "poi", se usa id_poi del payload (compatibilidad).
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
                    "tipo_elemento": "cliente",
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
                id_usuario_registro=id_usuario,
            )

        cursor.execute(
            """
            INSERT INTO t_clientes (
                id_empresa,
                clave,
                nombre,
                contacto,
                telefono,
                email,
                imagen,
                observaciones,
                id_poi,
                token,
                token_dashboard,
                id_usuario_registro,
                fecha_registro,
                id_usuario_cambio,
                fecha_cambio
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, NOW(), %s, NOW()
            )
            RETURNING id_cliente
            """,
            (
                id_empresa,
                payload["clave"],
                payload["nombre"],
                payload.get("contacto"),
                payload.get("telefono"),
                payload.get("email"),
                payload.get("imagen"),
                payload.get("observaciones"),
                id_poi,
                _generate_token(),
                _generate_token(),
                id_usuario,
                id_usuario,
            ),
        )

        id_cliente = cursor.fetchone()[0]
        connection.commit()

        # Regresa el objeto completo para no hacer un segundo query en el route
        return get_client_by_id(id_cliente, id_empresa)

    except Exception:
        # Si algo falla después del INSERT, deshacemos todo
        if connection:
            connection.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def update_client(
    id_cliente: int, payload: dict, id_empresa: int, id_usuario: int
) -> dict | None:
    """
    Actualiza los campos de un cliente existente.

    Solo actualiza los campos que vengan en el payload (no nulos en el schema).
    Construye el SET dinámico para no sobrescribir campos que el usuario
    no quiso tocar.

    Retorna el cliente actualizado, o None si no existe/no pertenece a la empresa.

    Lanza ValueError si la nueva clave ya está en uso por otro cliente.
    """
    # Verificar que el cliente existe y pertenece a la empresa
    existing = get_client_by_id(id_cliente, id_empresa)
    if not existing:
        return None

    # Si viene una nueva clave, validar que no esté en uso
    if payload.get("clave") and payload["clave"] != existing["clave"]:
        if is_clave_taken(payload["clave"], id_empresa, exclude_id=id_cliente):
            raise ValueError(
                f"La clave '{payload['clave']}' ya está en uso en esta empresa"
            )

    # Solo incluir en el SET los campos que realmente vinieron en el payload
    # Esto evita borrar datos si el cliente solo quiere cambiar el nombre
    updatable_fields = [
        "clave",
        "nombre",
        "contacto",
        "telefono",
        "email",
        "imagen",
        "observaciones",
        "id_poi",
    ]
    set_parts = []
    values = []

    for field in updatable_fields:
        # Incluye el campo solo si NO es None en el payload
        if (
            payload.get(field) is not None
            or field in payload
            and payload[field] is None
        ):
            # Caso especial: si el campo está en payload aunque sea None, lo actualiza
            # (el usuario lo puede querer borrar, por ejemplo telefono = null)
            if field in payload:
                set_parts.append(f"{field} = %s")
                values.append(payload[field])

    # Si no hay campos del cliente que actualizar Y tampoco viene un poi,
    # no hay nada que hacer — devuelve el estado actual sin tocar la BD.
    tiene_poi = bool(payload.get("poi"))
    if not set_parts and not tiene_poi:
        return existing

    # Solo armar el SET de t_clientes si hay campos del cliente que cambiar.
    # (Puede venir solo el poi: en ese caso set_parts queda vacío y se salta
    # el UPDATE de t_clientes, pero sí se procesa la geocerca más abajo.)
    if set_parts:
        # Siempre actualizar auditoría cuando se toca el cliente
        set_parts.append("id_usuario_cambio = %s")
        values.append(id_usuario)
        set_parts.append("fecha_cambio = NOW()")

        # Añadir los filtros al final del params
        values.extend([id_cliente, id_empresa])

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if set_parts:
            cursor.execute(
                f"UPDATE t_clientes SET {', '.join(set_parts)} WHERE id_cliente = %s AND id_empresa = %s",
                tuple(values),
            )

        # Domicilio (geocerca): si llega el objeto "poi" con coordenadas, lo
        # gestionamos en la misma transacción (atómico):
        #   - Si el cliente YA tiene id_poi → UPDATE de ese POI (conserva el id).
        #   - Si NO tiene → creamos uno nuevo y ligamos su id_poi al cliente.
        poi_data = payload.get("poi")
        if (
            poi_data
            and poi_data.get("lat") is not None
            and poi_data.get("lng") is not None
        ):
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
            id_poi_actual = existing.get("id_poi")
            if id_poi_actual:
                update_poi_fields(
                    cursor=cursor,
                    id_poi=id_poi_actual,
                    id_empresa=id_empresa,
                    payload=geo_payload,
                    id_usuario_cambio=id_usuario,
                )
            else:
                nuevo_id_poi = insert_poi(
                    cursor=cursor,
                    payload={
                        "tipo_elemento": "cliente",
                        "nombre": payload.get("nombre") or existing.get("nombre"),
                        **geo_payload,
                    },
                    id_empresa=id_empresa,
                    id_usuario_registro=id_usuario,
                )
                cursor.execute(
                    "UPDATE t_clientes SET id_poi = %s WHERE id_cliente = %s AND id_empresa = %s",
                    (nuevo_id_poi, id_cliente, id_empresa),
                )

        connection.commit()

        return get_client_by_id(id_cliente, id_empresa)

    except Exception:
        if connection:
            connection.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def delete_client(id_cliente: int, id_empresa: int) -> bool:
    """
    Elimina un cliente de forma permanente.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "DELETE FROM t_clientes WHERE id_cliente = %s AND id_empresa = %s",
            (id_cliente, id_empresa),
        )
        rows_affected = cursor.rowcount
        connection.commit()

        # rowcount = 0 significa que no existía el cliente con ese id_empresa
        return rows_affected > 0

    except Exception:
        if connection:
            connection.rollback()
        raise

    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
