from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from services.telemetry_service import now_local_naive, to_app_iso
from utils.db_cursor import main_cursor, telemetry_cursor

logger = logging.getLogger(__name__)

# Velocidad mínima para considerar que la unidad está en movimiento (km/h).
_MIN_MOVING_SPEED = 1.0

# Tope de delta de tiempo entre puntos consecutivos (s). Huecos largos no se suman al uso.
_MAX_DELTA_SEGS = 300

# Tope de distancia entre puntos consecutivos (km) para sumar al total de kilómetros.
_MAX_SEGMENTO_KM = 5.0

_PERIODOS_VALIDOS = {"hoy", "7d", "30d"}


def _resolver_rango(periodo: str) -> tuple[datetime, datetime, str]:
    """
    Devuelve (inicio, fin, bucket) para el periodo dado.
    - inicio y fin son naive datetime en hora local.
    """
    ahora = now_local_naive()
    hoy_medianoche = ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    if periodo == "hoy":
        return hoy_medianoche, ahora, "1 hour"
    if periodo == "7d":
        return hoy_medianoche - timedelta(days=6), ahora, "1 day"
    return hoy_medianoche - timedelta(days=29), ahora, "1 day"


def _get_unidades_empresa(id_empresa: int) -> list[dict[str, Any]]:
    """Unidades de la empresa con imei asignado (las que pueden reportar)."""
    with main_cursor() as cursor:
        cursor.execute(
            """
            SELECT id_unidad, imei, numero, marca, modelo, vel_max
            FROM t_unidades
            WHERE id_empresa = %s
              AND imei IS NOT NULL
              AND imei <> ''
            """,
            (id_empresa,),
        )
        return [
            {
                "id": row[0],
                "imei": row[1],
                "numero": row[2],
                "marca": row[3],
                "modelo": row[4],
                "vel_max": float(row[5]) if row[5] is not None else None,
            }
            for row in cursor.fetchall()
        ]


def _render_values_unidades(cursor, imeis_velmax: list[tuple]) -> str:
    """
    Renderiza la lista de tuplas (imei, vel_max) como VALUES SQL para el CTE
    de segmentos.
    """
    partes = [
        cursor.mogrify("(%s, %s::double precision)", tupla).decode()
        for tupla in imeis_velmax
    ]
    return ",".join(partes)


def _sql_segmentos_cte(values_sql: str) -> str:
    """
    Devuelve el CTE SQL que genera la tabla de segmentos (distancia y delta
    de tiempo entre puntos consecutivos) para las unidades y rango dados.
    """
    return f"""
    WITH puntos AS (
        SELECT
            d.imei,
            d.fecha_hora_gps,
            COALESCE(NULLIF(trim(d.velocidad::text), '')::double precision, 0) AS vel,
            left(d.status::text, 1) = '1' AS motor_on,
            d.latitud::double precision  AS lat,
            d.longitud::double precision AS lng,
            v.vel_max,
            lag(d.fecha_hora_gps) OVER w AS prev_fecha,
            lag(COALESCE(NULLIF(trim(d.velocidad::text), '')::double precision, 0)) OVER w AS prev_vel,
            lag(d.latitud::double precision)  OVER w AS prev_lat,
            lag(d.longitud::double precision) OVER w AS prev_lng
        FROM public.t_data d
        JOIN (VALUES {values_sql}) AS v(imei, vel_max) ON v.imei = d.imei
        WHERE d.fecha_hora_gps >= %s
          AND d.fecha_hora_gps <  %s
          AND d.latitud  IS NOT NULL
          AND d.longitud IS NOT NULL
          AND ((d.atributos::jsonb->>'FIX') IS DISTINCT FROM '0')
        WINDOW w AS (PARTITION BY d.imei ORDER BY d.fecha_hora_gps)
    ),
    segmentos AS (
        SELECT
            imei,
            fecha_hora_gps,
            vel,
            motor_on,
            vel_max,
            prev_vel,
            -- CASE explícito: LEAST(NULL, x) en Postgres devuelve x, no NULL,
            -- y el primer punto de cada unidad sumaría {_MAX_DELTA_SEGS}s fantasma.
            CASE
                WHEN prev_fecha IS NULL THEN 0
                ELSE LEAST(
                    EXTRACT(EPOCH FROM (fecha_hora_gps - prev_fecha)),
                    {_MAX_DELTA_SEGS}
                )
            END AS delta_segs,
            CASE
                WHEN prev_lat IS NULL THEN 0
                ELSE 2 * 6371 * asin(sqrt(
                    power(sin(radians(lat - prev_lat) / 2), 2) +
                    cos(radians(prev_lat)) * cos(radians(lat)) *
                    power(sin(radians(lng - prev_lng) / 2), 2)
                ))
            END AS dist_km
        FROM puntos
    )
    """


def get_dashboard_summary(id_empresa: int, periodo: str) -> dict[str, Any]:
    """
    Resumen completo del dashboard para una empresa y periodo.

    Returns:
        dict listo para jsonify — ver contrato en routes/dashboard_routes.py.
    """
    if periodo not in _PERIODOS_VALIDOS:
        raise ValueError(f"Periodo inválido: {periodo!r}")

    inicio, fin, bucket = _resolver_rango(periodo)
    unidades = _get_unidades_empresa(id_empresa)

    respuesta = {
        "periodo": periodo,
        "rango": {"inicio": to_app_iso(inicio), "fin": to_app_iso(fin)},
        "kilometros": {"total": 0.0, "unidades_con_km": 0},
        "uso": {"minutos_movimiento": 0, "minutos_ralenti": 0},
        "excesos": {"eventos": 0, "minutos": 0, "unidades": 0},
        "serie": [],
        "top_unidades": [],
    }

    if not unidades:
        return respuesta

    imeis_velmax = [(u["imei"], u["vel_max"]) for u in unidades]

    # Una sola conexión de telemetría para las dos consultas.
    with telemetry_cursor() as cursor:
        values_sql = _render_values_unidades(cursor, imeis_velmax)
        cte = _sql_segmentos_cte(values_sql)

        # Consulta 1: agregados por unidad
        cursor.execute(
            cte + f"""
            SELECT
                imei,
                COALESCE(SUM(dist_km) FILTER (WHERE dist_km <= {_MAX_SEGMENTO_KM}), 0),
                COALESCE(SUM(delta_segs) FILTER (
                    WHERE motor_on AND vel >= {_MIN_MOVING_SPEED}), 0),
                COALESCE(SUM(delta_segs) FILTER (
                    WHERE motor_on AND vel < {_MIN_MOVING_SPEED}), 0),
                COUNT(*) FILTER (
                    WHERE vel_max IS NOT NULL AND vel_max > 0
                      AND vel > vel_max
                      AND (prev_vel IS NULL OR prev_vel <= vel_max)),
                COALESCE(SUM(delta_segs) FILTER (
                    WHERE vel_max IS NOT NULL AND vel_max > 0
                      AND vel > vel_max), 0)
            FROM segmentos
            GROUP BY imei
            """,
            (inicio, fin),
        )
        por_imei = {
            row[0]: {
                "km": float(row[1]),
                "segs_movimiento": float(row[2]),
                "segs_ralenti": float(row[3]),
                "eventos_exceso": int(row[4]),
                "segs_exceso": float(row[5]),
            }
            for row in cursor.fetchall()
        }

        # Consulta 2: serie temporal para la gráfica
        cursor.execute(
            cte + f"""
            SELECT
                time_bucket('{bucket}', fecha_hora_gps),
                COALESCE(SUM(dist_km) FILTER (WHERE dist_km <= {_MAX_SEGMENTO_KM}), 0)
            FROM segmentos
            GROUP BY 1
            ORDER BY 1 ASC
            """,
            (inicio, fin),
        )
        serie = [
            {"bucket": to_app_iso(row[0]), "km": round(float(row[1]), 1)}
            for row in cursor.fetchall()
        ]

    return _ensamblar_respuesta(respuesta, unidades, por_imei, serie)


def _ensamblar_respuesta(base, unidades, por_imei, serie):
    """Combina catálogo de unidades (BD principal) con agregados (telemetría)."""
    total_km = 0.0
    total_mov = 0.0
    total_ral = 0.0
    total_eventos = 0
    total_segs_exceso = 0.0
    unidades_con_km = 0
    unidades_con_exceso = 0

    detalle = []
    for u in unidades:
        agg = por_imei.get(u["imei"])
        if not agg:
            continue
        km = agg["km"]
        total_km += km
        total_mov += agg["segs_movimiento"]
        total_ral += agg["segs_ralenti"]
        total_eventos += agg["eventos_exceso"]
        total_segs_exceso += agg["segs_exceso"]
        if km >= 0.1:
            unidades_con_km += 1
        if agg["eventos_exceso"] > 0:
            unidades_con_exceso += 1

        detalle.append(
            {
                "id": u["id"],
                "numero": u["numero"],
                "marca": u["marca"],
                "modelo": u["modelo"],
                "km": round(km, 1),
                "minutos_uso": round(
                    (agg["segs_movimiento"] + agg["segs_ralenti"]) / 60
                ),
                "excesos": agg["eventos_exceso"],
            }
        )

    # Top 5 por km — accionable: el frontend enlaza cada fila al mapa.
    detalle.sort(key=lambda d: d["km"], reverse=True)

    base["kilometros"] = {
        "total": round(total_km, 1),
        "unidades_con_km": unidades_con_km,
    }
    base["uso"] = {
        "minutos_movimiento": round(total_mov / 60),
        "minutos_ralenti": round(total_ral / 60),
    }
    base["excesos"] = {
        "eventos": total_eventos,
        "minutos": round(total_segs_exceso / 60),
        "unidades": unidades_con_exceso,
    }
    base["serie"] = serie
    base["top_unidades"] = detalle[:5]
    return base
