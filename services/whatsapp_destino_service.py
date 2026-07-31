import logging

from db.connection import get_db_connection, release_db_connection
from services.whatsapp_service import crear_grupo

logger = logging.getLogger(__name__)

TIPOS_VALIDOS = ("grupo", "persona")

_COLS = "id_destino_whatsapp, id_empresa, nombre, tipo, chatid, telefono, status"


def _fila_a_dict(row, cols) -> dict:
    return dict(zip(cols, row))


def listar_destinos(
    id_empresa: int | None = None, tipo: str | None = None
) -> list[dict]:
    """Lista destinos. Sin id_empresa (vista sudo) trae todos."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        condiciones, params = [], {}
        if id_empresa is not None:
            condiciones.append("id_empresa = %(id_empresa)s")
            params["id_empresa"] = id_empresa
        if tipo is not None:
            condiciones.append("tipo = %(tipo)s")
            params["tipo"] = tipo

        where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
        cur.execute(
            f"SELECT {_COLS} FROM public.t_destinos_whatsapp {where} "
            "ORDER BY id_empresa, tipo, nombre",
            params,
        )
        cols = [d[0] for d in cur.description]
        return [_fila_a_dict(r, cols) for r in cur.fetchall()]
    finally:
        if conn:
            release_db_connection(conn)


def crear_destino(
    id_empresa: int,
    tipo: str,
    nombre: str,
    telefono: str | None = None,
    participantes: list[str] | None = None,
) -> dict:
    """
    Alta de destino.
      persona → requiere telefono; el chatid se construye aquí.
      grupo   → requiere participantes; el grupo se crea en WhatsApp PRIMERO
                (Evolution) y solo si eso funciona se inserta en BD.
    Raises:
        ValueError: datos inválidos o fallo al crear el grupo en WhatsApp.
    """
    if tipo not in TIPOS_VALIDOS:
        raise ValueError(f"tipo inválido: {tipo}")

    if tipo == "persona":
        if not telefono:
            raise ValueError("un destino persona requiere teléfono")
        chatid = f"{telefono.strip().lstrip('+')}@s.whatsapp.net"
    else:
        if not participantes:
            raise ValueError("un grupo requiere al menos un participante")
        # Crear el grupo en WhatsApp ANTES de tocar la BD: si Evolution falla,
        # no queda basura. El emisor entra como admin automáticamente.
        chatid, detalle = crear_grupo(nombre, participantes)
        if not chatid:
            raise ValueError(f"No se pudo crear el grupo en WhatsApp: {detalle}")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            f"""
            INSERT INTO public.t_destinos_whatsapp
                (id_empresa, nombre, tipo, chatid, telefono, status)
            VALUES (%(id_empresa)s, %(nombre)s, %(tipo)s, %(chatid)s, %(telefono)s, 1)
            RETURNING {_COLS}
            """,
            {
                "id_empresa": id_empresa,
                "nombre": nombre,
                "tipo": tipo,
                "chatid": chatid,
                "telefono": telefono,
            },
        )
        cols = [d[0] for d in cur.description]
        fila = _fila_a_dict(cur.fetchone(), cols)
        conn.commit()
        return fila
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            release_db_connection(conn)


def editar_destino(
    id_destino: int, id_empresa: int, nombre: str, telefono: str | None = None
) -> dict | None:
    """
    Edita nombre (y teléfono si es persona — recalcula el chatid).
    Returns el destino actualizado, o None si no existe.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if telefono:
            chatid = f"{telefono.strip().lstrip('+')}@s.whatsapp.net"
            cur.execute(
                f"""
                UPDATE public.t_destinos_whatsapp
                   SET nombre = %(nombre)s, telefono = %(telefono)s, chatid = %(chatid)s
                 WHERE id_destino_whatsapp = %(id)s AND id_empresa = %(emp)s
                   AND tipo = 'persona'
                RETURNING {_COLS}
                """,
                {
                    "nombre": nombre,
                    "telefono": telefono,
                    "chatid": chatid,
                    "id": id_destino,
                    "emp": id_empresa,
                },
            )
        else:
            cur.execute(
                f"""
                UPDATE public.t_destinos_whatsapp
                   SET nombre = %(nombre)s
                 WHERE id_destino_whatsapp = %(id)s AND id_empresa = %(emp)s
                RETURNING {_COLS}
                """,
                {"nombre": nombre, "id": id_destino, "emp": id_empresa},
            )

        fila = cur.fetchone()
        if not fila:
            conn.rollback()
            return None
        cols = [d[0] for d in cur.description]
        conn.commit()
        return _fila_a_dict(fila, cols)
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            release_db_connection(conn)


def cambiar_status_destino(id_destino: int, id_empresa: int, status: int) -> bool:
    """Baja lógica: activo (1) / inactivo (0). Conserva historial de la cola."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE public.t_destinos_whatsapp
               SET status = %(status)s
             WHERE id_destino_whatsapp = %(id)s AND id_empresa = %(emp)s
            """,
            {"status": status, "id": id_destino, "emp": id_empresa},
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            release_db_connection(conn)


def eliminar_destino(id_destino: int, id_empresa: int) -> bool:
    """
    Borrado real. OJO: la FK de t_alertas_whatsapp es ON DELETE CASCADE, así
    que borrar el destino borra también su historial de alertas.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM public.t_destinos_whatsapp
             WHERE id_destino_whatsapp = %(id)s AND id_empresa = %(emp)s
            """,
            {"id": id_destino, "emp": id_empresa},
        )
        ok = cur.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            release_db_connection(conn)
