"""Lógica de negocio del catálogo de Rutas."""

import logging
import secrets
from db.connection import get_db_connection, release_db_connection
from validators.route_validators import TIPO_RUTA_MAP

logger = logging.getLogger(__name__)

# Mapa inverso: del número de la BD al string del frontend
TIPO_RUTA_INVERSO = {v: k for k, v in TIPO_RUTA_MAP.items()}


# Codificación de polyline


def encode_polyline(points: list[dict]) -> str:
    """Codifica una lista de {lat, lng} a un string polyline de Google.

    Es el mismo formato que usa Google Maps. Convierte cientos de
    coordenadas en una sola cadena compacta.
    """
    result = []
    prev_lat = 0
    prev_lng = 0

    for point in points:
        lat = int(round(point["lat"] * 1e5))
        lng = int(round(point["lng"] * 1e5))
        result.append(_encode_value(lat - prev_lat))
        result.append(_encode_value(lng - prev_lng))
        prev_lat = lat
        prev_lng = lng

    return "".join(result)


def decode_polyline(encoded: str) -> list[dict]:
    """Decodifica un polyline de vuelta a lista de {lat, lng}."""
    if not encoded:
        return []

    points = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)

    while index < length:
        lat, index = _decode_value(encoded, index, lat)
        lng, index = _decode_value(encoded, index, lng)
        points.append({"lat": lat / 1e5, "lng": lng / 1e5})

    return points


def _encode_value(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chunks = []
    while value >= 0x20:
        chunks.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chunks.append(chr(value + 63))
    return "".join(chunks)


def _decode_value(encoded: str, index: int, prev: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = ord(encoded[index]) - 63
        index += 1
        result |= (byte & 0x1F) << shift
        shift += 5
        if byte < 0x20:
            break
    delta = ~(result >> 1) if (result & 1) else (result >> 1)
    return prev + delta, index


# Listado

_SQL_LIST = """
    SELECT
        r.id_ruta, r.clave, r.nombre, r.tipo,
        c.nombre AS cliente,
        COALESCE(SUM(l.total_paradas), 0) AS total_paradas,
        COUNT(DISTINCT l.id_logistica_ruta) AS total_logisticas,
        MAX(l.kilometros) AS kilometros,
        MIN(l.fecha_inicio) AS fecha_inicio
    FROM t_rutas r
    LEFT JOIN t_clientes c ON c.id_cliente = r.id_cliente
    LEFT JOIN t_logisticas_ruta l ON l.id_ruta = r.id_ruta
    WHERE r.id_empresa = %s AND r.status = 1
    {search_clause}
    GROUP BY r.id_ruta, c.nombre
    ORDER BY r.clave, r.nombre
"""


def get_routes(id_empresa: int, search: str = "") -> list[dict]:
    """Lista las rutas de una empresa, con búsqueda opcional por clave o nombre."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if search:
                clause = "AND (r.clave ILIKE %s OR r.nombre ILIKE %s)"
                like = f"%{search}%"
                cur.execute(
                    _SQL_LIST.format(search_clause=clause), (id_empresa, like, like)
                )
            else:
                cur.execute(_SQL_LIST.format(search_clause=""), (id_empresa,))

            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()

        result = []
        for row in rows:
            record = dict(zip(cols, row))
            record["tipo"] = TIPO_RUTA_INVERSO.get(
                record["tipo"], "transporte_personal"
            )
            if record.get("kilometros") is not None:
                record["kilometros"] = float(record["kilometros"])
            if record.get("fecha_inicio"):
                record["fecha_inicio"] = record["fecha_inicio"].isoformat()
            result.append(record)
        return result
    finally:
        release_db_connection(conn)


# Detalle completo (para editar)


def get_route_by_id(id_ruta: int, id_empresa: int) -> dict | None:
    """Devuelve la ruta completa con sus logísticas, trazos y paradas."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id_ruta, clave, nombre, tipo, id_cliente, observaciones
                FROM t_rutas
                WHERE id_ruta = %s AND id_empresa = %s AND status = 1
                """,
                (id_ruta, id_empresa),
            )
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            route = dict(zip(cols, row))
            route["tipo"] = TIPO_RUTA_INVERSO.get(route["tipo"], "transporte_personal")

            # Grupos a los que pertenece
            cur.execute(
                "SELECT id_grupo_rutas FROM r_grupo_rutas_rutas WHERE id_ruta = %s",
                (id_ruta,),
            )
            route["id_grupo_rutas"] = [r[0] for r in cur.fetchall()]

            # Logísticas con su trazo decodificado y sus paradas
            cur.execute(
                """
                SELECT id_logistica_ruta, tipo_logistica, direccion_inicio,
                       direccion_fin, fecha_inicio, tiempo_recorrido_min,
                       kilometros, encoded_path, trace_color
                FROM t_logisticas_ruta
                WHERE id_ruta = %s
                ORDER BY tipo_logistica
                """,
                (id_ruta,),
            )
            lcols = [d[0] for d in cur.description]
            logisticas = []
            for lrow in cur.fetchall():
                log = dict(zip(lcols, lrow))
                log["path"] = decode_polyline(log.pop("encoded_path") or "")
                if log.get("kilometros") is not None:
                    log["kilometros"] = float(log["kilometros"])
                if log.get("fecha_inicio"):
                    log["fecha_inicio"] = log["fecha_inicio"].isoformat()

                # Paradas de esta logística
                cur.execute(
                    """
                    SELECT id_parada, numero, nombre, direccion, latitud, longitud,
                           tipo_geocerca, radio, poligono
                    FROM t_paradas_ruta
                    WHERE id_logistica_ruta = %s
                    ORDER BY numero
                    """,
                    (log["id_logistica_ruta"],),
                )
                pcols = [d[0] for d in cur.description]
                paradas = []
                for prow in cur.fetchall():
                    parada = dict(zip(pcols, prow))
                    parada["id"] = str(parada.pop("id_parada"))
                    parada["latitud"] = float(parada["latitud"])
                    parada["longitud"] = float(parada["longitud"])
                    paradas.append(parada)
                log["paradas"] = paradas
                logisticas.append(log)

            route["logisticas"] = logisticas
            return route
    finally:
        release_db_connection(conn)


# Crear / actualizar


def is_clave_taken(clave: str, id_empresa: int, exclude_id: int | None = None) -> bool:
    """Revisa si una clave ya existe en la empresa (para evitar duplicados)."""
    if not clave:
        return False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if exclude_id:
                cur.execute(
                    "SELECT 1 FROM t_rutas WHERE clave = %s AND id_empresa = %s AND id_ruta != %s AND status = 1",
                    (clave, id_empresa, exclude_id),
                )
            else:
                cur.execute(
                    "SELECT 1 FROM t_rutas WHERE clave = %s AND id_empresa = %s AND status = 1",
                    (clave, id_empresa),
                )
            return cur.fetchone() is not None
    finally:
        release_db_connection(conn)


def create_route(payload: dict, id_empresa: int, id_usuario: int) -> int:
    """Crea una ruta con sus logísticas y paradas en una sola transacción."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Insertar la ruta
            cur.execute(
                """
                INSERT INTO t_rutas
                    (id_empresa, clave, nombre, tipo, id_cliente, observaciones,
                     token, id_usuario_registro)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_ruta
                """,
                (
                    id_empresa,
                    payload.get("clave") or None,
                    payload["nombre"],
                    TIPO_RUTA_MAP[payload["tipo"]],
                    payload.get("id_cliente") or None,
                    payload.get("observaciones") or "",
                    secrets.token_hex(6),  # token corto para acceso público
                    id_usuario,
                ),
            )
            id_ruta = cur.fetchone()[0]

            # 2. Grupos
            for id_grupo in payload.get("id_grupo_rutas", []):
                cur.execute(
                    "INSERT INTO r_grupo_rutas_rutas (id_grupo_rutas, id_ruta) VALUES (%s, %s)",
                    (id_grupo, id_ruta),
                )

            # 3. Logísticas + paradas
            for log in payload["logisticas"]:
                _insert_logistica(cur, log, id_ruta, id_usuario)

            conn.commit()
            return id_ruta
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def _insert_logistica(cur, log: dict, id_ruta: int, id_usuario: int) -> None:
    """Inserta una logística con su trazo codificado y sus paradas."""
    paradas = log.get("paradas", [])
    encoded = encode_polyline(log.get("path", []))

    cur.execute(
        """
        INSERT INTO t_logisticas_ruta
            (id_ruta, tipo_logistica, direccion_inicio, direccion_fin,
             fecha_inicio, tiempo_recorrido_min, kilometros, encoded_path,
             trace_color, total_paradas, id_usuario_registro)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_logistica_ruta
        """,
        (
            id_ruta,
            log["tipo_logistica"],
            log.get("direccion_inicio") or "",
            log.get("direccion_fin") or "",
            log.get("fecha_inicio") or None,
            log.get("tiempo_recorrido_min") or None,
            log.get("kilometros") or None,
            encoded,
            log.get("trace_color") or "#2563eb",
            len(paradas),
            id_usuario,
        ),
    )
    id_logistica = cur.fetchone()[0]

    # Paradas de esta logística
    import json

    for parada in paradas:
        poligono = parada.get("poligono")
        cur.execute(
            """
            INSERT INTO t_paradas_ruta
                (id_logistica_ruta, id_ruta, numero, nombre, direccion,
                 latitud, longitud, tipo_geocerca, radio, poligono)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                id_logistica,
                id_ruta,
                parada["numero"],
                parada.get("nombre") or "",
                parada.get("direccion") or "",
                parada["latitud"],
                parada["longitud"],
                parada.get("tipo_geocerca", "circular"),
                parada.get("radio", 100),
                json.dumps(poligono) if poligono else None,
            ),
        )


def update_route(id_ruta: int, payload: dict, id_empresa: int, id_usuario: int) -> bool:
    """Actualiza una ruta. Reemplaza logísticas y paradas por simplicidad."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificar que la ruta exista y sea de la empresa
            cur.execute(
                "SELECT 1 FROM t_rutas WHERE id_ruta = %s AND id_empresa = %s AND status = 1",
                (id_ruta, id_empresa),
            )
            if not cur.fetchone():
                return False

            # Actualizar campos de la ruta
            cur.execute(
                """
                UPDATE t_rutas SET
                    clave = %s, nombre = %s, tipo = %s, id_cliente = %s,
                    observaciones = %s, fecha_cambio = CURRENT_TIMESTAMP,
                    id_usuario_cambio = %s
                WHERE id_ruta = %s
                """,
                (
                    payload.get("clave") or None,
                    payload["nombre"],
                    TIPO_RUTA_MAP[payload["tipo"]],
                    payload.get("id_cliente") or None,
                    payload.get("observaciones") or "",
                    id_usuario,
                    id_ruta,
                ),
            )

            # Reemplazar grupos
            cur.execute(
                "DELETE FROM r_grupo_rutas_rutas WHERE id_ruta = %s", (id_ruta,)
            )
            for id_grupo in payload.get("id_grupo_rutas", []):
                cur.execute(
                    "INSERT INTO r_grupo_rutas_rutas (id_grupo_rutas, id_ruta) VALUES (%s, %s)",
                    (id_grupo, id_ruta),
                )

            # Reemplazar logísticas (el ON DELETE CASCADE borra sus paradas)
            cur.execute("DELETE FROM t_logisticas_ruta WHERE id_ruta = %s", (id_ruta,))
            for log in payload["logisticas"]:
                _insert_logistica(cur, log, id_ruta, id_usuario)

            conn.commit()
            return True
    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def delete_route(id_ruta: int, id_empresa: int) -> bool:
    """Soft-delete: marca la ruta como inactiva (status=0)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE t_rutas SET status = 0 WHERE id_ruta = %s AND id_empresa = %s AND status = 1",
                (id_ruta, id_empresa),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        release_db_connection(conn)
