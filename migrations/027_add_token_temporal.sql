-- CONTEXTO:
--   El modelo de tokens de unidades se está modificando para soportar tokens temporales
--
-- ESTRATEGIA DE MIGRACIÓN:
--   1. Se agregan nuevas columnas para el token temporal: `token_temporal`, `acceso_temporal` y `fecha_expiracion_temporal`.
--
-- REVERSIBLE:
--   ALTER TABLE t_unidades_token DROP COLUMN token_temporal,
--       DROP COLUMN acceso_temporal, DROP COLUMN fecha_expiracion_temporal;
--   DROP INDEX IF EXISTS idx_t_unidades_token_temporal;

BEGIN;

ALTER TABLE t_unidades_token
    ADD COLUMN IF NOT EXISTS token_temporal              VARCHAR(15),
    ADD COLUMN IF NOT EXISTS acceso_temporal              BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS fecha_expiracion_temporal    TIMESTAMP;

-- Índice único parcial para buscar por token temporal
CREATE UNIQUE INDEX IF NOT EXISTS idx_t_unidades_token_temporal
    ON t_unidades_token (token_temporal)
    WHERE token_temporal IS NOT NULL;

-- Migrar tokens existentes que eran temporales
UPDATE t_unidades_token
   SET token_temporal           = token,
       acceso_temporal          = acceso_token_rastreo,
       fecha_expiracion_temporal = fecha_expiracion,
       token                    = NULL,
       acceso_token_rastreo     = FALSE,
       fecha_expiracion         = NULL
 WHERE token IS NOT NULL
   AND fecha_expiracion IS NOT NULL;

-- Los tokens existentes SIN fecha_expiracion ya eran permanentes por diseño
-- y se quedan donde están (en el campo `token`). No necesitan migración.

COMMIT;