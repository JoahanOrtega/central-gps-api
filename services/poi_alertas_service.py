"""
services/poi_alertas_service.py
================================================================================
Servicio para la configuracion de alertas de geocerca por POI.

Cada POI puede tener como maximo UNA configuracion de alertas (constraint
uq_t_alertas_poi_id_poi). Este servicio implementa el patron upsert:
  - Si el POI ya tiene alerta  -> UPDATE
  - Si no tiene               -> INSERT

Tipos de alertas soportadas:
  in_out      (0/1): notificar cuando una unidad entra o sale del POI
  permanencia (0/1): notificar si la unidad excede o no cumple tiempo minimo
    tipo_permanencia:    1=excede tiempo maximo, 2=no cumple tiempo minimo
    minutos_permanencia: umbral en minutos
  vel_max     (0/1): notificar si la unidad supera la velocidad dentro del POI
    vel_max_permitida:   velocidad limite en km/h
  alcance (1/2):
    1=solo aplica al grupo id_grupo_unidades
    2=aplica a todas las unidades de la empresa

Coherencia entre campos (espejada de los CHECK de la BD):
  - Si permanencia=1, tipo_permanencia y minutos_permanencia son requeridos.
  - Si vel_max=1, vel_max_permitida es requerida.
  - Si alcance=1, id_grupo_unidades es requerido.
"""

import logging
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


# ── Queries SQL ───────────────────────────────────────────────────────────────

_SQL_GET_ALERTA = """
    SELECT
        id_alerta_poi,
        id_poi,
        id_empresa,
        in_out,
        permanencia,
        tipo_permanencia,
        minutos_permanencia,
        vel_max,
        vel_max_permitida,
        alcance,
        id_grupo_unidades,
        status
    FROM public.t_alertas_poi
    WHERE id_poi = %s
      AND id_empresa = %s
"""

_SQL_INSERT_ALERTA = """
    INSERT INTO public.t_alertas_poi (
        id_poi,
        id_empresa,
        in_out,
        permanencia,
        tipo_permanencia,
        minutos_permanencia,
        vel_max,
        vel_max_permitida,
        alcance,
        id_grupo_unidades,
        status,
        id_usuario_registro
    ) VALUES (
        %(id_poi)s,
        %(id_empresa)s,
        %(in_out)s,
        %(permanencia)s,
        %(tipo_permanencia)s,
        %(minutos_permanencia)s,
        %(vel_max)s,
        %(vel_max_permitida)s,
        %(alcance)s,
        %(id_grupo_unidades)s,
        1,
        %(id_usuario)s
    )
    RETURNING id_alerta_poi
"""

_SQL_UPDATE_ALERTA = """
    UPDATE public.t_alertas_poi SET
        in_out              = %(in_out)s,
        permanencia         = %(permanencia)s,
        tipo_permanencia    = %(tipo_permanencia)s,
        minutos_permanencia = %(minutos_permanencia)s,
        vel_max             = %(vel_max)s,
        vel_max_permitida   = %(vel_max_permitida)s,
        alcance             = %(alcance)s,
        id_grupo_unidades   = %(id_grupo_unidades)s,
        status              = 1,
        id_usuario_cambio   = %(id_usuario)s
    WHERE id_poi    = %(id_poi)s
      AND id_empresa = %(id_empresa)s
    RETURNING id_alerta_poi
"""

_SQL_DESACTIVAR_ALERTA = """
    UPDATE public.t_alertas_poi
    SET status = 0, id_usuario_cambio = %s
    WHERE id_poi = %s AND id_empresa = %s
    RETURNING id_alerta_poi
"""

_SQL_VERIFICAR_POI = """
    SELECT id_poi FROM public.t_pois
    WHERE id_poi = %s AND id_empresa = %s AND status = 1
"""


def get_alerta_poi(id_poi: int, id_empresa: int) -> tuple[dict | None, dict | None]:
    """
    Retorna la configuracion de alerta de un POI.

    Si el POI no tiene alerta configurada, retorna un objeto con todos
    los campos en su valor default (alertas desactivadas) en lugar de
    None — el frontend siempre tiene un objeto con el que renderizar
    los toggles sin necesidad de verificar nulos.

    Returns:
        (alerta_dict, None) en exito
        (None, error_dict) si el POI no existe o no pertenece a la empresa
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verificar que el POI existe y pertenece a la empresa
        cur.execute(_SQL_VERIFICAR_POI, (id_poi, id_empresa))
        if not cur.fetchone():
            return None, {
                "code": "POI_NOT_FOUND",
                "message": "El POI no existe o no pertenece a tu empresa",
            }

        cur.execute(_SQL_GET_ALERTA, (id_poi, id_empresa))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()

        if row:
            return dict(zip(cols, row)), None

        # Sin alerta configurada — retornar objeto con defaults
        return {
            "id_alerta_poi": None,
            "id_poi": id_poi,
            "id_empresa": id_empresa,
            "in_out": 0,
            "permanencia": 0,
            "tipo_permanencia": None,
            "minutos_permanencia": None,
            "vel_max": 0,
            "vel_max_permitida": None,
            "alcance": 2,
            "id_grupo_unidades": None,
            "status": 0,
        }, None

    except Exception as exc:
        logger.error(
            "Error en get_alerta_poi id_poi=%s id_empresa=%s: %s",
            id_poi,
            id_empresa,
            repr(exc),
        )
        return None, {"code": "DATABASE_ERROR", "message": "Error interno del servidor"}
    finally:
        if conn:
            release_db_connection(conn)


def upsert_alerta_poi(
    id_poi: int,
    id_empresa: int,
    id_usuario: int,
    payload: dict,
) -> tuple[dict | None, dict | None]:
    """
    Crea o actualiza la configuracion de alerta de un POI.

    Implementa upsert manual:
      - Verifica si ya existe una alerta para el POI
      - Si existe -> UPDATE (incluyendo reactivar si estaba desactivada)
      - Si no     -> INSERT

    El worker lee t_alertas_poi en cada ciclo — el cambio tiene efecto
    en el proximo ciclo (maximo 15 segundos de latencia).

    Args:
        id_poi:     ID del POI a configurar.
        id_empresa: Empresa del usuario (para verificar propiedad).
        id_usuario: ID del usuario que hace el cambio (para auditoria).
        payload:    Campos de alerta validados por el schema.

    Returns:
        (alerta_dict, None) en exito
        (None, error_dict) en fallo
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Verificar que el POI pertenece a la empresa
        cur.execute(_SQL_VERIFICAR_POI, (id_poi, id_empresa))
        if not cur.fetchone():
            return None, {
                "code": "POI_NOT_FOUND",
                "message": "El POI no existe o no pertenece a tu empresa",
            }

        # Verificar si ya existe una alerta para este POI
        cur.execute(
            "SELECT id_alerta_poi FROM public.t_alertas_poi WHERE id_poi = %s AND id_empresa = %s",
            (id_poi, id_empresa),
        )
        existe = cur.fetchone()

        params = {
            "id_poi": id_poi,
            "id_empresa": id_empresa,
            "id_usuario": id_usuario,
            "in_out": payload.get("in_out", 0),
            "permanencia": payload.get("permanencia", 0),
            "tipo_permanencia": payload.get("tipo_permanencia"),
            "minutos_permanencia": payload.get("minutos_permanencia"),
            "vel_max": payload.get("vel_max", 0),
            "vel_max_permitida": payload.get("vel_max_permitida"),
            "alcance": payload.get("alcance", 2),
            "id_grupo_unidades": payload.get("id_grupo_unidades"),
        }

        # Limpiar campos dependientes cuando el toggle esta apagado
        # Evita que queden valores huerfanos en la BD
        if params["permanencia"] == 0:
            params["tipo_permanencia"] = None
            params["minutos_permanencia"] = None
        if params["vel_max"] == 0:
            params["vel_max_permitida"] = None
        if params["alcance"] == 2:
            params["id_grupo_unidades"] = None

        if existe:
            cur.execute(_SQL_UPDATE_ALERTA, params)
        else:
            cur.execute(_SQL_INSERT_ALERTA, params)

        row = cur.fetchone()
        id_alerta = row[0] if row else None
        conn.commit()

        # Retornar la alerta actualizada
        cur.execute(_SQL_GET_ALERTA, (id_poi, id_empresa))
        cols = [d[0] for d in cur.description]
        alerta = dict(zip(cols, cur.fetchone()))
        return alerta, None

    except Exception as exc:
        if conn:
            conn.rollback()
        logger.error(
            "Error en upsert_alerta_poi id_poi=%s id_empresa=%s: %s",
            id_poi,
            id_empresa,
            repr(exc),
        )
        return None, {"code": "DATABASE_ERROR", "message": "Error interno del servidor"}
    finally:
        if conn:
            release_db_connection(conn)


def desactivar_alerta_poi(
    id_poi: int,
    id_empresa: int,
    id_usuario: int,
) -> tuple[dict | None, dict | None]:
    """
    Desactiva (status=0) la alerta de un POI sin eliminarla.

    El worker filtra por status=1 — la alerta queda en BD para historial
    pero deja de generar eventos inmediatamente en el proximo ciclo.

    Returns:
        ({"desactivada": True}, None) en exito
        (None, error_dict) si no existe alerta activa para el POI
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute(_SQL_DESACTIVAR_ALERTA, (id_usuario, id_poi, id_empresa))
        row = cur.fetchone()

        if not row:
            return None, {
                "code": "ALERTA_NOT_FOUND",
                "message": "No existe una alerta activa para este POI",
            }

        conn.commit()
        return {"desactivada": True, "id_alerta_poi": row[0]}, None

    except Exception as exc:
        if conn:
            conn.rollback()
        logger.error(
            "Error en desactivar_alerta_poi id_poi=%s id_empresa=%s: %s",
            id_poi,
            id_empresa,
            repr(exc),
        )
        return None, {"code": "DATABASE_ERROR", "message": "Error interno del servidor"}
    finally:
        if conn:
            release_db_connection(conn)
