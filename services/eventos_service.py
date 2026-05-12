"""
Servicio para el historial de eventos de geocerca.

Lee de t_eventos en el servidor de telemetria (hypertable TimescaleDB)
y enriquece los resultados con nombres de unidades y POIs desde BD principal.

Por que dos queries separadas y no un JOIN:
  t_eventos vive en el servidor remoto de telemetria.
  t_unidades y t_pois viven en la BD principal local.
  PostgreSQL no permite JOINs entre servidores distintos — se resuelve
  en Python combinando los resultados de ambas queries.

Tipos de evento:
  10 = Entro al POI
  11 = Salio del POI
  12 = Permanencia maxima excedida
  13 = Permanencia minima no cumplida
  14 = Exceso de velocidad inicio
  15 = Exceso de velocidad fin
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from db.connection import (
    get_db_connection,
    release_db_connection,
    get_db_telemetry_connection,
    release_db_telemetry_connection,
)

# to_app_iso es el patron canonico del proyecto para serializar fechas de BD.
# Convierte datetime UTC naive → ISO 8601 con offset -06:00.
# Ej: "2026-05-06 16:33:49" → "2026-05-06T10:33:49-06:00"
# El frontend usa parseApiDate() que entiende este formato directamente.
# Sin este patron, .isoformat() emite "2026-05-06T16:33:49" (sin TZ) y
# el frontend asume UTC pero lo muestra sin convertir.
from services.telemetry_service import to_app_iso

logger = logging.getLogger(__name__)

# Tipos de evento que este modulo maneja
#   3/4   = velocidad global (sin POI — id_elemento NULL)
#   10-15 = geocerca (entrada, salida, permanencia, velocidad en POI)
#   19    = paso por geocerca (trayectoria cruza sin entrar)
TIPOS_EVENTO_GEOCERCA = (3, 4, 10, 11, 12, 13, 14, 15, 19)

DESCRIPCION_EVENTO = {
    3: "Inicio exceso de velocidad",
    4: "Fin exceso de velocidad",
    10: "Entro al POI",
    11: "Salio del POI",
    12: "Permanencia maxima excedida",
    13: "Permanencia minima no cumplida",
    14: "Exceso de velocidad en POI inicio",
    15: "Exceso de velocidad en POI fin",
    19: "Paso por geocerca",
}

# ── Queries SQL ───────────────────────────────────────────────────────────────

# Lee eventos de t_eventos con filtros dinamicos (BD telemetria).
# TimescaleDB usa fecha_hora_gmt para chunk exclusion — SIEMPRE filtrarlo
# con un rango de fechas para aprovechar la particion automatica.
_SQL_EVENTOS_BASE = """
    SELECT
        id_evento,
        id_unidad,
        id_elemento   AS id_poi,
        evento        AS tipo_evento,
        fecha_hora_gmt,
        payload,
        id_empresa
    FROM public.t_eventos
    WHERE id_empresa = %(id_empresa)s
      AND evento     = ANY(%(tipos)s)
      AND fecha_hora_gmt >= %(desde)s
      AND fecha_hora_gmt <= %(hasta)s
      {filtro_unidad}
      {filtro_poi}
    ORDER BY fecha_hora_gmt DESC
    LIMIT %(limite)s
    OFFSET %(offset)s
"""

_SQL_CONTEO_BASE = """
    SELECT COUNT(*) AS total
    FROM public.t_eventos
    WHERE id_empresa = %(id_empresa)s
      AND evento     = ANY(%(tipos)s)
      AND fecha_hora_gmt >= %(desde)s
      AND fecha_hora_gmt <= %(hasta)s
      {filtro_unidad}
      {filtro_poi}
"""

# Nombres de unidades para enriquecer los eventos (BD principal)
_SQL_NOMBRES_UNIDADES = """
    SELECT id_unidad, numero, marca, modelo
    FROM t_unidades
    WHERE id_empresa = %s AND status = 1
"""

# Nombres de POIs para enriquecer los eventos (BD principal)
_SQL_NOMBRES_POIS = """
    SELECT id_poi, nombre
    FROM t_pois
    WHERE id_empresa = %s AND status = 1
"""


def _build_query(filtros: dict) -> tuple[str, str, dict]:
    """
    Construye las queries de datos y conteo con filtros dinamicos.

    Returns:
        (sql_datos, sql_conteo, params)
    """
    filtro_unidad = ""
    filtro_poi = ""

    if filtros.get("id_unidad"):
        filtro_unidad = "AND id_unidad = %(id_unidad)s"
    if filtros.get("id_poi"):
        filtro_poi = "AND id_elemento = %(id_poi)s"

    # Tipos de evento: si el usuario filtra por tipo, usar ese;
    # si no, usar todos los tipos de geocerca
    tipos = filtros.get("tipos_evento") or list(TIPOS_EVENTO_GEOCERCA)

    params = {
        "id_empresa": filtros["id_empresa"],
        "tipos": tipos,
        "desde": filtros["desde"],
        "hasta": filtros["hasta"],
        "id_unidad": filtros.get("id_unidad"),
        "id_poi": filtros.get("id_poi"),
        "limite": filtros.get("limite", 50),
        "offset": filtros.get("offset", 0),
    }

    sql_datos = _SQL_EVENTOS_BASE.format(
        filtro_unidad=filtro_unidad,
        filtro_poi=filtro_poi,
    )
    sql_conteo = _SQL_CONTEO_BASE.format(
        filtro_unidad=filtro_unidad,
        filtro_poi=filtro_poi,
    )

    return sql_datos, sql_conteo, params


def get_eventos(filtros: dict) -> tuple[dict | None, dict | None]:
    """
    Retorna eventos de geocerca paginados con nombres de unidad y POI.

    Args:
        filtros: dict con los siguientes campos:
            id_empresa  (int, requerido)
            desde       (datetime, requerido)
            hasta       (datetime, requerido)
            id_unidad   (int, opcional)
            id_poi      (int, opcional)
            tipos_evento (list[int], opcional — default: todos)
            pagina      (int, opcional — default: 1)
            limite      (int, opcional — default: 50, max: 200)

    Returns:
        (resultado_dict, None) en exito
        (None, error_dict) en fallo
    """
    conn_main = conn_telem = None
    try:
        # Normalizar paginacion
        pagina = max(1, int(filtros.get("pagina", 1)))
        limite = min(200, max(1, int(filtros.get("limite", 50))))
        filtros["limite"] = limite
        filtros["offset"] = (pagina - 1) * limite

        conn_main = get_db_connection()
        conn_telem = get_db_telemetry_connection()

        cur_main = conn_main.cursor()
        cur_telem = conn_telem.cursor()

        # ── 1. Cargar nombres de unidades y POIs (BD principal) ───────────
        id_empresa = filtros["id_empresa"]

        cur_main.execute(_SQL_NOMBRES_UNIDADES, (id_empresa,))
        unidades_map = {
            row[0]: {"numero": row[1], "marca": row[2], "modelo": row[3]}
            for row in cur_main.fetchall()
        }

        cur_main.execute(_SQL_NOMBRES_POIS, (id_empresa,))
        pois_map = {row[0]: row[1] for row in cur_main.fetchall()}

        # ── 2. Consultar eventos en BD telemetria ─────────────────────────
        sql_datos, sql_conteo, params = _build_query(filtros)

        cur_telem.execute(sql_conteo, params)
        total = cur_telem.fetchone()[0]

        cur_telem.execute(sql_datos, params)
        cols = [d[0] for d in cur_telem.description]
        rows = cur_telem.fetchall()

        # ── 3. Enriquecer eventos con nombres ─────────────────────────────
        eventos = []
        for row in rows:
            ev = dict(zip(cols, row))

            # Agregar nombres desde los maps de BD principal.
            # id_poi puede ser NULL para eventos globales (ev. 3 y 4) —
            # en ese caso nombre_poi queda como None (se muestra como "Sin POI" en frontend).
            unidad_info = unidades_map.get(ev["id_unidad"], {})
            ev["numero_unidad"] = unidad_info.get("numero", f"Unidad {ev['id_unidad']}")
            ev["marca_unidad"] = unidad_info.get("marca")
            ev["nombre_poi"] = (
                pois_map.get(ev["id_poi"], f"POI {ev['id_poi']}")
                if ev["id_poi"] is not None
                else None
            )
            ev["descripcion"] = DESCRIPCION_EVENTO.get(
                ev["tipo_evento"], "Evento desconocido"
            )

            # Serializar fecha con offset de zona horaria (-06:00).
            # to_app_iso sigue el patron canonico del proyecto:
            # UTC naive → ISO 8601 con offset -06:00 para que el frontend
            # pueda mostrar la hora local sin ambiguedad.
            # .isoformat() sin timezone emitia "2026-05-06T16:33:49" y el
            # frontend no sabia si era UTC o local.
            if isinstance(ev.get("fecha_hora_gmt"), datetime):
                ev["fecha_hora_gmt"] = to_app_iso(ev["fecha_hora_gmt"])

            eventos.append(ev)

        total_paginas = max(1, -(-total // limite))  # ceil division

        return {
            "eventos": eventos,
            "total": total,
            "pagina": pagina,
            "limite": limite,
            "total_paginas": total_paginas,
            "tiene_mas": pagina < total_paginas,
        }, None

    except Exception as exc:
        logger.error("Error en get_eventos: %s", repr(exc), exc_info=True)
        if conn_telem:
            try:
                conn_telem.rollback()
            except Exception:
                pass
        return None, {"code": "DATABASE_ERROR", "message": "Error interno del servidor"}
    finally:
        if conn_main:
            release_db_connection(conn_main)
        if conn_telem:
            release_db_telemetry_connection(conn_telem)


def get_eventos_export(filtros: dict) -> tuple[list[dict] | None, dict | None]:
    """
    Retorna TODOS los eventos para exportacion (sin paginacion).
    Limite maximo de 5000 filas para proteger el servidor.

    Args:
        filtros: mismos que get_eventos pero sin pagina/limite.

    Returns:
        (lista_eventos, None) en exito
        (None, error_dict) en fallo
    """
    filtros["limite"] = 5000
    filtros["offset"] = 0
    resultado, error = get_eventos(filtros)
    if error:
        return None, error
    return resultado["eventos"], None
