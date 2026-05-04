-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 006: crear tabla t_alertas_poi
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Almacena la CONFIGURACIÓN de alertas que el usuario administrador
--   define por cada POI. Esta tabla responde:
--   "¿qué alertas están activas para el POI X y a quiénes afectan?"
--
--   Tipos de alerta soportados (equivalentes al legacy PHP):
--     - in_out:      avisar cuando una unidad entra o sale del perimetro
--     - permanencia: avisar si la unidad pasa más/menos tiempo del configurado
--     - vel_max:     avisar si la unidad excede la velocidad máxima dentro del POI
--
--   Diferencia clave vs legacy:
--     En el legacy, la configuración de alertas y el estado geográfico
--     vivían mezclados en una sola tabla (r_poi_unidades + columnas de alerta).
--     Aquí los separamos:
--       - t_alertas_poi  → configuración (raramente cambia)
--       - r_poi_unidades → estado geográfico (cambia cada 15s)
--     Esto evita contención de escritura entre el admin que edita alertas
--     y el worker que actualiza posiciones.
--
-- Alcance de las alertas:
--   El campo `alcance` define si la alerta aplica a:
--     1 = grupo de unidades (id_grupo_unidades)
--     2 = todas las unidades de la empresa
--   No se implementa "unidades individuales" por ahora — el legacy lo tenía
--   pero se usaba poco y complicaba las queries del worker.
--
-- Cómo aplicar:
--   psql -U <usuario> -d <base_de_datos> -f migrations/006_create_t_alertas_poi.sql
--
-- Cómo revertir:
--   DROP TABLE IF EXISTS public.t_alertas_poi;
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Tabla principal
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.t_alertas_poi (

    -- ── Clave primaria ──────────────────────────────────────────────────────
    id_alerta_poi   SERIAL          PRIMARY KEY,

    -- ── POI al que pertenece esta configuración de alerta ───────────────────
    -- Una alerta pertenece a exactamente un POI.
    -- Si el POI se elimina (soft-delete), la alerta queda huérfana pero no
    -- se borra automáticamente — permite restaurar el POI con su config.
    -- El worker verifica que el POI tenga status=1 antes de procesar.
    id_poi          INTEGER         NOT NULL
                    REFERENCES public.t_pois (id_poi) ON DELETE RESTRICT,

    id_empresa      INTEGER         NOT NULL,   -- desnormalizado para filtros rápidos

    -- ── Alerta de entrada / salida ───────────────────────────────────────────
    -- in_out: 1 = activa, 0 = inactiva
    -- Cuando está activa, el worker genera eventos 10 (entró) y 11 (salió).
    in_out          SMALLINT        NOT NULL DEFAULT 0
                    CHECK (in_out IN (0, 1)),

    -- ── Alerta de permanencia ────────────────────────────────────────────────
    -- permanencia: 1 = activa, 0 = inactiva
    -- tipo_permanencia:
    --   1 = avisar si la unidad pasa MÁS tiempo del permitido (max excedido)
    --   2 = avisar si la unidad pasa MENOS tiempo del necesario (min no cumplido)
    -- minutos_permanencia: umbral en minutos
    permanencia         SMALLINT    NOT NULL DEFAULT 0
                        CHECK (permanencia IN (0, 1)),
    tipo_permanencia    SMALLINT    NULL
                        CHECK (tipo_permanencia IN (1, 2)),
    minutos_permanencia INTEGER     NULL
                        CHECK (minutos_permanencia IS NULL OR minutos_permanencia > 0),

    -- ── Alerta de velocidad máxima dentro del POI ────────────────────────────
    -- vel_max: 1 = activa, 0 = inactiva
    -- vel_max_permitida: velocidad en km/h. Si la unidad la supera dentro
    --   del POI, se genera evento 14 (inicio exceso) y 15 (fin exceso).
    vel_max             SMALLINT    NOT NULL DEFAULT 0
                        CHECK (vel_max IN (0, 1)),
    vel_max_permitida   INTEGER     NULL
                        CHECK (vel_max_permitida IS NULL OR vel_max_permitida > 0),

    -- ── Alcance de la alerta ─────────────────────────────────────────────────
    -- Define a qué unidades aplica esta alerta.
    --   1 = grupo específico (requiere id_grupo_unidades)
    --   2 = todas las unidades de la empresa
    alcance             SMALLINT    NOT NULL DEFAULT 2
                        CHECK (alcance IN (1, 2)),
    id_grupo_unidades   INTEGER     NULL,   -- solo relevante cuando alcance=1

    -- ── Estado de la alerta ──────────────────────────────────────────────────
    -- status: 1 = activa (el worker la procesa), 0 = inactiva (ignorada)
    -- Permite desactivar toda la configuración de un POI sin borrarla.
    status              SMALLINT    NOT NULL DEFAULT 1
                        CHECK (status IN (0, 1)),

    -- ── Auditoría ────────────────────────────────────────────────────────────
    fecha_registro      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro INTEGER     NULL,
    fecha_cambio        TIMESTAMP   NULL,
    id_usuario_cambio   INTEGER     NULL

);

COMMENT ON TABLE public.t_alertas_poi IS
    'Configuración de alertas por POI: qué tipos de alerta están activas, '
    'a qué unidades aplican, y cuáles son los umbrales (permanencia, velocidad). '
    '1 fila por POI — si un POI no tiene fila aquí, no genera alertas.';

COMMENT ON COLUMN public.t_alertas_poi.in_out IS
    '1 = notificar cuando una unidad entra o sale del perímetro del POI.';

COMMENT ON COLUMN public.t_alertas_poi.permanencia IS
    '1 = alerta de tiempo dentro del POI activa. '
    'Ver tipo_permanencia y minutos_permanencia para el detalle.';

COMMENT ON COLUMN public.t_alertas_poi.tipo_permanencia IS
    '1 = avisar si la unidad excede el tiempo máximo. '
    '2 = avisar si la unidad no cumple el tiempo mínimo.';

COMMENT ON COLUMN public.t_alertas_poi.vel_max IS
    '1 = alerta de velocidad máxima dentro del POI activa.';

COMMENT ON COLUMN public.t_alertas_poi.alcance IS
    '1 = solo aplica al grupo de unidades definido en id_grupo_unidades. '
    '2 = aplica a todas las unidades activas de la empresa.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Restricción: un POI solo puede tener una configuración de alertas
-- ─────────────────────────────────────────────────────────────────────────────
-- Si en el futuro se decide permitir múltiples configuraciones por POI
-- (p.ej. alerta diferente por grupo), se puede quitar esta restricción
-- y ajustar la query del worker.

ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT uq_t_alertas_poi_id_poi
    UNIQUE (id_poi);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Restricción: validación cruzada de campos de permanencia
-- ─────────────────────────────────────────────────────────────────────────────
-- Si la alerta de permanencia está activa, DEBE tener tipo y minutos.
-- Si está inactiva, tipo y minutos no importan (pueden ser NULL).
-- Esta regla previene configuraciones incoherentes desde la BD misma,
-- no solo desde el frontend.

ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_permanencia_coherente CHECK (
        permanencia = 0
        OR (
            permanencia = 1
            AND tipo_permanencia IS NOT NULL
            AND minutos_permanencia IS NOT NULL
        )
    );

-- Similar para velocidad máxima: si vel_max=1, vel_max_permitida no puede ser NULL.
ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_vel_max_coherente CHECK (
        vel_max = 0
        OR (vel_max = 1 AND vel_max_permitida IS NOT NULL)
    );

-- Si alcance=1 (grupo), id_grupo_unidades es obligatorio.
ALTER TABLE public.t_alertas_poi
    ADD CONSTRAINT chk_alertas_poi_alcance_coherente CHECK (
        alcance = 2
        OR (alcance = 1 AND id_grupo_unidades IS NOT NULL)
    );


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Índices
-- ─────────────────────────────────────────────────────────────────────────────

-- El worker lee TODAS las alertas activas de una empresa en cada ciclo.
-- Este es el acceso más frecuente a esta tabla.
CREATE INDEX IF NOT EXISTS idx_t_alertas_poi_empresa_activas
    ON public.t_alertas_poi (id_empresa)
    WHERE status = 1;

-- Lookup por POI: para el endpoint de configuración del POI en el frontend.
CREATE INDEX IF NOT EXISTS idx_t_alertas_poi_poi
    ON public.t_alertas_poi (id_poi);


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Trigger: actualizar fecha_cambio automáticamente
-- ─────────────────────────────────────────────────────────────────────────────

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
    'Actualiza fecha_cambio en cada UPDATE de t_alertas_poi. '
    'Permite auditar cuándo cambió la configuración de alertas de un POI.';

CREATE TRIGGER trg_t_alertas_poi_fecha_cambio
    BEFORE UPDATE ON public.t_alertas_poi
    FOR EACH ROW
    EXECUTE FUNCTION public.set_t_alertas_poi_fecha_cambio();


COMMIT;