BEGIN;

ALTER TABLE t_grupos_operadores
    ADD COLUMN IF NOT EXISTS status SMALLINT NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_grupos_operadores_status
    ON t_grupos_operadores (id_empresa, status);

COMMIT;
