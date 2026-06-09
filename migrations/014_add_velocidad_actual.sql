BEGIN;

ALTER TABLE t_itinerario_fecha_unidad
    ADD COLUMN IF NOT EXISTS velocidad_actual NUMERIC(6,2) DEFAULT 0;

COMMENT ON COLUMN t_itinerario_fecha_unidad.velocidad_actual IS
    'Velocidad del último ping GPS procesado por el worker (km/h).
     Se actualiza en cada ciclo junto con vel_max y fecha_hora_update.';

COMMIT;