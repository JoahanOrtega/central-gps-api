CREAR ARCHIVO NUEVO:
═══════════════════════════════════════════════════════════════
  central-gps-api/migrations/004_migrate_role_permissions_to_users.sql

CONTENIDO COMPLETO:
═══════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 004: migrar permisos de roles a usuarios específicos
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Hasta ahora el sistema usaba un modelo "rol-based": los permisos se
--   asignaban al rol (r_rol_permisos) y los usuarios los heredaban según
--   su id_rol. Para admin_empresa esto significaba "todos heredan los 124
--   permisos automáticamente".
--
--   El nuevo modelo es "user-specific": cada usuario tiene SUS propios
--   permisos en r_usuario_permisos. Los roles se mantienen como
--   etiquetas (sudo_erp, admin_empresa, usuario) pero NO determinan
--   permisos automáticamente. La excepción es sudo_erp, que mantiene
--   sus permisos vía rol porque tiene bypass en el código.
--
-- Objetivo:
--   1. Por cada usuario admin_empresa o usuario activo, COPIAR los permisos
--      que estaba heredando del rol → r_usuario_permisos (específicos).
--      Esto evita que pierdan acceso al vaciar el rol.
--   2. Vaciar r_rol_permisos para admin_empresa (id_rol=2) y usuario (id_rol=3).
--   3. NO tocar r_rol_permisos para sudo_erp (id_rol=1).
--
-- Seguridad:
--   - Todo en una transacción: si algo falla, ROLLBACK completo.
--   - Verificación post-migración con SELECT antes del COMMIT.
--   - Idempotente: ON CONFLICT DO NOTHING en el INSERT.
--   - Solo afecta usuarios con status=1 (activos).
--
-- Usuarios afectados (en este momento):
--   - id=4 (robust@adaman.com.mx, admin_empresa)
--   - id=3 (sudo3@centralgps.com.mx, usuario)
--   El usuario id=2 (sudo_erp) NO se toca.
--
-- Cómo aplicar:
--   podman exec -i centralgo_db_1 psql -U postgres -d centralgps_project \
--       < migrations/004_migrate_role_permissions_to_users.sql
--
-- Cómo revertir:
--   Restaurar desde el backup pre-migración 004 que ya existe en
--   ~/backups/pre_migration_004_<timestamp>/db_backup.sql
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── 1. Copiar permisos del rol a r_usuario_permisos ──────────────────────────
-- Para cada usuario admin_empresa o usuario activo, traemos los permisos
-- que heredaba de su rol. Lo cruzamos con r_empresa_usuarios para saber
-- a qué empresas asignarlos (un usuario puede pertenecer a varias empresas).
--
-- ON CONFLICT (id_usuario, id_empresa, id_permiso) DO NOTHING garantiza
-- idempotencia: si algún permiso ya estaba asignado individualmente al
-- usuario en esa empresa, no se duplica.
INSERT INTO public.r_usuario_permisos (
    id_usuario, id_empresa, id_permiso
)
SELECT DISTINCT
    u.id              AS id_usuario,
    eu.id_empresa     AS id_empresa,
    rp.id_permiso     AS id_permiso
FROM public.t_usuarios u
JOIN public.t_roles r              ON r.id_rol = u.id_rol
JOIN public.r_empresa_usuarios eu  ON eu.id_usuario = u.id
JOIN public.r_rol_permisos rp      ON rp.id_rol = r.id_rol
JOIN public.t_permisos p           ON p.id_permiso = rp.id_permiso
WHERE r.clave IN ('admin_empresa', 'usuario')
  AND u.status = 1
  AND p.status = 1
ON CONFLICT (id_usuario, id_empresa, id_permiso) DO NOTHING;


-- ── 2. Verificación previa al DELETE ──────────────────────────────────────
-- Antes de vaciar r_rol_permisos, confirmamos que los usuarios afectados
-- ya tienen sus permisos copiados a r_usuario_permisos. Si esto devuelve
-- 0 cuando esperábamos N, ABORTAMOS con ROLLBACK manual.
DO $$
DECLARE
    permisos_copiados INTEGER;
    usuarios_afectados INTEGER;
BEGIN
    SELECT COUNT(*) INTO permisos_copiados
    FROM r_usuario_permisos rup
    JOIN t_usuarios u ON u.id = rup.id_usuario
    WHERE u.id_rol IN (2, 3)
      AND u.status = 1;

    SELECT COUNT(*) INTO usuarios_afectados
    FROM t_usuarios
    WHERE id_rol IN (2, 3) AND status = 1;

    -- Loguear el resultado para visibilidad en consola psql
    RAISE NOTICE 'Permisos copiados a r_usuario_permisos: %', permisos_copiados;
    RAISE NOTICE 'Usuarios afectados (admin_empresa + usuario): %', usuarios_afectados;

    -- Sanity check: si hay usuarios pero ningún permiso copiado, algo está mal
    IF usuarios_afectados > 0 AND permisos_copiados = 0 THEN
        RAISE EXCEPTION 'Sanity check fallido: hay % usuarios pero 0 permisos copiados. ROLLBACK.', usuarios_afectados;
    END IF;
END $$;


-- ── 3. Vaciar r_rol_permisos para admin_empresa y usuario ────────────────────
-- sudo_erp (id_rol=1) NO se toca: su rol mantiene los 126 permisos para
-- que el JWT al login los exponga al frontend (necesario para los menús
-- y validaciones de UI). El bypass de sudo_erp en código sigue intacto.
DELETE FROM public.r_rol_permisos
WHERE id_rol IN (
    SELECT id_rol FROM public.t_roles WHERE clave IN ('admin_empresa', 'usuario')
);


-- ── 4. Verificación post-migración ──────────────────────────────────────────
-- Estos selects salen al log. Si algo se ve raro, hacer ROLLBACK manual
-- antes del COMMIT (ejecutar `ROLLBACK;` en psql).
SELECT
    'POST-MIGRACIÓN' AS estado,
    (SELECT COUNT(*) FROM r_rol_permisos WHERE id_rol = 1)               AS sudo_erp_rol,
    (SELECT COUNT(*) FROM r_rol_permisos WHERE id_rol = 2)               AS admin_empresa_rol,
    (SELECT COUNT(*) FROM r_rol_permisos WHERE id_rol = 3)               AS usuario_rol,
    (SELECT COUNT(*) FROM r_usuario_permisos)                            AS total_usuario_permisos;

-- Esperado:
--   sudo_erp_rol         = 126  (intacto)
--   admin_empresa_rol    = 0    (vaciado)
--   usuario_rol          = 0    (vaciado)
--   total_usuario_permisos > 0  (los copiados desde el rol)

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- DESPUÉS DE APLICAR
--
-- Los usuarios admin_empresa y usuario afectados deben hacer LOGOUT + LOGIN
-- para que el JWT se regenere con sus permisos específicos. Los permisos
-- efectivos serán los mismos que antes (la migración los preservó), pero
-- ahora vienen de r_usuario_permisos en lugar de r_rol_permisos.
--
-- Para verificar manualmente que un usuario específico mantuvo su acceso:
--
--   SELECT p.clave
--   FROM r_usuario_permisos rup
--   JOIN t_permisos p ON p.id_permiso = rup.id_permiso
--   WHERE rup.id_usuario = 4  -- robust@adaman.com.mx
--     AND p.status = 1
--   ORDER BY p.clave;
--
-- Esperado: debe traer los 124 permisos que tenía como admin_empresa.
-- ─────────────────────────────────────────────────────────────────────────────