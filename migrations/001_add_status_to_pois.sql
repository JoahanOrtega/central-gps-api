-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 001: agregar columna status a t_pois
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   t_pois era la única tabla de entidades de negocio del proyecto que NO
--   tenía columna status (todas las demás —t_unidades, t_usuarios, t_empresas,
--   t_clientes, t_operadores, t_modelos_avl, t_grupos_unidades, t_grupos_pois,
--   t_roles, t_permisos— sí la tienen). Esto rompía el patrón de soft-delete
--   uniforme del sistema.
--
-- Esta migración:
--   1. Agrega la columna status como smallint con default 1 (activo).
--   2. Como ya hay POIs históricos sin la columna, el DEFAULT 1 los marca
--      automáticamente como activos. NOT NULL al final garantiza que ningún
--      POI futuro se inserte sin status.
--   3. Crea un índice parcial sobre id_empresa + status=1, que es la
--      consulta más frecuente (listado de POIs activos por empresa).
--      El índice parcial pesa menos que uno completo y acelera la query
--      común sin penalizar las raras (POIs eliminados).
--
-- Cómo aplicar:
--   psql -U <usuario> -d <base_de_datos> -f migrations/001_add_status_to_pois.sql
--
-- Cómo revertir (solo si el cambio falla):
--   ALTER TABLE t_pois DROP COLUMN status;
--   DROP INDEX IF EXISTS idx_t_pois_empresa_status;
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- 1. Agregar columna con default 1 para que los POIs existentes queden activos.
--    No usamos NOT NULL en este paso porque postgres puede tardar en migrar
--    tablas grandes — el default ya garantiza que los nuevos vienen con valor.
ALTER TABLE public.t_pois
    ADD COLUMN status smallint DEFAULT 1;

-- 2. Forzar NOT NULL una vez todos los registros tienen valor (gracias al
--    DEFAULT del paso anterior). Esto previene que cualquier INSERT futuro
--    omita el status y deje un POI en estado ambiguo.
ALTER TABLE public.t_pois
    ALTER COLUMN status SET NOT NULL;

-- 3. Índice parcial para acelerar el listado de POIs activos por empresa.
--    Es la query más frecuente (cada vez que se carga el catálogo) y este
--    índice excluye los POIs "eliminados" (status=0), que típicamente serán
--    una minoría con el tiempo. Más eficiente que un índice completo.
CREATE INDEX IF NOT EXISTS idx_t_pois_empresa_status
    ON public.t_pois (id_empresa)
    WHERE status = 1;

-- 4. Comentario en la columna para que cualquier desarrollador que inspeccione
--    la tabla con \d+ t_pois sepa qué significa cada valor sin tener que
--    leer el código.
COMMENT ON COLUMN public.t_pois.status IS
    'Soft-delete flag: 1 = activo (visible y editable), 0 = eliminado lógicamente.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Permiso 'unidades.eliminar' (no existía).
-- ─────────────────────────────────────────────────────────────────────────────
-- Necesario para que el endpoint DELETE /units/<id> tenga un permiso que
-- validar. Sin esto, el decorador @permiso_required("unidades.eliminar")
-- siempre rechazaría con 403 porque la clave no existe en t_permisos.
--
-- ON CONFLICT DO NOTHING para que la migración sea idempotente: si ya
-- alguien insertó el permiso manualmente, no falla.
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status)
VALUES (
    'unidades.eliminar',
    'Eliminar unidades',
    'unidades',
    'Permite eliminar (soft-delete) unidades de la flota',
    1
)
ON CONFLICT (clave) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Asignar el nuevo permiso al rol admin_empresa.
-- ─────────────────────────────────────────────────────────────────────────────
-- Por consistencia con unidades.editar y unidades.crear que ya están
-- asignados a admin_empresa. El rol sudo_erp tiene bypass automático
-- en código, no necesita asignación explícita.
INSERT INTO public.r_rol_permisos (id_rol, id_permiso)
SELECT
    r.id_rol,
    p.id_permiso
FROM public.t_roles r
CROSS JOIN public.t_permisos p
WHERE r.clave = 'admin_empresa'
  AND p.clave = 'unidades.eliminar'
ON CONFLICT (id_rol, id_permiso) DO NOTHING;

COMMIT;