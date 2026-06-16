BEGIN;

ALTER TABLE t_operadores
    ADD COLUMN IF NOT EXISTS status SMALLINT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_operadores_status
    ON t_operadores (id_empresa, status);

COMMIT;