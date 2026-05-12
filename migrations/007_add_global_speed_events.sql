-- Migration 007 — PARTE A: BD principal (centralgps_project)
-- ──────────────────────────────────────────────────────────────────────────────
--
-- Aplica únicamente en la BD PRINCIPAL local.
-- Aplicar:
--   docker compose exec db psql -U postgres -d centralgps_project -f /tmp/007.sql
--
-- NOTA IMPORTANTE:
--   t_eventos NO existe en centralgps_project — vive en la BD de telemetría
--   remota (136.119.58.28). Los índices sobre t_eventos van en el archivo
--   007b_telemetry_indices.sql que se aplica en el servidor remoto.
--   Este archivo solo toca t_unidades (BD principal).
--
-- Revertir:
--   DROP INDEX IF EXISTS idx_t_unidades_vel_max;

BEGIN;

-- ──────────────────────────────────────────────────────────────────────────────
-- 1. Índice en t_unidades.vel_max para el worker
-- ──────────────────────────────────────────────────────────────────────────────
-- El worker consulta t_unidades con filtro id_empresa + status = 1 para
-- obtener vel_max en cada ciclo. Este índice parcial permite un index-only
-- scan cuando hay muchas unidades inactivas (status=0).

CREATE INDEX IF NOT EXISTS idx_t_unidades_vel_max
    ON public.t_unidades (id_empresa, status)
    WHERE status = 1 AND vel_max IS NOT NULL AND vel_max > 0;

COMMENT ON INDEX public.idx_t_unidades_vel_max IS
    'Unidades activas con velocidad máxima configurada. '
    'Usado por el worker para filtrar unidades que requieren ev. 3/4.';

COMMIT;