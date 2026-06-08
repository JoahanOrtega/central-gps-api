import logging
import secrets

from db.connection import get_db_connection, release_db_connection

logger = logging.getLogger(__name__)


# Helpers internos


def _row_to_dict(cur, row: tuple) -> dict:
    """Convierte una tupla de BD al dict correspondiente usando cur.description."""
    return dict(zip([d[0] for d in cur.description], row))


def _serialize_itinerary(record: dict) -> dict:
    """
    Normaliza los tipos de un registro de t_itinerarios para la respuesta JSON.

    - dias: el array SMALLINT[] de PG llega como lista Python → ya está bien.
    - horas: TIME de PG llega como datetime.time → convertir a string "HH:MM".
    - decimales: NUMERIC llega como Decimal → float.
    - fechas: DATE llega como datetime.date → ISO string.
    """
    # Horas: TIME → "HH:MM"
    for campo_hora in ("hora_inicio", "hora_fin"):
        v = record.get(campo_hora)
        if v is not None and hasattr(v, "strftime"):
            record[campo_hora] = v.strftime("%H:%M")

    # Fecha inicio: DATE → "YYYY-MM-DD"
    if record.get("fecha_inicio") and hasattr(record["fecha_inicio"], "isoformat"):
        record["fecha_inicio"] = record["fecha_inicio"].isoformat()

    # dias: ya viene como lista Python desde psycopg2 con SMALLINT[] — OK
    # Si por alguna razón llegara como None, normalizar a lista vacía
    if record.get("dias") is None:
        record["dias"] = []

    return record


def _serialize_parada(record: dict) -> dict:
    """Normaliza los tipos de un registro de r_itinerario_paradas."""
    if record.get("hora_abordaje") and hasattr(record["hora_abordaje"], "strftime"):
        record["hora_abordaje"] = record["hora_abordaje"].strftime("%H:%M")
    return record


# Listado agrupado por ruta

# Columnas base del itinerario para los dos modos de listado.
# Se reutiliza en ambas queries para garantizar consistencia.
_COLS_ITINERARIO = """
    i.id_itinerario,
    i.id_ruta,
    i.id_logistica_ruta,
    i.turno,
    i.tipo,
    i.dias,
    i.hora_inicio,
    i.hora_fin,
    i.minutos_tolerancia_inicio,
    i.minutos_tolerancia_fin,
    i.minutos_tolerancia_anticipacion,
    i.total_paradas,
    i.fecha_inicio,
    i.token,
    i.status
"""

_SQL_GROUPED = """
    SELECT
        -- Datos de la ruta (para la cabecera del grupo)
        r.id_ruta,
        r.clave      AS clave_ruta,
        r.nombre     AS nombre_ruta,
        r.tipo       AS tipo_ruta,
        c.nombre     AS cliente,

        -- Datos del itinerario
        {cols_itinerario},

        -- Logística: dirección de inicio/fin para mostrar en el listado
        l.direccion_inicio,
        l.direccion_fin,
        l.tipo_logistica,
        l.trace_color,

        -- Duración calculada (útil para mostrar "1h 30min" sin computar en el cliente)
        CASE
            WHEN i.hora_inicio IS NOT NULL AND i.hora_fin IS NOT NULL
            THEN EXTRACT(EPOCH FROM (
                CASE
                    -- Turno nocturno: cruza medianoche
                    WHEN i.hora_fin < i.hora_inicio
                    THEN (i.hora_fin + INTERVAL '24 hours') - i.hora_inicio
                    ELSE i.hora_fin - i.hora_inicio
                END
            ))::INTEGER
            ELSE NULL
        END AS duracion_segundos

    FROM t_itinerarios i
    INNER JOIN t_rutas r              ON r.id_ruta = i.id_ruta
    INNER JOIN t_logisticas_ruta l    ON l.id_logistica_ruta = i.id_logistica_ruta
    LEFT  JOIN t_clientes c           ON c.id_cliente = r.id_cliente
    WHERE i.id_empresa = %s
      AND i.status = 1
      AND r.status  = 1
      {search_clause}
    ORDER BY
        r.nombre,
        r.id_ruta,
        l.tipo_logistica,  -- ida antes que vuelta
        i.hora_inicio,
        i.turno
"""


def get_itineraries_grouped(
    id_empresa: int,
    search: str = "",
    id_ruta: int | None = None,
) -> list[dict]:
    """
    Retorna los itinerarios agrupados por ruta para el catálogo visual.

    La respuesta es una lista de grupos:
    [
      {
        "id_ruta": 1,
        "clave_ruta": "R01",
        "nombre_ruta": "Ruta Centro",
        "cliente": "Empresa X",
        "itinerarios": [
          { id_itinerario, turno, tipo, dias, hora_inicio, hora_fin, ... },
          ...
        ]
      },
      ...
    ]

    Args:
        id_empresa: Empresa del usuario autenticado.
        search:     Búsqueda libre en nombre de ruta o clave.
        id_ruta:    Si se pasa, filtra solo los itinerarios de esa ruta.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Construir cláusulas de filtro opcionales
            params = [id_empresa]
            clauses = []

            if search:
                clauses.append("AND (r.nombre ILIKE %s OR r.clave ILIKE %s)")
                like = f"%{search}%"
                params += [like, like]

            if id_ruta:
                clauses.append("AND i.id_ruta = %s")
                params.append(id_ruta)

            search_clause = " ".join(clauses)

            sql = _SQL_GROUPED.format(
                cols_itinerario=_COLS_ITINERARIO,
                search_clause=search_clause,
            )
            cur.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return []

        # Agrupar por ruta en Python — la query ya viene ordenada por ruta
        grupos: dict[int, dict] = {}
        for row in rows:
            record = _row_to_dict(cur, row)

            id_ruta_key = record["id_ruta"]

            # Crear el grupo si es la primera vez que vemos esta ruta
            if id_ruta_key not in grupos:
                grupos[id_ruta_key] = {
                    "id_ruta": record["id_ruta"],
                    "clave_ruta": record["clave_ruta"],
                    "nombre_ruta": record["nombre_ruta"],
                    "tipo_ruta": record["tipo_ruta"],
                    "cliente": record["cliente"],
                    "itinerarios": [],
                }

            # Campos exclusivos del itinerario (excluir los de la ruta)
            itinerario = {
                k: v
                for k, v in record.items()
                if k not in ("clave_ruta", "nombre_ruta", "tipo_ruta", "cliente")
            }
            grupos[id_ruta_key]["itinerarios"].append(_serialize_itinerary(itinerario))

        return list(grupos.values())

    finally:
        release_db_connection(conn)


# Listado plano paginado

# Tamaño de página por defecto y máximo permitido.
PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100

_SQL_PAGED = """
    SELECT
        {cols_itinerario},
        r.clave      AS clave_ruta,
        r.nombre     AS nombre_ruta,
        l.tipo_logistica,
        l.trace_color,
        l.direccion_inicio,
        l.direccion_fin,
        CASE
            WHEN i.hora_inicio IS NOT NULL AND i.hora_fin IS NOT NULL
            THEN EXTRACT(EPOCH FROM (
                CASE
                    WHEN i.hora_fin < i.hora_inicio
                    THEN (i.hora_fin + INTERVAL '24 hours') - i.hora_inicio
                    ELSE i.hora_fin - i.hora_inicio
                END
            ))::INTEGER
            ELSE NULL
        END AS duracion_segundos,
        -- Total de filas sin paginar (para que el cliente calcule total de páginas)
        COUNT(*) OVER () AS total_count
    FROM t_itinerarios i
    INNER JOIN t_rutas r           ON r.id_ruta = i.id_ruta
    INNER JOIN t_logisticas_ruta l ON l.id_logistica_ruta = i.id_logistica_ruta
    WHERE i.id_empresa = %s
      AND i.status = 1
      AND r.status  = 1
      {search_clause}
    ORDER BY r.nombre, i.hora_inicio, i.turno
    LIMIT %s OFFSET %s
"""


def get_itineraries_paged(
    id_empresa: int,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
    search: str = "",
    id_ruta: int | None = None,
) -> dict:
    """
    Listado plano paginado de itinerarios con metadatos de paginación.

    Respuesta:
    {
      "data": [ { id_itinerario, turno, nombre_ruta, ... }, ... ],
      "pagination": {
        "page":        1,
        "page_size":   25,
        "total":       147,
        "total_pages": 6
      }
    }

    Args:
        id_empresa: Empresa del usuario autenticado.
        page:       Número de página (base 1).
        page_size:  Registros por página (máx PAGE_SIZE_MAX).
        search:     Búsqueda en nombre o clave de ruta.
        id_ruta:    Filtro opcional por ruta específica.
    """
    # Proteger contra valores fuera de rango
    page = max(1, page)
    page_size = max(1, min(page_size, PAGE_SIZE_MAX))
    offset = (page - 1) * page_size

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            params = [id_empresa]
            clauses = []

            if search:
                clauses.append(
                    "AND (r.nombre ILIKE %s OR r.clave ILIKE %s OR i.turno ILIKE %s)"
                )
                like = f"%{search}%"
                params += [like, like, like]

            if id_ruta:
                clauses.append("AND i.id_ruta = %s")
                params.append(id_ruta)

            search_clause = " ".join(clauses)
            params += [page_size, offset]

            sql = _SQL_PAGED.format(
                cols_itinerario=_COLS_ITINERARIO,
                search_clause=search_clause,
            )
            cur.execute(sql, params)
            rows = cur.fetchall()

        if not rows:
            return {
                "data": [],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "total_pages": 0,
                },
            }

        # total_count viene en cada fila (window function COUNT(*) OVER ())
        total = rows[0][-1]  # última columna
        total_pages = (total + page_size - 1) // page_size

        data = []
        for row in rows:
            record = _row_to_dict(cur, row)
            record.pop("total_count", None)  # no exponer al cliente
            data.append(_serialize_itinerary(record))

        return {
            "data": data,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    finally:
        release_db_connection(conn)


# Detalle completo (para el editor)


def get_itinerary_by_id(id_itinerario: int, id_empresa: int) -> dict | None:
    """
    Retorna el itinerario completo con sus paradas y hora de abordaje.

    Respuesta:
    {
      id_itinerario, id_ruta, id_logistica_ruta, turno, tipo, dias,
      hora_inicio, hora_fin, tolerancias, total_paradas, fecha_inicio, ...
      "paradas": [
        { id_parada, numero, nombre, direccion, latitud, longitud,
          hora_abordaje, segundos_recorrido_continuo, segundos_recorrido_mixto },
        ...
      ]
    }
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Datos del itinerario
            cur.execute(
                f"""
                SELECT {_COLS_ITINERARIO},
                       r.clave AS clave_ruta, r.nombre AS nombre_ruta,
                       l.tipo_logistica, l.trace_color,
                       l.direccion_inicio, l.direccion_fin
                FROM t_itinerarios i
                INNER JOIN t_rutas r           ON r.id_ruta = i.id_ruta
                INNER JOIN t_logisticas_ruta l ON l.id_logistica_ruta = i.id_logistica_ruta
                WHERE i.id_itinerario = %s
                  AND i.id_empresa = %s
                  AND i.status = 1
                """,
                (id_itinerario, id_empresa),
            )
            row = cur.fetchone()
            if not row:
                return None

            itinerario = _serialize_itinerary(_row_to_dict(cur, row))

            # Paradas con hora de abordaje, ordenadas por número de parada
            cur.execute(
                """
                SELECT
                    p.id_parada, p.numero, p.nombre, p.direccion,
                    p.latitud, p.longitud, p.tipo_geocerca, p.radio,
                    rip.hora_abordaje,
                    rip.segundos_recorrido_continuo,
                    rip.segundos_recorrido_mixto
                FROM r_itinerario_paradas rip
                INNER JOIN t_paradas_ruta p ON p.id_parada = rip.id_parada
                WHERE rip.id_itinerario = %s
                ORDER BY p.numero ASC
                """,
                (id_itinerario,),
            )
            pcols = [d[0] for d in cur.description]
            paradas = []
            for prow in cur.fetchall():
                parada = dict(zip(pcols, prow))
                parada["latitud"] = float(parada["latitud"])
                parada["longitud"] = float(parada["longitud"])
                paradas.append(_serialize_parada(parada))

            itinerario["paradas"] = paradas
            return itinerario

    finally:
        release_db_connection(conn)


# Validación de duplicados


def is_turno_taken(
    turno: str,
    id_logistica_ruta: int,
    exclude_id: int | None = None,
) -> bool:
    """
    Verifica si ya existe un itinerario con el mismo turno en la misma logística.

    En la v2.5 el par (turno, id_logistica_ruta) era único: no puede haber
    dos "turno 1" en el mismo sentido de la misma ruta.

    Args:
        turno:             Código de turno ('1', '2', '1A', etc.)
        id_logistica_ruta: La logística sobre la que corre el itinerario.
        exclude_id:        id_itinerario a excluir (para el UPDATE — no contar el mismo).
    """
    if not turno:
        return False

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if exclude_id:
                cur.execute(
                    """
                    SELECT 1 FROM t_itinerarios
                    WHERE turno = %s
                      AND id_logistica_ruta = %s
                      AND id_itinerario != %s
                      AND status = 1
                    """,
                    (turno, id_logistica_ruta, exclude_id),
                )
            else:
                cur.execute(
                    """
                    SELECT 1 FROM t_itinerarios
                    WHERE turno = %s
                      AND id_logistica_ruta = %s
                      AND status = 1
                    """,
                    (turno, id_logistica_ruta),
                )
            return cur.fetchone() is not None
    finally:
        release_db_connection(conn)


# Crear


def create_itinerary(
    payload: dict,
    id_empresa: int,
    id_usuario: int,
) -> int:
    """
    Crea un itinerario con sus paradas en una sola transacción.

    Args:
        payload:    Datos validados por CreateItinerarySchema.
        id_empresa: Empresa del usuario autenticado.
        id_usuario: Usuario que crea el itinerario (para auditoría).

    Returns:
        id_itinerario del registro creado.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # 1. Insertar el itinerario
            cur.execute(
                """
                INSERT INTO t_itinerarios (
                    id_empresa, id_ruta, id_logistica_ruta,
                    turno, tipo, dias,
                    hora_inicio, hora_fin,
                    minutos_tolerancia_inicio,
                    minutos_tolerancia_fin,
                    minutos_tolerancia_anticipacion,
                    total_paradas, fecha_inicio,
                    token, id_usuario_registro
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id_itinerario
                """,
                (
                    id_empresa,
                    payload["id_ruta"],
                    payload["id_logistica_ruta"],
                    payload.get("turno") or None,
                    payload.get("tipo", 1),
                    payload.get("dias", []),
                    payload.get("hora_inicio") or None,
                    payload.get("hora_fin") or None,
                    payload.get("minutos_tolerancia_inicio", 30),
                    payload.get("minutos_tolerancia_fin", 0),
                    payload.get("minutos_tolerancia_anticipacion", 10),
                    len(payload.get("paradas", [])),
                    payload.get("fecha_inicio") or None,
                    secrets.token_urlsafe(9)[:15],  # token de 15 chars
                    id_usuario,
                ),
            )
            id_itinerario = cur.fetchone()[0]

            # 2. Insertar paradas con hora de abordaje
            _insert_paradas(cur, id_itinerario, payload.get("paradas", []))

            conn.commit()
            return id_itinerario

    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


def _insert_paradas(cur, id_itinerario: int, paradas: list[dict]) -> None:
    """
    Helper privado: inserta las paradas de un itinerario.
    Se llama tanto en create como en update (tras borrar las previas).
    """
    for parada in paradas:
        cur.execute(
            """
            INSERT INTO r_itinerario_paradas
                (id_itinerario, id_parada, hora_abordaje,
                 segundos_recorrido_continuo, segundos_recorrido_mixto)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                id_itinerario,
                parada["id_parada"],
                parada.get("hora_abordaje") or None,
                parada.get("segundos_recorrido_continuo") or None,
                parada.get("segundos_recorrido_mixto") or None,
            ),
        )


# Actualizar


def update_itinerary(
    id_itinerario: int,
    payload: dict,
    id_empresa: int,
    id_usuario: int,
) -> bool:
    """
    Actualiza un itinerario. Reemplaza sus paradas por las nuevas.

    Returns:
        True si se actualizó correctamente.
        False si el itinerario no existe o no pertenece a la empresa.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Verificar que el itinerario exista y sea de la empresa
            cur.execute(
                """
                SELECT 1 FROM t_itinerarios
                WHERE id_itinerario = %s AND id_empresa = %s AND status = 1
                """,
                (id_itinerario, id_empresa),
            )
            if not cur.fetchone():
                return False

            # Actualizar campos del itinerario
            cur.execute(
                """
                UPDATE t_itinerarios SET
                    id_ruta                          = %s,
                    id_logistica_ruta                = %s,
                    turno                            = %s,
                    tipo                             = %s,
                    dias                             = %s,
                    hora_inicio                      = %s,
                    hora_fin                         = %s,
                    minutos_tolerancia_inicio        = %s,
                    minutos_tolerancia_fin           = %s,
                    minutos_tolerancia_anticipacion  = %s,
                    total_paradas                    = %s,
                    fecha_inicio                     = %s,
                    fecha_cambio                     = CURRENT_TIMESTAMP,
                    id_usuario_cambio                = %s
                WHERE id_itinerario = %s
                """,
                (
                    payload["id_ruta"],
                    payload["id_logistica_ruta"],
                    payload.get("turno") or None,
                    payload.get("tipo", 1),
                    payload.get("dias", []),
                    payload.get("hora_inicio") or None,
                    payload.get("hora_fin") or None,
                    payload.get("minutos_tolerancia_inicio", 30),
                    payload.get("minutos_tolerancia_fin", 0),
                    payload.get("minutos_tolerancia_anticipacion", 10),
                    len(payload.get("paradas", [])),
                    payload.get("fecha_inicio") or None,
                    id_usuario,
                    id_itinerario,
                ),
            )

            # Reemplazar paradas (DELETE + INSERT — el CASCADE de la FK
            # garantiza que no queden huérfanas si usamos el servicio correctamente)
            cur.execute(
                "DELETE FROM r_itinerario_paradas WHERE id_itinerario = %s",
                (id_itinerario,),
            )
            _insert_paradas(cur, id_itinerario, payload.get("paradas", []))

            conn.commit()
            return True

    except Exception:
        conn.rollback()
        raise
    finally:
        release_db_connection(conn)


# Eliminar (soft-delete)


def delete_itinerary(id_itinerario: int, id_empresa: int) -> bool:
    """
    Soft-delete: marca el itinerario como inactivo (status=0).

    No borra las paradas ni el registro — se mantiene el historial.
    Mismo patrón que delete_route() en route_service.py.

    Returns:
        True si se marcó como eliminado, False si no existía o no era de la empresa.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE t_itinerarios
                SET status = 0
                WHERE id_itinerario = %s
                  AND id_empresa = %s
                  AND status = 1
                """,
                (id_itinerario, id_empresa),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
    finally:
        release_db_connection(conn)
