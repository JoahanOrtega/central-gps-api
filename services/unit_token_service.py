import logging
import secrets
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)

_TOKEN_LEN = 15


def _generate_token(length: int) -> str:
    return secrets.token_urlsafe(length)[:length]


# Helpers


def _check_unit_ownership(cursor, id_unidad: int, id_empresa: int) -> bool:
    cursor.execute(
        "SELECT 1 FROM t_unidades WHERE id_unidad = %s AND id_empresa = %s",
        (id_unidad, id_empresa),
    )
    return cursor.fetchone() is not None


def _ensure_token_row(cursor, id_unidad: int, id_empresa: int):
    cursor.execute(
        """
        INSERT INTO t_unidades_token (id_unidad, id_empresa)
        VALUES (%s, %s)
        ON CONFLICT (id_unidad) DO NOTHING
        """,
        (id_unidad, id_empresa),
    )


def _generate_unique_token(cursor) -> str:
    for _ in range(5):
        candidato = _generate_token(_TOKEN_LEN)
        cursor.execute(
            "SELECT 1 FROM t_unidades_token WHERE token = %s OR token_temporal = %s",
            (candidato, candidato),
        )
        if cursor.fetchone() is None:
            return candidato
    raise RuntimeError("No se pudo generar un token único")


# Lectura


def get_unit_token_config(id_unidad: int, id_empresa: int) -> dict | None:
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if not _check_unit_ownership(cursor, id_unidad, id_empresa):
            return None

        cursor.execute(
            """
            SELECT
                acceso_token_rastreo, token,
                token_requiere_clave_acceso, token_clave_acceso,
                fecha_expiracion,
                token_temporal, acceso_temporal, fecha_expiracion_temporal
            FROM t_unidades_token
            WHERE id_unidad = %s
            """,
            (id_unidad,),
        )
        row = cursor.fetchone()
        if not row:
            return _default_token_cfg()

        return {
            "acceso_token_rastreo": row[0],
            "token": row[1],
            "token_requiere_clave_acceso": row[2],
            "token_clave_acceso": row[3],
            "fecha_expiracion": row[4].isoformat() if row[4] else None,
            "token_temporal": row[5],
            "acceso_temporal": row[6],
            "fecha_expiracion_temporal": row[7].isoformat() if row[7] else None,
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def _default_token_cfg() -> dict:
    return {
        "acceso_token_rastreo": False,
        "token": None,
        "token_requiere_clave_acceso": False,
        "token_clave_acceso": None,
        "fecha_expiracion": None,
        "token_temporal": None,
        "acceso_temporal": False,
        "fecha_expiracion_temporal": None,
    }


# Token PERMANENTE


def regenerate_tracking_token(id_unidad: int, id_empresa: int) -> dict | None:
    """Genera/regenera el token PERMANENTE (sin expiración)."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if not _check_unit_ownership(cursor, id_unidad, id_empresa):
            return None

        _ensure_token_row(cursor, id_unidad, id_empresa)
        nuevo_token = _generate_unique_token(cursor)

        cursor.execute(
            """
            UPDATE t_unidades_token
               SET token = %s,
                   acceso_token_rastreo = TRUE,
                   fecha_expiracion = NULL,
                   fecha_actualizacion = NOW() AT TIME ZONE 'America/Mexico_City'
             WHERE id_unidad = %s
            """,
            (nuevo_token, id_unidad),
        )
        connection.commit()
        return {"token": nuevo_token}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error en regenerate_tracking_token: %s", repr(e))
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def revoke_tracking_token(id_unidad: int, id_empresa: int) -> bool:
    """Revoca SOLO el permanente. El temporal no se toca."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if not _check_unit_ownership(cursor, id_unidad, id_empresa):
            return False

        cursor.execute(
            """
            UPDATE t_unidades_token
               SET token = NULL,
                   acceso_token_rastreo = FALSE,
                   fecha_actualizacion = NOW() AT TIME ZONE 'America/Mexico_City'
             WHERE id_unidad = %s
            """,
            (id_unidad,),
        )
        connection.commit()
        return True
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error en revoke_tracking_token: %s", repr(e))
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# Token TEMPORAL


def regenerate_temporal_token(
    id_unidad: int, id_empresa: int, minutos_expiracion: int
) -> dict | None:
    """Genera/regenera el token TEMPORAL (con expiración obligatoria)."""
    if not minutos_expiracion or minutos_expiracion <= 0:
        raise ValueError("El token temporal requiere duración positiva")

    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if not _check_unit_ownership(cursor, id_unidad, id_empresa):
            return None

        _ensure_token_row(cursor, id_unidad, id_empresa)
        nuevo_token = _generate_unique_token(cursor)

        cursor.execute(
            """
            UPDATE t_unidades_token
               SET token_temporal = %s,
                   acceso_temporal = TRUE,
                   fecha_expiracion_temporal = (NOW() AT TIME ZONE 'America/Mexico_City')
                                               + (%s * INTERVAL '1 minute'),
                   fecha_actualizacion = NOW() AT TIME ZONE 'America/Mexico_City'
             WHERE id_unidad = %s
            """,
            (nuevo_token, minutos_expiracion, id_unidad),
        )
        connection.commit()
        return {"token": nuevo_token}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error en regenerate_temporal_token: %s", repr(e))
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def revoke_temporal_token(id_unidad: int, id_empresa: int) -> bool:
    """Revoca SOLO el temporal. El permanente no se toca."""
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        if not _check_unit_ownership(cursor, id_unidad, id_empresa):
            return False

        cursor.execute(
            """
            UPDATE t_unidades_token
               SET token_temporal = NULL,
                   acceso_temporal = FALSE,
                   fecha_expiracion_temporal = NULL,
                   fecha_actualizacion = NOW() AT TIME ZONE 'America/Mexico_City'
             WHERE id_unidad = %s
            """,
            (id_unidad,),
        )
        connection.commit()
        return True
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error en revoke_temporal_token: %s", repr(e))
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# SIN autenticación


def get_unit_by_token(token: str) -> dict | None:
    """
    Resuelve un token a los datos públicos de la unidad.
    Busca en AMBAS columnas (permanente y temporal).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                u.id_unidad, u.numero, u.marca, u.modelo, u.imei, u.vel_max,
                t.token, t.acceso_token_rastreo, t.fecha_expiracion,
                t.token_temporal, t.acceso_temporal, t.fecha_expiracion_temporal
            FROM t_unidades_token t
            JOIN t_unidades u ON u.id_unidad = t.id_unidad
            WHERE t.token = %s OR t.token_temporal = %s
            """,
            (token, token),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        cursor.execute("SELECT NOW() AT TIME ZONE 'America/Mexico_City'")
        ahora = cursor.fetchone()[0]

        token_perm, acceso_perm, exp_perm = row[6], row[7], row[8]
        token_temp, acceso_temp, exp_temp = row[9], row[10], row[11]

        valido = False

        if token == token_perm and acceso_perm:
            if exp_perm is None or exp_perm > ahora:
                valido = True

        if not valido and token == token_temp and acceso_temp:
            if exp_temp is not None and exp_temp > ahora:
                valido = True

        if not valido:
            return None

        return {
            "id_unidad": row[0],
            "numero": row[1],
            "marca": row[2],
            "modelo": row[3],
            "imei": str(row[4]).strip() if row[4] else None,
            "vel_max": row[5],
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
