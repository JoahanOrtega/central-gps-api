"""
operator_assignment_service.py — Asignación exclusiva operador↔unidad.

La relación es 1:1 (un operador maneja una unidad, una unidad tiene un
operador). La lógica de exclusividad vive en el procedure de Postgres
asignar_operador_unidad (migración 018), que desasigna vínculos previos de
ambos lados antes de crear el nuevo y sincroniza id_unidad_operador en
t_operadores, t_unidades y r_unidad_operador.

NOTA: este es el único service que delega lógica a un stored procedure; el
resto del sistema la tiene en Python. Decisión explícita para portar fiel la
semántica del v3.0.
"""

import logging
from datetime import date
from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


def assign_operator_to_unit(
    id_operador: int,
    id_unidad: int,
    id_usuario: int,
    fecha_asignacion: str | None = None,
) -> bool:
    """
    Asigna un operador a una unidad de forma exclusiva.

    Llama al procedure asignar_operador_unidad, que se encarga de romper
    vínculos previos de ambos lados. Pasar id_unidad=0 o id_operador=0
    efectivamente desasigna (el procedure lo interpreta como "sin vínculo").

    Args:
        id_operador: ID del operador (0 = ninguno).
        id_unidad: ID de la unidad (0 = ninguna).
        id_usuario: ID del usuario que realiza el cambio (auditoría).
        fecha_asignacion: fecha 'YYYY-MM-DD'; si no viene, usa hoy.

    Returns:
        True si la operación se ejecutó sin error.
    """
    fecha = fecha_asignacion or date.today().isoformat()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CALL asignar_operador_unidad(%s, %s, %s, %s)",
                (id_unidad, id_operador, id_usuario, fecha),
            )
            conn.commit()
            return True
    except Exception:
        conn.rollback()
        logger.error(
            "Error en assign_operator_to_unit operador=%s unidad=%s",
            id_operador,
            id_unidad,
            exc_info=True,
        )
        raise
    finally:
        release_db_connection(conn)


def unassign_operator(id_operador: int, id_unidad: int, id_usuario: int) -> bool:
    """
    Rompe el vínculo de un operador y/o una unidad.

    Llama al procedure desasignar_unidad_operador. Cualquiera de los dos IDs
    puede ser 0 para desasignar solo por un lado.

    Returns:
        True si la operación se ejecutó sin error.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CALL desasignar_unidad_operador(%s, %s, %s)",
                (id_unidad, id_operador, id_usuario),
            )
            conn.commit()
            return True
    except Exception:
        conn.rollback()
        logger.error(
            "Error en unassign_operator operador=%s unidad=%s",
            id_operador,
            id_unidad,
            exc_info=True,
        )
        raise
    finally:
        release_db_connection(conn)
