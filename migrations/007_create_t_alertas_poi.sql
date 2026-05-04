-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 007: crear tabla t_eventos_poi (particionada por mes)
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Registro histórico inmutable de TODOS los eventos de geocerca.
--   Cada vez que una unidad entra o sale de un POI, se inserta una fila aquí.
--   Es la fuente de verdad para reportes, historial y auditoría.
--
-- Tipos de evento (equivalencia con legacy PHP):
--   10 → entró al POI
--   11 → salió del POI
--   12 → permanencia máxima alcanzada (tiempo excedido)
--   13 → permanencia mínima no cumplida (salió muy pronto)
--   14 → inicio de exceso de velocidad dentro del POI
--   15 → fin de exceso de velocidad dentro del POI
--
-- Por qué particionado:
--   Esta tabla crece indefinidamente — es el log de eventos de toda la flota.
--   Con 1000 unidades monitoreando 50 POIs cada una, y asumiendo 10 eventos
--   por unidad por día → 10,000 eventos/día → 300,000/mes → 3.6M/año.
--   Sin particionado, las queries de reportes (rango de fechas) se
--   degradan linealmente con el tiempo.
--
-- Estrategia de particionado:
--   RANGE sobre fecha_hora_evento (truncada al mes).
--   Ventajas vs el legacy PHP (tablas semanales por nombre dinámico):
--     - Las particiones tienen FK reales (en PostgreSQL 12+ las tablas
--       particionadas soportan referencias)
--     - Los índices se crean automáticamente en cada partición
--     - Las queries con filtro de fecha_hora_evento aprovechan
--       "partition pruning" — solo escanean los meses relevantes
--     - La retención se hace con DROP TABLE de la partición, instantáneo
--       sin importar cuántas filas tenga
--
-- Retención sugerida:
--   Mantener 13 meses de particiones activas (año en curso + enero anterior).
--   Las particiones más antiguas se pueden archivar a cold storage o eliminar
--   dependiendo del contrato de servicio.
--
-- Nomenclatura de particiones:
--   t_eventos_poi_YYYY_MM (ej: t_eventos_poi_2026_05)
--   Se crean manualmente al inicio de cada mes con el script al final
--   de esta migración. Un cron job de mantenimiento puede automatizarlo.
--
-- Cómo aplicar:
--   psql -U <usuario> -d <base_de_datos> -f migrations/007_create_t_eventos_poi.sql
--
-- Cómo revertir:
--   DROP TABLE IF EXISTS public.t_eventos_poi CASCADE;
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tabla padre (estructura + declaración de particionado)
-- ─────────────────────────────────────────────────────────────────────────────
-- IMPORTANTE: en tablas particionadas por RANGE, la columna de particionado
-- (fecha_hora_evento) DEBE ser parte de la clave primaria.
-- La clave primaria compuesta (id_evento, fecha_hora_evento) garantiza
-- unicidad global y permite que PostgreSQL enrute los INSERT correctamente.

CREATE TABLE IF NOT EXISTS public.t_eventos_poi (

    -- ── Clave primaria compuesta (requerida por PARTITION BY RANGE) ─────────
    id_evento           BIGSERIAL       NOT NULL,
    fecha_hora_evento   TIMESTAMP       NOT NULL,   -- columna de particionado

    -- ── Quién y dónde ────────────────────────────────────────────────────────
    id_empresa          INTEGER         NOT NULL,
    id_unidad           INTEGER         NOT NULL,
    id_poi              INTEGER         NOT NULL,

    -- ── Qué pasó ─────────────────────────────────────────────────────────────
    -- tipo_evento:
    --   10 = entró al POI
    --   11 = salió del POI
    --   12 = permanencia máxima alcanzada
    --   13 = permanencia mínima no cumplida
    --   14 = inicio exceso de velocidad dentro del POI
    --   15 = fin exceso de velocidad dentro del POI
    tipo_evento         SMALLINT        NOT NULL
                        CHECK (tipo_evento IN (10, 11, 12, 13, 14, 15)),

    -- ── Datos del GPS en el momento del evento ───────────────────────────────
    latitud             NUMERIC(10,8)   NULL,
    longitud            NUMERIC(11,8)   NULL,
    velocidad           NUMERIC(7,2)    NULL,   -- km/h al momento del evento

    -- ── Detalles extra por tipo de evento ────────────────────────────────────
    -- Almacena datos variables según el tipo de evento:
    --   evento 12/13: { "fecha_hora_in": "...", "minutos": 45 }
    --   evento 14/15: { "vel_max_permitida": 40, "vel_max_alcanzada": 67.3,
    --                   "distancia_km": 0.3, "duracion_segundos": 18 }
    --   eventos 10/11: NULL (toda la info ya está en las otras columnas)
    detalles            JSONB           NULL,

    -- ── Metadatos de registro ────────────────────────────────────────────────
    -- fecha_registro es cuándo el worker detectó y registró el evento.
    -- fecha_hora_evento es cuándo ocurrió según el GPS (puede ser distinto
    -- si hay lag de red o el worker tuvo downtime).
    fecha_registro      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- ── Clave primaria compuesta ─────────────────────────────────────────────
    PRIMARY KEY (id_evento, fecha_hora_evento)

) PARTITION BY RANGE (fecha_hora_evento);


COMMENT ON TABLE public.t_eventos_poi IS
    'Log histórico de eventos de geocerca (entradas, salidas, permanencia, velocidad). '
    'Particionado por mes sobre fecha_hora_evento para mantener rendimiento en el tiempo. '
    'Tipos de evento: 10=entró, 11=salió, 12=perm.máx, 13=perm.min, 14=vel inicio, 15=vel fin.';

COMMENT ON COLUMN public.t_eventos_poi.fecha_hora_evento IS
    'Timestamp del evento según el GPS de la unidad. '
    'Columna de particionado — SIEMPRE incluirla en filtros WHERE para usar partition pruning.';

COMMENT ON COLUMN public.t_eventos_poi.detalles IS
    'JSON con datos variables según tipo_evento. '
    'Nulo para eventos 10/11 (entrada/salida). '
    'Ver comentario de la migración para el esquema por tipo.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Índices en la tabla padre
-- ─────────────────────────────────────────────────────────────────────────────
-- En PostgreSQL 11+, los índices creados en la tabla padre se heredan
-- automáticamente a todas las particiones presentes y futuras.

-- Índice para el endpoint de historial del mapa:
--   "dame los últimos N eventos de esta empresa en este rango de fechas"
-- El orden DESC es el que usa el frontend (más recientes primero).
CREATE INDEX IF NOT EXISTS idx_t_eventos_poi_empresa_fecha
    ON public.t_eventos_poi (id_empresa, fecha_hora_evento DESC);

-- Índice para el historial de una unidad específica:
--   "¿cuándo entró/salió esta unidad de cualquier POI?"
CREATE INDEX IF NOT EXISTS idx_t_eventos_poi_unidad_fecha
    ON public.t_eventos_poi (id_unidad, fecha_hora_evento DESC);

-- Índice para el historial de un POI específico:
--   "¿qué unidades entraron a este POI hoy?"
CREATE INDEX IF NOT EXISTS idx_t_eventos_poi_poi_fecha
    ON public.t_eventos_poi (id_poi, fecha_hora_evento DESC);

-- Índice para el SSE: "dame los eventos nuevos desde el timestamp X"
-- El worker publica en Redis, pero el endpoint de historial inicial
-- al conectar necesita este índice.
CREATE INDEX IF NOT EXISTS idx_t_eventos_poi_tipo_fecha
    ON public.t_eventos_poi (tipo_evento, fecha_hora_evento DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Particiones iniciales
-- ─────────────────────────────────────────────────────────────────────────────
-- Se crean las particiones del mes actual y los 2 meses siguientes.
-- Un cron job debe crear la partición del mes siguiente al inicio de cada mes.
-- Ver sección 5 (función de mantenimiento) para automatizarlo.

-- Mayo 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_05
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- Junio 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_06
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- Julio 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_07
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

-- Agosto 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_08
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

-- Septiembre 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_09
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

-- Octubre 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_10
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');

-- Noviembre 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_11
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');

-- Diciembre 2026
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2026_12
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
    
-- Enero 2027
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2027_01
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2027-01-01') TO ('2027-02-01');

-- Febrero 2027
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2027_02
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2027-02-01') TO ('2027-03-01');

-- Marzo 2027
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2027_03
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2027-03-01') TO ('2027-04-01');

-- Abril 2027
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2027_04
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2027-04-01') TO ('2027-05-01');

-- Mayo 2027
CREATE TABLE IF NOT EXISTS public.t_eventos_poi_2027_05
    PARTITION OF public.t_eventos_poi
    FOR VALUES FROM ('2027-05-01') TO ('2027-06-01');

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Partición DEFAULT (safety net)
-- ─────────────────────────────────────────────────────────────────────────────
-- Captura cualquier INSERT que no entre en ninguna partición definida.
-- Sin esto, un INSERT con fecha fuera de rango lanza un error y el worker
-- se cae. Con la partición default, el evento se guarda y se puede
-- migrar manualmente cuando se cree la partición correcta.

CREATE TABLE IF NOT EXISTS public.t_eventos_poi_default
    PARTITION OF public.t_eventos_poi
    DEFAULT;

COMMENT ON TABLE public.t_eventos_poi_default IS
    'Partición de seguridad: captura eventos cuya fecha_hora_evento no cae '
    'en ninguna partición mensual definida. Revisar periódicamente y mover '
    'los datos a la partición correcta si los hay.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Función de mantenimiento: crear_particion_siguiente_mes()
-- ─────────────────────────────────────────────────────────────────────────────
-- Crea la partición del mes siguiente si no existe.
-- Llamar desde un cron job el día 1 de cada mes a las 00:00 UTC-6:
--   SELECT public.crear_particion_eventos_poi_siguiente_mes();

CREATE OR REPLACE FUNCTION public.crear_particion_eventos_poi_siguiente_mes()
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    -- Calcular el primer día del mes siguiente
    v_inicio        DATE := DATE_TRUNC('month', NOW() + INTERVAL '1 month')::DATE;
    -- El límite superior es el primer día del mes posterior al que vamos a crear
    v_fin           DATE := DATE_TRUNC('month', NOW() + INTERVAL '2 months')::DATE;
    v_nombre        TEXT := 't_eventos_poi_' || TO_CHAR(v_inicio, 'YYYY_MM');
    v_existe        BOOLEAN;
    v_sql           TEXT;
BEGIN
    -- Verificar si la partición ya existe para evitar error en el cron
    SELECT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = v_nombre
          AND n.nspname = 'public'
    ) INTO v_existe;

    IF v_existe THEN
        RETURN 'SKIPPED: la partición ' || v_nombre || ' ya existe.';
    END IF;

    -- Crear la partición del mes siguiente
    v_sql := FORMAT(
        'CREATE TABLE public.%I PARTITION OF public.t_eventos_poi '
        'FOR VALUES FROM (%L) TO (%L)',
        v_nombre,
        v_inicio::TIMESTAMP,
        v_fin::TIMESTAMP
    );

    EXECUTE v_sql;

    RETURN 'CREATED: partición ' || v_nombre || ' creada para el rango ['
           || v_inicio || ', ' || v_fin || ')';

EXCEPTION WHEN OTHERS THEN
    RETURN 'ERROR: ' || SQLERRM;
END;
$$;

COMMENT ON FUNCTION public.crear_particion_eventos_poi_siguiente_mes() IS
    'Crea la partición del mes siguiente en t_eventos_poi si no existe. '
    'Llamar el día 1 de cada mes desde un cron job o pg_cron. '
    'Retorna un mensaje de texto describiendo la acción tomada.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Vista: v_eventos_poi_recientes
-- ─────────────────────────────────────────────────────────────────────────────
-- Vista de conveniencia que une eventos con nombres de unidad y POI.
-- El frontend y los reportes la usan para evitar JOINs repetidos.
-- Incluye solo los últimos 30 días para rendimiento (el índice
-- idx_t_eventos_poi_empresa_fecha la aprovecha al filtrar por empresa).

CREATE OR REPLACE VIEW public.v_eventos_poi_recientes AS
SELECT
    e.id_evento,
    e.id_empresa,
    e.id_unidad,
    u.numero            AS numero_unidad,
    u.marca             AS marca_unidad,
    e.id_poi,
    p.nombre            AS nombre_poi,
    p.tipo_poi          AS tipo_poi,
    e.tipo_evento,
    -- Etiqueta legible del tipo de evento — evita magic numbers en el frontend
    CASE e.tipo_evento
        WHEN 10 THEN 'Entró al POI'
        WHEN 11 THEN 'Salió del POI'
        WHEN 12 THEN 'Permanencia máxima excedida'
        WHEN 13 THEN 'Permanencia mínima no cumplida'
        WHEN 14 THEN 'Exceso de velocidad inicio'
        WHEN 15 THEN 'Exceso de velocidad fin'
        ELSE 'Evento desconocido'
    END                 AS descripcion_evento,
    e.latitud,
    e.longitud,
    e.velocidad,
    e.detalles,
    e.fecha_hora_evento,
    e.fecha_registro
FROM
    public.t_eventos_poi e
    -- LEFT JOIN para no perder eventos si la unidad o el POI fueron eliminados
    LEFT JOIN public.t_unidades u ON u.id_unidad = e.id_unidad
    LEFT JOIN public.t_pois     p ON p.id_poi     = e.id_poi
WHERE
    -- Limitar a 30 días para que la vista sea rápida por defecto.
    -- Queries de reportes históricos deben ir directo a t_eventos_poi
    -- con sus propios filtros de fecha.
    e.fecha_hora_evento >= NOW() - INTERVAL '30 days';

COMMENT ON VIEW public.v_eventos_poi_recientes IS
    'Vista de conveniencia: une t_eventos_poi con nombres de unidad y POI. '
    'Limitada a los últimos 30 días. Para rangos mayores, '
    'consultar t_eventos_poi directamente con filtro de fecha.';


COMMIT;