import logging
import secrets
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)

# Longitud del token de rastreo
_TOKEN_LEN = 15
# Longitud del token de dashboard
_DASHBOARD_TOKEN_LEN = 20


def _generate_token(length: int) -> str:
    """Genera un token url-safe corto. token_urlsafe(n) da ~1.3n chars."""
    return secrets.token_urlsafe(length)[:length]


# ─── Lectura de la configuración ──────────────────────────────────────────────


def get_client_token_config(id_cliente: int, id_empresa: int) -> dict | None:
    """
    Devuelve la configuración de token (rastreo + dashboard) de un cliente.

    Retorna None si el cliente no existe en la empresa. Si el cliente existe
    pero no tiene fila en las tablas de token/dashboard (caso raro tras la
    migración), devuelve los valores por defecto.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Verificar que el cliente pertenece a la empresa.
        cursor.execute(
            "SELECT 1 FROM t_clientes WHERE id_cliente = %s AND id_empresa = %s",
            (id_cliente, id_empresa),
        )
        if cursor.fetchone() is None:
            return None

        cursor.execute(
            """
            SELECT
                acceso_token_rastreo,
                token,
                early_access_token_rastreo,
                acceso_global,
                token_requiere_clave_acceso,
                token_clave_acceso,
                permite_acceso_clave_usuario,
                tipo_vista_token,
                tipo_icono_unidad,
                visualizar_info_paradas,
                tipo_itinerario_visible,
                ocultar_itinerarios_terminados,
                tipo_agrupacion_itinerarios,
                tipo_ordenamiento_itinerarios,
                identificacion_automatica_tipo_itinerario
            FROM t_clientes_token
            WHERE id_cliente = %s
            """,
            (id_cliente,),
        )
        row = cursor.fetchone()
        token_cfg = (
            {
                "acceso_token_rastreo": row[0],
                "token": row[1],
                "early_access_token_rastreo": row[2],
                "acceso_global": row[3],
                "token_requiere_clave_acceso": row[4],
                "token_clave_acceso": row[5],
                "permite_acceso_clave_usuario": row[6],
                "tipo_vista_token": row[7],
                "tipo_icono_unidad": row[8],
                "visualizar_info_paradas": row[9],
                "tipo_itinerario_visible": row[10],
                "ocultar_itinerarios_terminados": row[11],
                "tipo_agrupacion_itinerarios": row[12],
                "tipo_ordenamiento_itinerarios": row[13],
                "identificacion_automatica_tipo_itinerario": row[14],
            }
            if row
            else _default_token_cfg()
        )

        cursor.execute(
            """
            SELECT
                acceso_dashboard_cmp,
                token_dashboard,
                dashboard_requiere_clave_acceso,
                dashboard_clave_acceso,
                visualizar_estadistica_aforos,
                visualizar_graficas_generales
            FROM t_clientes_dashboard
            WHERE id_cliente = %s
            """,
            (id_cliente,),
        )
        drow = cursor.fetchone()
        dashboard_cfg = (
            {
                "acceso_dashboard_cmp": drow[0],
                "token_dashboard": drow[1],
                "dashboard_requiere_clave_acceso": drow[2],
                "dashboard_clave_acceso": drow[3],
                "visualizar_estadistica_aforos": drow[4],
                "visualizar_graficas_generales": drow[5],
            }
            if drow
            else _default_dashboard_cfg()
        )

        return {"token": token_cfg, "dashboard": dashboard_cfg}
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


def _default_token_cfg() -> dict:
    return {
        "acceso_token_rastreo": False,
        "token": None,
        "early_access_token_rastreo": False,
        "acceso_global": False,
        "token_requiere_clave_acceso": False,
        "token_clave_acceso": None,
        "permite_acceso_clave_usuario": False,
        "tipo_vista_token": 0,
        "tipo_icono_unidad": False,
        "visualizar_info_paradas": 0,
        "tipo_itinerario_visible": False,
        "ocultar_itinerarios_terminados": False,
        "tipo_agrupacion_itinerarios": False,
        "tipo_ordenamiento_itinerarios": False,
        "identificacion_automatica_tipo_itinerario": False,
    }


def _default_dashboard_cfg() -> dict:
    return {
        "acceso_dashboard_cmp": False,
        "token_dashboard": None,
        "dashboard_requiere_clave_acceso": False,
        "dashboard_clave_acceso": None,
        "visualizar_estadistica_aforos": False,
        "visualizar_graficas_generales": False,
    }


# ─── Generación / regeneración de token ──────────────────────────────────────


def _ensure_token_row(cursor, id_cliente: int, id_empresa: int):
    """Crea la fila en t_clientes_token si no existe (UPSERT idempotente)."""
    cursor.execute(
        """
        INSERT INTO t_clientes_token (id_cliente, id_empresa)
        VALUES (%s, %s)
        ON CONFLICT (id_cliente) DO NOTHING
        """,
        (id_cliente, id_empresa),
    )


def regenerate_tracking_token(id_cliente: int, id_empresa: int) -> dict | None:
    """
    Genera (o regenera) el token de rastreo del cliente y activa el acceso.

    Retorna {"token": <nuevo_token>} o None si el cliente no pertenece a la
    empresa. Reintenta ante colisión de token (índice único).
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT 1 FROM t_clientes WHERE id_cliente = %s AND id_empresa = %s",
            (id_cliente, id_empresa),
        )
        if cursor.fetchone() is None:
            return None

        _ensure_token_row(cursor, id_cliente, id_empresa)

        # Generar token único (reintenta si colisiona con el índice único).
        nuevo_token = None
        for _ in range(5):
            candidato = _generate_token(_TOKEN_LEN)
            cursor.execute(
                "SELECT 1 FROM t_clientes_token WHERE token = %s",
                (candidato,),
            )
            if cursor.fetchone() is None:
                nuevo_token = candidato
                break
        if nuevo_token is None:
            raise RuntimeError("No se pudo generar un token único")

        cursor.execute(
            """
            UPDATE t_clientes_token
               SET token = %s,
                   acceso_token_rastreo = TRUE
             WHERE id_cliente = %s
            """,
            (nuevo_token, id_cliente),
        )
        connection.commit()
        return {"token": nuevo_token}
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en regenerate_tracking_token id_cliente=%s: %s",
            id_cliente,
            repr(e),
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)


# ─── Actualización de la configuración ───────────────────────────────────────

# Campos editables de cada tabla (defensa en profundidad).
_TOKEN_FIELDS = frozenset(
    {
        "acceso_token_rastreo",
        "early_access_token_rastreo",
        "acceso_global",
        "token_requiere_clave_acceso",
        "token_clave_acceso",
        "permite_acceso_clave_usuario",
        "tipo_vista_token",
        "tipo_icono_unidad",
        "visualizar_info_paradas",
        "tipo_itinerario_visible",
        "ocultar_itinerarios_terminados",
        "tipo_agrupacion_itinerarios",
        "tipo_ordenamiento_itinerarios",
        "identificacion_automatica_tipo_itinerario",
    }
)

_DASHBOARD_FIELDS = frozenset(
    {
        "acceso_dashboard_cmp",
        "dashboard_requiere_clave_acceso",
        "dashboard_clave_acceso",
        "visualizar_estadistica_aforos",
        "visualizar_graficas_generales",
    }
)


def update_token_config(id_cliente: int, id_empresa: int, payload: dict) -> dict | None:
    """
    Actualiza las opciones de token y/o dashboard del cliente.

    El payload puede traer claves de token y de dashboard mezcladas; se separan
    y se aplican a su tabla. No toca el token en sí (eso es regenerate_*).
    Retorna la config actualizada, o None si el cliente no pertenece a la empresa.
    """
    connection = None
    cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            "SELECT 1 FROM t_clientes WHERE id_cliente = %s AND id_empresa = %s",
            (id_cliente, id_empresa),
        )
        if cursor.fetchone() is None:
            return None

        _ensure_token_row(cursor, id_cliente, id_empresa)
        cursor.execute(
            """
            INSERT INTO t_clientes_dashboard (id_cliente, id_empresa)
            VALUES (%s, %s)
            ON CONFLICT (id_cliente) DO NOTHING
            """,
            (id_cliente, id_empresa),
        )

        # Separar y aplicar campos de token.
        token_updates = {k: v for k, v in payload.items() if k in _TOKEN_FIELDS}
        if token_updates:
            sets = [f"{k} = %s" for k in token_updates]
            values = list(token_updates.values()) + [id_cliente]
            cursor.execute(
                f"UPDATE t_clientes_token SET {', '.join(sets)} WHERE id_cliente = %s",
                tuple(values),
            )

        # Separar y aplicar campos de dashboard.
        dash_updates = {k: v for k, v in payload.items() if k in _DASHBOARD_FIELDS}
        if dash_updates:
            sets = [f"{k} = %s" for k in dash_updates]
            values = list(dash_updates.values()) + [id_cliente]
            cursor.execute(
                f"UPDATE t_clientes_dashboard SET {', '.join(sets)} WHERE id_cliente = %s",
                tuple(values),
            )

        connection.commit()
        # Devolver la config actualizada reabriendo conexión vía el getter.
        return get_client_token_config(id_cliente, id_empresa)
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(
            "Error en update_token_config id_cliente=%s: %s", id_cliente, repr(e)
        )
        raise
    finally:
        if cursor:
            cursor.close()
        if connection:
            release_db_connection(connection)
