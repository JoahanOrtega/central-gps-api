BEGIN;

ALTER TABLE t_cargas_combustible ALTER COLUMN litros TYPE NUMERIC(20, 4);
ALTER TABLE t_cargas_combustible ALTER COLUMN costo_litro TYPE NUMERIC(20, 6);

COMMIT;