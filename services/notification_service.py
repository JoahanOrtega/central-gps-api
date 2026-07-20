"""
notification_service.py — Notificaciones persistentes por usuario.

La memoria de las alertas del sistema: el WS (unit_state_event) avisa en
vivo a quien está conectado; esta capa persiste en t_notificaciones_usuario
para que la campanita muestre lo que pasó mientras el usuario no estaba —
el patrón que todo usuario conoce de Gmail/Slack.

Destinatarios de un evento de empresa: los usuarios activos de esa empresa
más los sudo_erp (que operan todas). Fan-out-on-write: una fila por
destinatario — ver nota de escalabilidad en la migración 029.
"""

from __future__ import annotations

import logging
from typing import Any

from utils.db_cursor import main_cursor

logger = logging.getLogger(__name__)

# Rol con visibilidad global (sudo_erp). Recibe copia de las alertas de
# todas las empresas, etiquetada con la empresa del evento para que su
# campanita filtre por la empresa activa.
_ROL_SUDO = 1

# Retención: las notificaciones mayores a esto se purgan (la campanita es
# memoria reciente, no bitácora — para historial profundo están los eventos).
_RETENCION_DIAS = 90


def crear_para_empresa(
    id_empresa: int,
    tipo: int,
    titulo: str,
    mensaje: str | None = None,
    id_unidad: int | None = None,
) -> int:
    """
    Persiste una notificación para todos los destinatarios de la empresa.

    Un solo INSERT..SELECT resuelve destinatarios y fan-out en un viaje:
    usuarios activos de la empresa + sudos activos.

    Returns:
        Número de filas creadas (destinatarios alcanzados).
    """
    with main_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO t_notificaciones_usuario
                (id_usuario, id_empresa, tipo, titulo, mensaje, id_unidad)
            SELECT u.id, %(id_empresa)s, %(tipo)s, %(titulo)s,
                   %(mensaje)s, %(id_unidad)s
            FROM t_usuarios u
            WHERE u.status = 1
              AND (u.id_empresa = %(id_empresa)s OR u.id_rol = %(rol_sudo)s)
            """,
            {
                "id_empresa": id_empresa,
                "tipo": tipo,
                "titulo": titulo,
                "mensaje": mensaje,
                "id_unidad": id_unidad,
                "rol_sudo": _ROL_SUDO,
            },
        )
        creadas = cursor.rowcount
        cursor.connection.commit()

    logger.info(
        "Notificación tipo=%s persistida — empresa=%s destinatarios=%s",
        tipo,
        id_empresa,
        creadas,
    )
    return creadas


def listar(
    id_usuario: int,
    id_empresa: int,
    limit: int = 20,
    offset: int = 0,
    solo_no_leidas: bool = False,
) -> dict[str, Any]:
    """
    Últimas notificaciones del usuario en la empresa + contador de no leídas.

    Una sola ida a BD: la lista y el contador comparten conexión. El
    contador viaja siempre (aunque se pida solo_no_leidas) porque el badge
    de la campanita lo necesita en cada apertura.
    """
    with main_cursor() as cursor:
        filtro_leidas = "AND leida = false" if solo_no_leidas else ""
        cursor.execute(
            f"""
            SELECT id_notificacion, tipo, titulo, mensaje, id_unidad,
                   leida, fecha_registro
            FROM t_notificaciones_usuario
            WHERE id_usuario = %s AND id_empresa = %s {filtro_leidas}
            ORDER BY fecha_registro DESC
            LIMIT %s OFFSET %s
            """,
            (id_usuario, id_empresa, limit, offset),
        )
        items = [
            {
                "id": row[0],
                "tipo": row[1],
                "titulo": row[2],
                "mensaje": row[3],
                "id_unidad": row[4],
                "leida": row[5],
                "fecha": row[6].isoformat() if row[6] else None,
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT count(*)
            FROM t_notificaciones_usuario
            WHERE id_usuario = %s AND id_empresa = %s AND leida = false
            """,
            (id_usuario, id_empresa),
        )
        no_leidas = cursor.fetchone()[0]

    return {"items": items, "no_leidas": no_leidas}


def marcar_leidas(
    id_usuario: int,
    id_empresa: int,
    ids: list[int] | None = None,
) -> int:
    """
    Marca como leídas las notificaciones indicadas, o TODAS las de la
    empresa si ids es None — el "Marcar todas como leídas" de la campanita.

    El WHERE por id_usuario es la garantía de seguridad: nadie puede marcar
    (ni enumerar) notificaciones ajenas aunque adivine ids.
    """
    with main_cursor() as cursor:
        if ids:
            cursor.execute(
                """
                UPDATE t_notificaciones_usuario
                SET leida = true,
                    fecha_leida = now() AT TIME ZONE 'America/Mexico_City'
                WHERE id_usuario = %s AND id_empresa = %s
                  AND id_notificacion = ANY(%s) AND leida = false
                """,
                (id_usuario, id_empresa, ids),
            )
        else:
            cursor.execute(
                """
                UPDATE t_notificaciones_usuario
                SET leida = true,
                    fecha_leida = now() AT TIME ZONE 'America/Mexico_City'
                WHERE id_usuario = %s AND id_empresa = %s AND leida = false
                """,
                (id_usuario, id_empresa),
            )
        actualizadas = cursor.rowcount
        cursor.connection.commit()

    return actualizadas


def limpiar_antiguas() -> int:
    """
    Purga notificaciones más viejas que la retención. Pensada para
    ejecutarse una vez al día desde el ciclo del worker (idempotente y
    barata: el índice por fecha acota el barrido).
    """
    with main_cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM t_notificaciones_usuario
            WHERE fecha_registro <
                  (now() AT TIME ZONE 'America/Mexico_City')
                  - make_interval(days => %s)
            """,
            (_RETENCION_DIAS,),
        )
        purgadas = cursor.rowcount
        cursor.connection.commit()

    if purgadas:
        logger.info("Notificaciones purgadas por retención: %s", purgadas)
    return purgadas
