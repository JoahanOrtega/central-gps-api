--   1. RENOMBRES: UPDATE sobre t_permisos.clave conserva id_permiso, por lo
--      que TODAS las asignaciones existentes en r_rol_permisos y
--      r_usuario_permisos siguen vigentes sin tocarlas.
--   2. cund2 duplicaba "Editar unidades" con cund_edit: sus asignaciones se
--      copian a unidades.editar y la clave se desactiva (status=0) —
--      histórico intacto, catálogo sin duplicados.
--   3. INSERTS: claves que el código ya exige y no tenían equivalente
--      legacy. El módulo se hereda de una clave hermana para respetar la
--      agrupación del wizard, y se conceden por backfill a los roles y
--      usuarios que ya tienen la hermana (no cambian permisos efectivos:
--      quien podía editar, ahora también puede crear, que era el
--      comportamiento implícito antes de que existiera la clave).

BEGIN;

-- ─── 1. Renombres: legacy → moderna (conservan asignaciones) ────────────────
UPDATE t_permisos SET clave = 'unidades.ver'      WHERE clave = 'cund1';
UPDATE t_permisos SET clave = 'unidades.crear'    WHERE clave = 'cund3';

-- cund_edit además traía modulo='catalogos' (el grupo fantasma "catalogos"
-- del wizard) y descripción con mojibake — se corrigen en el mismo paso.
UPDATE t_permisos
   SET clave = 'unidades.editar',
       modulo = 'unidades',
       descripcion = 'Permite editar datos de unidades existentes. El '
           || 'sudo_erp puede editar todos los campos incluido equipo '
           || 'instalado; admin_empresa y usuario pueden editar solo datos '
           || 'operativos (operador, combustible, seguro, verificación).'
 WHERE clave = 'cund_edit';

UPDATE t_permisos SET clave = 'clientes.ver'      WHERE clave = 'cclt1';
UPDATE t_permisos SET clave = 'pois.ver'          WHERE clave = 'cpoi1';

-- crep1: renombre + descripción con mojibake corregida
UPDATE t_permisos
   SET clave = 'reportes.ver',
       descripcion = 'Permite visualizar el módulo de reportes'
 WHERE clave = 'crep1';

-- Estandarización borrar → eliminar en los módulos que el código nuevo ya
-- usa con .eliminar (verificado: el backend no referencia estos .borrar).
UPDATE t_permisos SET clave = 'aforos.eliminar'   WHERE clave = 'aforos.borrar';
UPDATE t_permisos SET clave = 'clientes.eliminar' WHERE clave = 'clientes.borrar';
UPDATE t_permisos SET clave = 'rutas.eliminar'    WHERE clave = 'rutas.borrar';

-- ─── 2. cund2: duplicado semántico de "Editar unidades" ─────────────────────
-- Copiar sus asignaciones de rol hacia unidades.editar (sin duplicar)...
INSERT INTO r_rol_permisos (id_rol, id_permiso)
SELECT rp.id_rol, dest.id_permiso
FROM r_rol_permisos rp
JOIN t_permisos org  ON org.id_permiso = rp.id_permiso AND org.clave = 'cund2'
JOIN t_permisos dest ON dest.clave = 'unidades.editar'
WHERE NOT EXISTS (
    SELECT 1 FROM r_rol_permisos x
    WHERE x.id_rol = rp.id_rol AND x.id_permiso = dest.id_permiso
);

-- ...y las de usuario
INSERT INTO r_usuario_permisos
    (id_usuario, id_empresa, id_permiso, id_usuario_registro, fecha_registro)
SELECT up.id_usuario, up.id_empresa, dest.id_permiso,
       up.id_usuario_registro, now() AT TIME ZONE 'America/Mexico_City'
FROM r_usuario_permisos up
JOIN t_permisos org  ON org.id_permiso = up.id_permiso AND org.clave = 'cund2'
JOIN t_permisos dest ON dest.clave = 'unidades.editar'
WHERE NOT EXISTS (
    SELECT 1 FROM r_usuario_permisos x
    WHERE x.id_usuario = up.id_usuario
      AND x.id_empresa = up.id_empresa
      AND x.id_permiso = dest.id_permiso
);

UPDATE t_permisos SET status = 0 WHERE clave = 'cund2';

-- ─── 3. Claves nuevas sin equivalente legacy ────────────────────────────────
INSERT INTO t_permisos (clave, nombre, modulo, descripcion, status)
SELECT v.clave, v.nombre, p.modulo, v.descripcion, 1
FROM (VALUES
    ('aforos.crear',        'Crear aforos',        'aforos.editar',
     'Permite crear registros de aforo'),
    ('clientes.crear',      'Crear clientes',      'clientes.editar',
     'Permite crear clientes'),
    ('rutas.crear',         'Crear rutas',         'rutas.editar',
     'Permite crear rutas'),
    ('cumplimiento.ver',    'Ver cumplimiento',    'cumplimiento.monitor',
     'Permite visualizar el módulo de cumplimiento'),
    ('cumplimiento.editar', 'Editar cumplimiento', 'cumplimiento.habilitar',
     'Permite editar la configuración de cumplimiento')
) AS v(clave, nombre, hermana, descripcion)
JOIN t_permisos p ON p.clave = v.hermana
WHERE NOT EXISTS (SELECT 1 FROM t_permisos e WHERE e.clave = v.clave);

-- Backfill de roles: conceder cada clave nueva a los roles con su hermana
INSERT INTO r_rol_permisos (id_rol, id_permiso)
SELECT rp.id_rol, nueva.id_permiso
FROM (VALUES
    ('aforos.crear',        'aforos.editar'),
    ('clientes.crear',      'clientes.editar'),
    ('rutas.crear',         'rutas.editar'),
    ('cumplimiento.ver',    'cumplimiento.monitor'),
    ('cumplimiento.editar', 'cumplimiento.habilitar')
) AS m(clave_nueva, clave_hermana)
JOIN t_permisos nueva ON nueva.clave = m.clave_nueva
JOIN t_permisos herm  ON herm.clave  = m.clave_hermana
JOIN r_rol_permisos rp ON rp.id_permiso = herm.id_permiso
WHERE NOT EXISTS (
    SELECT 1 FROM r_rol_permisos x
    WHERE x.id_rol = rp.id_rol AND x.id_permiso = nueva.id_permiso
);

-- Backfill de usuarios con permisos específicos
INSERT INTO r_usuario_permisos
    (id_usuario, id_empresa, id_permiso, id_usuario_registro, fecha_registro)
SELECT up.id_usuario, up.id_empresa, nueva.id_permiso,
       up.id_usuario_registro, now() AT TIME ZONE 'America/Mexico_City'
FROM (VALUES
    ('aforos.crear',        'aforos.editar'),
    ('clientes.crear',      'clientes.editar'),
    ('rutas.crear',         'rutas.editar'),
    ('cumplimiento.ver',    'cumplimiento.monitor'),
    ('cumplimiento.editar', 'cumplimiento.habilitar')
) AS m(clave_nueva, clave_hermana)
JOIN t_permisos nueva ON nueva.clave = m.clave_nueva
JOIN t_permisos herm  ON herm.clave  = m.clave_hermana
JOIN r_usuario_permisos up ON up.id_permiso = herm.id_permiso
WHERE NOT EXISTS (
    SELECT 1 FROM r_usuario_permisos x
    WHERE x.id_usuario = up.id_usuario
      AND x.id_empresa = up.id_empresa
      AND x.id_permiso = nueva.id_permiso
);

COMMIT;