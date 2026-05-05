-- Migration 005: r_poi_unidades
-- Tracks current geographic state of each unit relative to each POI.
-- Answers: is unit X currently INSIDE or OUTSIDE POI Y?
--
-- To apply:
--   psql -U <user> -d <database> -f migrations/005_create_r_poi_unidades.sql
--
-- To revert:
--   DROP TABLE IF EXISTS public.r_poi_unidades;

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Main table
-- ---------------------------------------------------------------------------
-- Row count is bounded: MAX_ROWS = active_units x pois_per_company
-- For 1000 units x 500 POIs = 500,000 rows — manageable without partitioning.
--
-- No FK to t_pois / t_unidades intentionally:
-- The worker updates these rows every 15s in batch. ON DELETE CASCADE FKs
-- would leave inconsistent state if a POI is deleted mid-cycle.
-- Referential integrity is enforced at the service layer.

CREATE TABLE IF NOT EXISTS public.r_poi_unidades (

    -- Primary key
    id_poi_unidad           SERIAL          PRIMARY KEY,

    -- Relations (no FK by design — see note above)
    id_poi                  INTEGER         NOT NULL,
    id_unidad               INTEGER         NOT NULL,
    id_empresa              INTEGER         NOT NULL,   -- denormalized for company queries without JOIN

    -- Current geographic state
    -- in_actual: 1 = unit inside POI perimeter, 0 = outside
    -- This is the critical field — worker compares it against new calculation.
    in_actual               SMALLINT        NOT NULL DEFAULT 0
                            CHECK (in_actual IN (0, 1)),

    -- Timestamps of the current event
    -- fecha_hora_in:  when the unit entered in the current event. NULL if never entered or already exited.
    -- fecha_hora_out: when the unit last exited. NULL if never exited.
    fecha_hora_in           TIMESTAMP       NULL,
    fecha_hora_out          TIMESTAMP       NULL,

    -- Last GPS timestamp processed for this pair.
    -- Worker skips GPS data whose timestamp is <= this value to avoid reprocessing.
    fecha_hora_gps          TIMESTAMP       NULL,

    -- Permanence alert flag.
    -- 0 = not yet fired for current event. 1 = already fired.
    -- Resets to 0 each time the unit re-enters (fecha_hora_in updates).
    alerta_permanencia      SMALLINT        NOT NULL DEFAULT 0
                            CHECK (alerta_permanencia IN (0, 1)),

    -- Max speed alert inside POI
    -- fecha_hora_ini_vel_max: when the speed excess started. NULL if no active excess.
    -- vel_max_alcanzada: peak speed detected during the active excess.
    fecha_hora_ini_vel_max  TIMESTAMP       NULL,
    vel_max_alcanzada       NUMERIC(7,2)    NULL,

    -- Audit
    fecha_registro          TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_cambio            TIMESTAMP       NULL

);

COMMENT ON TABLE public.r_poi_unidades IS
    'Current state of each unit relative to each POI. '
    '1 row per (id_unidad, id_poi) pair. '
    'Updated by the geofence worker on every detection cycle.';

COMMENT ON COLUMN public.r_poi_unidades.in_actual IS
    '1 = unit inside POI perimeter, 0 = outside.';

COMMENT ON COLUMN public.r_poi_unidades.fecha_hora_gps IS
    'Timestamp of last GPS data processed for this pair. '
    'Worker skips data with timestamp <= this value.';

COMMENT ON COLUMN public.r_poi_unidades.alerta_permanencia IS
    '0 = permanence alert not yet fired for current event. '
    '1 = already fired. Resets to 0 on each new entry.';


-- ---------------------------------------------------------------------------
-- 2. Uniqueness constraint
-- ---------------------------------------------------------------------------
-- Guarantees exactly 1 row per (unit, POI) pair.
-- Worker uses INSERT ... ON CONFLICT (id_unidad, id_poi) DO UPDATE
-- for atomic upsert without race conditions.

ALTER TABLE public.r_poi_unidades
    ADD CONSTRAINT uq_r_poi_unidades_unidad_poi
    UNIQUE (id_unidad, id_poi);


-- ---------------------------------------------------------------------------
-- 3. Indexes
-- ---------------------------------------------------------------------------

-- Main worker index: "give me all records for company X where unit is inside a POI"
-- Partial filter excludes 80-90% of rows (most units will be outside).
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_empresa_in
    ON public.r_poi_unidades (id_empresa)
    WHERE in_actual = 1;

-- For the endpoint: "which POI is unit X currently in?"
-- Frequent query from the live map.
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_unidad
    ON public.r_poi_unidades (id_unidad);

-- For cleanup when a POI is deleted.
CREATE INDEX IF NOT EXISTS idx_r_poi_unidades_poi
    ON public.r_poi_unidades (id_poi);


-- ---------------------------------------------------------------------------
-- 4. Trigger: auto-update fecha_cambio
-- ---------------------------------------------------------------------------
-- Worker does direct UPDATE without passing fecha_cambio in the payload.
-- Trigger guarantees it is always updated, same pattern as t_pois and
-- other tables in this project.

CREATE OR REPLACE FUNCTION public.set_r_poi_unidades_fecha_cambio()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- Only update if a relevant field actually changed.
    -- Avoids unnecessary writes if the worker confirms state without change
    -- (e.g. unit is still inside and worker confirms it).
    IF (
        NEW.in_actual          IS DISTINCT FROM OLD.in_actual          OR
        NEW.fecha_hora_in      IS DISTINCT FROM OLD.fecha_hora_in      OR
        NEW.fecha_hora_out     IS DISTINCT FROM OLD.fecha_hora_out     OR
        NEW.alerta_permanencia IS DISTINCT FROM OLD.alerta_permanencia
    ) THEN
        NEW.fecha_cambio = NOW();
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.set_r_poi_unidades_fecha_cambio() IS
    'Updates fecha_cambio only when geographic state fields change. '
    'Avoids redundant writes when the worker confirms unchanged state.';

CREATE TRIGGER trg_r_poi_unidades_fecha_cambio
    BEFORE UPDATE ON public.r_poi_unidades
    FOR EACH ROW
    EXECUTE FUNCTION public.set_r_poi_unidades_fecha_cambio();


COMMIT;