-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 003: agregar columna status a t_pois (soft-delete)
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   La tabla t_pois del legacy NO tiene columna status. El service
--   poi_service.get_pois ya filtra por `AND status = 1` (asumiendo
--   soft-delete como otras entidades del sistema), pero la columna no
--   existe → todas las queries fallan con UndefinedColumn.
--
-- Solución:
--   Agregar la columna status con default=1 (activo). Todos los POIs
--   existentes quedan automáticamente como activos. La eliminación
--   (soft-delete) cambia status a 0 sin borrar la fila.
--
-- Consistencia:
--   - t_unidades:    tiene status (legacy + PR 3 lo usa para soft-delete)
--   - t_clientes:    tiene status
--   - t_usuarios:    tiene status
--   - t_pois:        ← NO tenía. Esta migración lo agrega.
--   - t_grupos_pois: revisar también si lo necesita
--
-- Idempotencia:
--   IF NOT EXISTS evita error si la migración se aplica dos veces.
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── 1. Agregar la columna status a t_pois ─────────────────────────────────────
ALTER TABLE public.t_pois
    ADD COLUMN IF NOT EXISTS status smallint NOT NULL DEFAULT 1;

-- ── 2. Comentario para documentar la convención ───────────────────────────────
COMMENT ON COLUMN public.t_pois.status IS
    'Soft-delete: 1=activo, 0=eliminado. Espejo de t_unidades.status.';

-- ── 3. Índice parcial para mejorar queries de listado ─────────────────────────
-- La mayoría de queries son "WHERE id_empresa = X AND status = 1".
-- El índice parcial (solo activos) acelera esa lectura sin pesar mucho
-- en escrituras porque los inactivos casi no se consultan.
CREATE INDEX IF NOT EXISTS idx_pois_empresa_activos
    ON public.t_pois (id_empresa)
    WHERE status = 1;

-- ── 4. Verificación ──────────────────────────────────────────────────────────
-- Todos los POIs existentes deben quedar con status=1.
SELECT
    COUNT(*) AS total_pois,
    COUNT(*) FILTER (WHERE status = 1) AS activos,
    COUNT(*) FILTER (WHERE status = 0) AS inhabilitados
FROM public.t_pois;

-- Esperado:
--   total_pois = activos (todos los existentes ahora son 1)
--   inhabilitados = 0

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- DESPUÉS DE APLICAR
--
-- 1. Reiniciar el backend (NO es necesario, postgres recoge el cambio
--    al instante porque ALTER TABLE en una transacción separada).
--
-- 2. Recargar /home/catalogs/points-of-interest.
--    Ya debería mostrar la lista de POIs sin error 500.
--
-- 3. Si en el futuro quieres "eliminar" un POI:
--    UPDATE t_pois SET status = 0 WHERE id_poi = X;
--    El POI desaparece del catálogo pero queda en BD para histórico.
-- ─────────────────────────────────────────────────────────────────────────────