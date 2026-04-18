import hashlib
import logging
from datetime import datetime, timedelta, timezone
from db.connection import get_db_connection, release_db_connection
from config import Config

logger = logging.getLogger(__name__)


def _hash_token(token_crudo: str) -> str:
    """
    Calcula el SHA-256 del token crudo.

    Solo este hash se almacena en BD — nunca el token crudo.
    Si la BD se filtra, los hashes son inútiles sin el token original.
    """
    return hashlib.sha256(token_crudo.encode()).hexdigest()


def save_refresh_token(
    id_usuario: int,
    token_crudo: str,
    ip_origen: str = None,
    user_agent: str = None,
) -> bool:
    """
    Guarda el hash del refresh token en la tabla t_refresh_tokens.

    Solo almacena el hash SHA-256 — nunca el token crudo. Si la BD se filtra,
    los hashes son computacionalmente inviables de revertir.

    Args:
        id_usuario:  ID del usuario dueño del token.
        token_crudo: Token generado por generate_refresh_token() — se hashea aquí.
        ip_origen:   IP del cliente al momento del login (para auditoría).
        user_agent:  Navegador/dispositivo del cliente (para auditoría).

    Returns:
        True si se guardó correctamente, False si hubo un error.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        expires_at = datetime.now(timezone.utc) + timedelta(
            days=Config.REFRESH_TOKEN_EXPIRATION_DAYS
        )
        token_hash = _hash_token(token_crudo)

        cursor.execute(
            """
            INSERT INTO t_refresh_tokens
                (id_usuario, token_hash, expires_at, ip_origen, user_agent)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (id_usuario, token_hash, expires_at, ip_origen, user_agent),
        )
        connection.commit()
        logger.info(
            "Refresh token guardado para id_usuario=%s desde ip=%s",
            id_usuario,
            ip_origen,
        )
        return True

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error guardando refresh token id_usuario=%s: %s", id_usuario, repr(e)
        )
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def validate_and_rotate_refresh_token(token_crudo: str) -> dict | None:
    """
    Valida el refresh token y lo rota.

    La rotación garantiza que cada refresh token se usa exactamente una vez.
    Al validar:
      1. Se busca el hash en BD
      2. Si está revocado → posible robo → se revocan TODOS los tokens del usuario
      3. Si expiró → retorna None (sesión terminada)
      4. Si es válido → se revoca el token actual y se retorna el usuario

    El nuevo refresh token lo genera el caller (auth_routes.py) y lo guarda
    con save_refresh_token() — este servicio solo invalida el viejo.

    Args:
        token_crudo: Token recibido desde la cookie HttpOnly.

    Returns:
        Diccionario con id_usuario si el token es válido, None si no lo es.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        token_hash = _hash_token(token_crudo)

        cursor.execute(
            """
            SELECT id, id_usuario, expires_at, revoked
            FROM t_refresh_tokens
            WHERE token_hash = %s
            """,
            (token_hash,),
        )
        row = cursor.fetchone()

        # Token no encontrado en BD — podría ser antiguo o fabricado
        if not row:
            logger.warning("Refresh token no encontrado en BD — posible token inválido")
            return None

        token_id, id_usuario, expires_at, revoked = row

        # Token ya fue revocado — señal de posible robo (reuse detection).
        # Si un atacante roba un refresh token y lo usa después de que el usuario
        # legítimo ya lo renovó, llegará a este punto.
        # Respuesta defensiva: revocar TODOS los tokens del usuario.
        if revoked:
            logger.warning(
                "Refresh token revocado reutilizado — posible robo de token. "
                "Revocando todos los tokens del usuario id=%s",
                id_usuario,
            )
            cursor.execute(
                "UPDATE t_refresh_tokens SET revoked = TRUE WHERE id_usuario = %s",
                (id_usuario,),
            )
            connection.commit()
            return None

        # Token expirado — la sesión larga terminó, el usuario debe hacer login
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            # Normalizar a UTC si la BD retornó datetime sin tzinfo
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if now > expires_at:
            logger.info(
                "Refresh token expirado para id_usuario=%s (expiró: %s)",
                id_usuario,
                expires_at,
            )
            return None

        # Token válido — revocarlo (rotación: este token ya no puede usarse de nuevo)
        cursor.execute(
            "UPDATE t_refresh_tokens SET revoked = TRUE WHERE id = %s",
            (token_id,),
        )
        connection.commit()

        logger.info("Refresh token validado y rotado para id_usuario=%s", id_usuario)
        return {"id_usuario": id_usuario}

    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error validando refresh token: %s", repr(e))
        return None
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def revoke_refresh_token(token_crudo: str) -> bool:
    """
    Revoca un refresh token específico.

    Llamado en logout — invalida el token del dispositivo actual.
    El usuario seguirá con sesión activa en otros dispositivos.

    Args:
        token_crudo: Token recibido desde la cookie HttpOnly.

    Returns:
        True si se revocó, False si hubo error.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        token_hash = _hash_token(token_crudo)
        cursor.execute(
            "UPDATE t_refresh_tokens SET revoked = TRUE WHERE token_hash = %s",
            (token_hash,),
        )
        connection.commit()
        return True
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error revocando refresh token: %s", repr(e))
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def revoke_all_user_tokens(id_usuario: int) -> bool:
    """
    Revoca todos los refresh tokens de un usuario.

    Útil para:
      - Logout de todos los dispositivos (cambio de contraseña, cuenta comprometida)
      - Detección de robo de token (ver validate_and_rotate_refresh_token)

    Args:
        id_usuario: ID del usuario cuyos tokens se revocarán.

    Returns:
        True si se revocaron, False si hubo error.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE t_refresh_tokens SET revoked = TRUE WHERE id_usuario = %s",
            (id_usuario,),
        )
        connection.commit()
        logger.info("Todos los tokens revocados para id_usuario=%s", id_usuario)
        return True
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error("Error revocando tokens de id_usuario=%s: %s", id_usuario, repr(e))
        return False
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
