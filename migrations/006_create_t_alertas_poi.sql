-- Migration 006: t_alertas_poi
-- Alert configuration per POI (entry/exit, permanence, max speed)
--
-- To apply:
--   psql -U <user> -d <database> -f migrations/006_create_t_alertas_poi.sql
--
-- To revert:
--   DROP TABLE IF EXISTS public.t_alertas_poi;

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Main table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.t_alertas_poi (

    id_alerta_poi   SERIAL          PRIMARY KEY,

    -- POI this alert config belongs to
    id_poi          INTEGER         NOT NULL
                    REFERENCES public.t_pois (id_poi) ON DELETE RESTRICT,

    id_empresa      INTEGER         NOT NULL,

    -- Entry/exit alert: 1=active, 0=inactive
    in_out          SMALLINT        NOT NULL DEFAULT 0
                    CHECK (in_out IN (0, 1)),

    -- Permanence alert: 1=active, 0=inactive
    -- tipo_permanencia: 1=max exceeded, 2=min not met
    -- minutos_permanencia: threshold in minutes
    permanencia         SMALLINT    NOT NULL DEFAULT 0
                        CHECK (permanencia IN (0, 1)),
    tipo_permanencia    SMALLINT    NULL
                        CHECK (tipo_permanencia IN (1, 2)),
    minutos_permanencia INTEGER     NULL
                        CHECK (minutos_permanencia IS NULL OR minutos_permanencia > 0),

    -- Max speed alert inside POI: 1=active, 0=inactive
    -- vel_max_permitida: speed in km/h
    vel_max             SMALLINT    NOT NULL DEFAULT 0
                        CHECK (vel_max IN (0, 1)),
    vel_max_permitida   INTEGER     NULL
                        CHECK (vel_max_permitida IS NULL OR vel_max_permitida > 0),

    -- Alert scope
    -- 1=specific group (requires id_grupo_unidades)
    -- 2=all units of the company
    alcance             SMALLINT    NOT NULL DEFAULT 2
                        CHECK (alcance IN (1, 2)),
    id_grupo_unidades   INTEGER     NULL,

    -- status: 1=active (worker processes it), 0=inactive (ignored)
    status              SMALLINT    NOT NULL DEFAULT 1
                        CHECK (status IN (0, 1)),

    -- Audit
    fecha_registro      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro INTEGER     NULL,
    fecha_cambio        TIMESTAMP   NULL,
    id_usuario_cambio   INTEGER     NULL

);

COMMENT ON TABLE public.t_alertas_poi IS
    'Alert configuration per POI. '
    '1 row per POI. If a POI has no row here, it generates no alerts.';

COMMENT ON COLUMN public.t_alertas_poi.in_out IS
    '1 = notify when a unit enters or exits the POI perimeter.';

COMMENT ON COLUMN public.t_alertas_poi.permanencia IS
    '1 = time-inside-POI alert active. See tipo_permanencia and minutos_permanencia.';

COMMENT ON COLUMN public.t_alertas_poi.tipo_permanencia IS
    '1 = alert if unit exceeds max time. 2 = alert if unit does not meet min time.';

COMMENT ON COLUMN public.t_alertas_poi.vel_max IS
    '1 = max speed alert inside POI active.';

COMMENT ON COLUMN public.t_alertas_poi.alcance IS
    '1 = applies only to the unit group in id_grupo_unidades. '
    '2 = applies to all active units of the company.';


-- ---------------------------------------------------------------------------
-- 2. Unique constraint: one config per POI
-- ---------------------------------------------------------------------------

ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT uq_t_alertas_poi_id_poi
    UNIQUE (id_poi);


-- ---------------------------------------------------------------------------
-- 3. Cross-field validation constraints
-- ---------------------------------------------------------------------------

-- If permanencia=1, tipo_permanencia and minutos_permanencia are required
ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_permanencia_coherente CHECK (
        permanencia = 0
        OR (
            permanencia = 1
            AND tipo_permanencia IS NOT NULL
            AND minutos_permanencia IS NOT NULL
        )
    );

-- If vel_max=1, vel_max_permitida is required
ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_vel_max_coherente CHECK (
        vel_max = 0
        OR (vel_max = 1 AND vel_max_permitida IS NOT NULL)
    );

-- If alcance=1 (group), id_grupo_unidades is required
ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_alcance_coherente CHECK (
        alcance = 2
        OR (alcance = 1 AND id_grupo_unidades IS NOT NULL)
    );


-- ---------------------------------------------------------------------------
-- 4. Indexes
-- ---------------------------------------------------------------------------

-- Worker reads all active alerts for a company on each cycle
CREATE INDEX IF NOT EXISTS idx_t_alertas_poi_empresa_activas
    ON public.t_alertas_poi (id_empresa)
    WHERE status = 1;

-- Lookup by POI for the frontend config endpoint
CREATE INDEX IF NOT EXISTS idx_t_alertas_poi_poi
    ON public.t_alertas_poi (id_poi);


-- ---------------------------------------------------------------------------
-- 5. Trigger: auto-update fecha_cambio on UPDATE
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.set_t_alertas_poi_fecha_cambio()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.fecha_cambio = NOW();
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.set_t_alertas_poi_fecha_cambio() IS
    'Updates fecha_cambio on every UPDATE of t_alertas_poi.';

CREATE TRIGGER trg_t_alertas_poi_fecha_cambio
    BEFORE UPDATE ON public.t_alertas_poi
    FOR EACH ROW
    EXECUTE FUNCTION public.set_t_alertas_poi_fecha_cambio();


COMMIT;