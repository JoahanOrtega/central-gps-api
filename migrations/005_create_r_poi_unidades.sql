-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 005: crear tabla r_poi_unidades
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Esta tabla es el "estado actual" de cada combinación unidad ↔ POI.
--   Responde la pregunta: ¿la unidad X está DENTRO o FUERA del POI Y en
--   este momento?
--
--   El worker de detección de geocercas (Tarea 2) consulta y actualiza
--   esta tabla en cada ciclo. Si old_in ≠ new_in, hay un evento
--   de entrada (evento=10) o salida (evento=11) que se persiste en
--   t_eventos_poi (Migración 006).
--
-- Equivalencia con legacy PHP:
--   Directa con la tabla `r_poi_unidades` de MariaDB. Se eliminan los
--   campos de alertas (in_out, vel_max, permanencia, etc.) porque en
--   la nueva arquitectura esa configuración vive en t_alertas_poi
--   (Migración 006). Esta tabla es solo estado geográfico puro.
--
-- Particionado:
--   No se particiona. El número de filas es acotado:
--     MAX_FILAS = count(t_unidades_activas) × count(t_pois_por_empresa)
--   Para 1000 unidades × 500 POIs = 500,000 filas — manejable en una
--   sola tabla con los índices correctos.
--
-- Cómo aplicar:
--   psql -U <usuario> -d <base_de_datos> -f migrations/005_create_r_poi_unidades.sql
--
-- Cómo revertir:
--   DROP TABLE IF EXISTS public.r_poi_unidades;
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tabla principal
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.r_poi_unidades (

    -- ── Clave primaria ──────────────────────────────────────────────────────
    id_poi_unidad   SERIAL          PRIMARY KEY,

    -- ── Relaciones ──────────────────────────────────────────────────────────
    -- NO usamos FK a t_pois y t_unidades por rendimiento:
    -- el worker actualiza estas filas cada 15s en batch. Las FK con
    -- ON DELETE CASCADE las dejaría en estado inconsistente si se elimina
    -- un POI mientras el worker está corriendo. La integridad se garantiza
    -- a nivel de servicio (el worker verifica que el POI/unidad existan
    -- antes de insertar).
    id_poi          INTEGER         NOT NULL,
    id_unidad       INTEGER         NOT NULL,
    id_empresa      INTEGER         NOT NULL,   -- desnormalizado para queries por empresa sin JOIN

    -- ── Estado geográfico actual ─────────────────────────────────────────────
    -- in_actual: 1 = unidad dentro del POI, 0 = fuera
    -- Es el campo crítico — el worker compara in_actual contra el nuevo
    -- cálculo para determinar si hubo cambio.
    in_actual       SMALLINT        NOT NULL DEFAULT 0
                    CHECK (in_actual IN (0, 1)),

    -- ── Timestamps del evento actual ─────────────────────────────────────────
    -- fecha_hora_in: cuándo entró al POI en el evento actual.
    --   NULL si nunca ha entrado, o si ya salió (in_actual=0).
    -- fecha_hora_out: cuándo salió del POI en el último evento.
    --   NULL si nunca ha salido.
    fecha_hora_in   TIMESTAMP       NULL,
    fecha_hora_out  TIMESTAMP       NULL,

    -- ── Timestamp del último dato GPS procesado ──────────────────────────────
    -- El worker solo procesa un dato GPS si su fecha_hora_gps es POSTERIOR
    -- a este campo. Evita reprocesar el mismo punto si el worker tiene lag.
    fecha_hora_gps  TIMESTAMP       NULL,

    -- ── Alertas de permanencia ───────────────────────────────────────────────
    -- alerta_permanencia: flag para saber si ya se disparó la alerta de
    -- permanencia en el evento actual. Se resetea a 0 cada vez que la
    -- unidad entra de nuevo (fecha_hora_in se actualiza).
    -- Equivalente al campo `alerta_permanencia` del legacy PHP.
    alerta_permanencia SMALLINT     NOT NULL DEFAULT 0
                       CHECK (alerta_permanencia IN (0, 1)),

    -- ── Alertas de velocidad máxima dentro del POI ───────────────────────────
    -- fecha_hora_ini_vel_max: cuándo inició el exceso de velocidad dentro del POI.
    --   NULL si no hay exceso activo.
    -- vel_max_alcanzada: la velocidad máxima detectada en el exceso activo.
    fecha_hora_ini_vel_max  TIMESTAMP   NULL,
    vel_max_alcanzada       NUMERIC(7,2) NULL,

    -- ── Auditoría de la fila ─────────────────────────────────────────────────
    fecha_registro  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_cambio    TIMESTAMP       NULL

);

COMMENT ON TABLE public.r_poi_unidades IS
    'Estado actual de cada unidad respecto a cada POI. '
    '1 fila por combinación (id_unidad, id_poi). '
    'El worker de geocercas actualiza esta tabla cada ciclo de detección.';

COMMENT ON COLUMN public.r_poi_unidades.in_actual IS
    '1 = unidad dentro del perímetro del POI, 0 = fuera.';

COMMENT ON COLUMN public.r_poi_unidades.fecha_hora_gps IS
    'Timestamp del último dato GPS procesado para esta combinación. '
    'El worker omite datos cuyo timestamp sea <= este valor.';

COMMENT ON COLUMN public.r_poi_unidades.alerta_permanencia IS
    '0 = alerta de permanencia no disparada en el evento actual. '
    '1 = ya se disparó. Se resetea a 0 en cada nueva entrada.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Restricción de unicidad
-- ─────────────────────────────────────────────────────────────────────────────
-- Garantiza que existe exactamente 1 fila por par (unidad, POI).
-- El worker usa INSERT ... ON CONFLICT (id_unidad, id_poi) DO UPDATE
-- para hacer upsert sin race conditions.

ALTER TABLE public.r_poi_unidades
    ADD CONSTRAINT uq_r_poi_unidades_unidad_poi
    UNIQUE (id_unidad, id_poi);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Índices
-- ─────────────────────────────────────────────────────────────────────────────

-- Índice principal del worker: "dame todos los registros de la empresa X
-- donde la unidad esté dentro de algún POI (in_actual=1)".
-- El filtro parcial excluye el 80-90% de filas (la mayoría estará fuera).
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_empresa_in
    ON public.r_poi_unidades (id_empresa)
    WHERE in_actual = 1;

-- Índice para el endpoint que responde "¿en qué POI está la unidad X?"
-- Consulta frecuente del mapa en tiempo real.
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_unidad
    ON public.r_poi_unidades (id_unidad);

-- Índice para limpiezas por POI (cuando se elimina un POI, borrar sus filas).
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_poi
    ON public.r_poi_unidades (id_poi);


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Trigger: actualizar fecha_cambio automáticamente
-- ─────────────────────────────────────────────────────────────────────────────
-- El worker hace UPDATE directo sin pasar fecha_cambio en el payload.
-- El trigger lo garantiza siempre actualizado, igual que en t_pois y
-- otras tablas del proyecto que siguen este patrón.

CREATE OR REPLACE FUNCTION public.set_r_poi_unidades_fecha_cambio()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Solo actualizar si realmente cambió algún campo relevante.
    -- Evita escrituras innecesarias si el worker hace UPDATE con los
    -- mismos valores (p.ej. la unidad sigue dentro y el worker confirma).
    IF (
        NEW.in_actual       IS DISTINCT FROM OLD.in_actual       OR
        NEW.fecha_hora_in   IS DISTINCT FROM OLD.fecha_hora_in   OR
        NEW.fecha_hora_out  IS DISTINCT FROM OLD.fecha_hora_out  OR
        NEW.alerta_permanencia IS DISTINCT FROM OLD.alerta_permanencia
    ) THEN
        NEW.fecha_cambio = NOW();
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.set_r_poi_unidades_fecha_cambio() IS
    'Actualiza fecha_cambio solo cuando cambian campos de estado geográfico. '
    'Evita escrituras redundantes cuando el worker confirma estado sin cambio.';

CREATE TRIGGER trg_r_poi_unidades_fecha_cambio
    BEFORE UPDATE ON public.r_poi_unidades
    FOR EACH ROW
    EXECUTE FUNCTION public.set_r_poi_unidades_fecha_cambio();


COMMIT;