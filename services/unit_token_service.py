import logging
import secrets
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)

# Longitud del token de rastreo. Igual que el de cliente (ver
# client_token_service.py) para mantener consistencia entre ambos enlaces.
_TOKEN_LEN = 15


def _generate_token(length: int) -> str:
    """Genera un token url-safe corto. token_urlsafe(n) da ~1.3n chars."""
    return secrets.token_urlsafe(length)[:length]


# ─── Lectura de la configuración ──────────────────────────────────────────────


def get_unit_token_config(id_unidad: int, id_empresa: int) -> dict | None:
    """
    Devuelve la configuración de token de rastreo de una unidad.

    Retorna None si la unidad no existe en la empresa. Si la unidad existe
    pero aún no tiene fila en t_unidades_token, devuelve los valores por
    defecto (sin token generado).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Verificar que la unidad pertenece a la empresa antes de exponer nada.
        cursor.execute(
            "SELECT 1 FROM t_unidades WHERE id_unidad = %s AND id_empresa = %s",
            (id_unidad, id_empresa),
        )
        if cursor.fetchone() is None:
            return None

        cursor.execute(
            """
            SELECT
                acceso_token_rastreo,
                token,
                token_requiere_clave_acceso,
                token_clave_acceso,
                fecha_expiracion
            FROM t_unidades_token
            WHERE id_unidad = %s
            """,
            (id_unidad,),
        )
        row = cursor.fetchone()
        return (
            {
                "acceso_token_rastreo": row[0],
                "token": row[1],
                "token_requiere_clave_acceso": row[2],
                "token_clave_acceso": row[3],
                "fecha_expiracion": row[4].isoformat() if row[4] else None,
            }
            if row
            else _default_token_cfg()
        )
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def _default_token_cfg() -> dict:
    """Configuración por defecto cuando la unidad no tiene fila de token aún."""
    return {
        "acceso_token_rastreo": False,
        "token": None,
        "token_requiere_clave_acceso": False,
        "token_clave_acceso": None,
        "fecha_expiracion": None,
    }


# ─── Generación / regeneración de token ──────────────────────────────────────


def _ensure_token_row(cursor, id_unidad: int, id_empresa: int):
    """Crea la fila en t_unidades_token si no existe (UPSERT idempotente)."""
    cursor.execute(
        """
        INSERT INTO t_unidades_token (id_unidad, id_empresa)
        VALUES (%s, %s)
        ON CONFLICT (id_unidad) DO NOTHING
        """,
        (id_unidad, id_empresa),
    )


def regenerate_tracking_token(id_unidad: int, id_empresa: int) -> dict | None:
    """
    Genera (o regenera) el token de rastreo de la unidad y activa el acceso.

    Retorna {"token": <nuevo_token>} o None si la unidad no pertenece a la
    empresa. Reintenta ante colisión de token (índice único parcial).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT 1 FROM t_unidades WHERE id_unidad = %s AND id_empresa = %s",
            (id_unidad, id_empresa),
        )
        if cursor.fetchone() is None:
            return None

        _ensure_token_row(cursor, id_unidad, id_empresa)

        # Generar token único (reintenta si colisiona con el índice único).
        nuevo_token = None
        for _ in range(5):
            candidato = _generate_token(_TOKEN_LEN)
            cursor.execute(
                "SELECT 1 FROM t_unidades_token WHERE token = %s",
                (candidato,),
            )
            if cursor.fetchone() is None:
                nuevo_token = candidato
                break
        if nuevo_token is None:
            raise RuntimeError("No se pudo generar un token único")

        # Token permanente por ahora (fecha_expiracion queda NULL). La
        # actualizacion de fecha_actualizacion deja rastro de la regeneración.
        cursor.execute(
            """
            UPDATE t_unidades_token
               SET token = %s,
                   acceso_token_rastreo = TRUE,
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
        logger.error(
            "Error en regenerate_tracking_token id_unidad=%s: %s",
            id_unidad,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def revoke_tracking_token(id_unidad: int, id_empresa: int) -> bool:
    """
    Revoca el token de rastreo: borra el token y desactiva el acceso.

    El enlace público deja de funcionar de inmediato. Retorna True si se
    revocó, False si la unidad no pertenece a la empresa.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT 1 FROM t_unidades WHERE id_unidad = %s AND id_empresa = %s",
            (id_unidad, id_empresa),
        )
        if cursor.fetchone() is None:
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
        logger.error(
            "Error en revoke_tracking_token id_unidad=%s: %s",
            id_unidad,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── Resolución pública del token (SIN autenticación) ────────────────────────


def get_unit_by_token(token: str) -> dict | None:
    """
    Resuelve un token de rastreo a los datos públicos de la unidad.

    Usada por el endpoint público SIN JWT: el token es la credencial. Solo
    expone lo mínimo para pintar el rastreo (nunca datos sensibles como chip,
    operador o aseguradora).

    Retorna None si el token no existe, el acceso está desactivado, o el token
    expiró. La query filtra por acceso_token_rastreo = TRUE para que revocar el
    acceso invalide el enlace al instante.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT
                u.id_unidad,
                u.numero,
                u.marca,
                u.modelo,
                u.imei,
                t.fecha_expiracion
            FROM t_unidades_token t
            JOIN t_unidades u ON u.id_unidad = t.id_unidad
            WHERE t.token = %s
              AND t.acceso_token_rastreo = TRUE
            """,
            (token,),
        )
        row = cursor.fetchone()
        if row is None:
            return None

        # Si el token tiene fecha de expiración y ya pasó, se considera
        # inválido. NULL = permanente (no expira). La comparación se hace en
        # Python contra la hora UTC-6 para no depender de otra query.
        fecha_expiracion = row[5]
        if fecha_expiracion is not None:
            cursor.execute("SELECT NOW() AT TIME ZONE 'America/Mexico_City'")
            ahora = cursor.fetchone()[0]
            if fecha_expiracion < ahora:
                return None

        return {
            "id_unidad": row[0],
            "numero": row[1],
            "marca": row[2],
            "modelo": row[3],
            "imei": str(row[4]).strip() if row[4] else None,
        }
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)