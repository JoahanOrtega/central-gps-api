-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 002: seed completo del catálogo de permisos del sistema legacy
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   El catálogo actual de t_permisos tiene solo 8 entradas básicas. El sistema
--   legacy (que estamos migrando) maneja ~96 permisos granulares por módulo.
--   Esta migración inserta todos los permisos faltantes para que el wizard
--   "Nuevo usuario" del Panel ERP pueda mostrarlos como checkboxes.
--
--   IMPORTANTE: insertar el permiso aquí NO activa la funcionalidad — solo
--   habilita el checkbox en la UI. Los módulos que aún no se han migrado
--   (Aforos, Rutas, Itinerarios, etc.) tendrán sus permisos en BD pero sin
--   código que los valide. Cuando se migre cada módulo, su backend usará
--   @permiso_required("modulo.accion") y la cadena se cierra.
--
-- Convención de claves:
--   <modulo>.<accion>
--   <modulo>: nombre singular del recurso (unidades, pois, rutas, etc.)
--   <accion>: verbo o sustantivo en snake_case (ver, editar, video_vivo)
--
-- Idempotencia:
--   ON CONFLICT (clave) DO NOTHING en cada INSERT. Si la migración se
--   ejecuta dos veces, los duplicados se ignoran. Útil si el equipo aplica
--   por error la misma migración en distintos entornos.
--
-- Asignación al rol admin_empresa:
--   Al final del archivo se hace un INSERT masivo en r_rol_permisos para
--   que el rol admin_empresa herede TODOS los permisos nuevos. Esto
--   replica el comportamiento del legacy donde el "Tipo de Acceso: Acceso
--   Total" entrega todos los permisos.
--
--   NO se asigna al rol "usuario" — ese rol parte de cero permisos y es
--   el sudo_erp quien decide cuáles asignar al crear cada usuario.
--
-- Cómo aplicar:
--   psql -U <usuario> -d <db> -f migrations/002_seed_legacy_permissions.sql
--
-- Cómo revertir (¡cuidado: borra los permisos!):
--   DELETE FROM public.t_permisos
--    WHERE clave LIKE 'dashboard.%'
--       OR clave LIKE 'mapa.%'
--       OR clave LIKE 'unidades.%'
--       OR clave LIKE 'clientes.%'
--       OR clave LIKE 'terminales.%'
--       OR clave LIKE 'operadores.%'
--       OR clave LIKE 'pois.%'
--       OR clave LIKE 'gasolineras.%'
--       OR clave LIKE 'usuarios.%'
--       OR clave LIKE 'rutas.%'
--       OR clave LIKE 'itinerarios.%'
--       OR clave LIKE 'roles_itin.%'
--       OR clave LIKE 'cumplimiento.%'
--       OR clave LIKE 'aforos.%'
--       OR clave LIKE 'turnos_cliente.%'
--       OR clave LIKE 'semaforos.%'
--       OR clave LIKE 'monitor_sem.%'
--       OR clave LIKE 'hist_cumplim.%'
--       OR clave LIKE 'cargas.%'
--       OR clave LIKE 'tickets.%'
--       OR clave LIKE 'rep.%';
--   (las relaciones en r_rol_permisos se borran automáticamente por la FK)
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ─── GENERAL: Dashboard ──────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('dashboard.ver',                'Ver dashboard',                          'dashboard', 'Acceso a la vista principal del dashboard',                  1),
  ('dashboard.widgets_resumen',    'Ver widgets de resumen',                 'dashboard', 'Visualizar tarjetas de resumen del dashboard',                1),
  ('dashboard.kilometros',         'Ver gráfica de kilómetros',              'dashboard', 'Visualizar gráfica de kilómetros recorridos',                 1),
  ('dashboard.utilizacion',        'Ver gráfica de tiempo de utilización',   'dashboard', 'Visualizar gráfica de tiempo de utilización por unidad',      1),
  ('dashboard.excesos_velocidad',  'Ver gráfica de excesos de velocidad',    'dashboard', 'Visualizar gráfica de excesos de velocidad',                  1)
ON CONFLICT (clave) DO NOTHING;

-- ─── GENERAL: Mapa ───────────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('mapa.ver',                'Ver mapa',                  'mapa', 'Acceso a la vista del mapa con unidades en tiempo real', 1),
  ('mapa.recorridos',         'Consultar recorridos',      'mapa', 'Consultar el recorrido histórico de unidades en el mapa', 1),
  ('mapa.excesos_velocidad',  'Ver excesos de velocidad',  'mapa', 'Visualizar excesos de velocidad sobre el mapa',           1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Unidades ─────────────────────────────────────────────────────
-- Nota: unidades.ver, unidades.editar, unidades.crear y unidades.eliminar ya
-- existen en el catálogo desde la migración 001 — ON CONFLICT los respeta.
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('unidades.alertas',         'Configurar alertas de unidad',         'unidades', 'Configurar y gestionar alertas operativas de la unidad',        1),
  ('unidades.comandos',        'Enviar comandos',                       'unidades', 'Enviar comandos remotos al equipo AVL de la unidad',           1),
  ('unidades.video_vivo',      'Ver video en vivo',                     'unidades', 'Visualizar transmisión de video en vivo de la unidad',         1),
  ('unidades.video_historico', 'Consultar video histórico',             'unidades', 'Consultar grabaciones históricas de la unidad',                 1),
  ('unidades.snapshots',       'Consultar snapshots',                   'unidades', 'Consultar imágenes capturadas (snapshots) de la unidad',       1),
  ('unidades.token_rastreo',   'Gestionar token de rastreo',            'unidades', 'Crear, leer, actualizar y borrar tokens públicos de rastreo',  1),
  ('unidades.grupos',          'Gestionar grupos de unidades',          'unidades', 'CRUD sobre grupos de unidades',                                  1),
  ('unidades.exportar',        'Exportar unidades',                     'unidades', 'Exportar listado de unidades a archivo',                        1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Clientes ─────────────────────────────────────────────────────
-- Nota: clientes.ver ya existe.
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('clientes.editar',            'Agregar/editar clientes',           'clientes', 'Crear nuevos clientes o editar existentes',                  1),
  ('clientes.borrar',            'Borrar clientes',                    'clientes', 'Eliminar (soft-delete) clientes del catálogo',              1),
  ('clientes.alertas',           'Configurar alertas de cliente',     'clientes', 'Configurar alertas relacionadas con un cliente',             1),
  ('clientes.exportar',          'Exportar clientes',                  'clientes', 'Exportar listado de clientes a archivo',                    1),
  ('clientes.token_rastreo',     'Gestionar token de rastreo',        'clientes', 'Generar tokens de rastreo público por cliente',              1),
  ('clientes.cumplimiento',      'Dashboard de cumplimiento',          'clientes', 'Acceso al dashboard de cumplimiento del cliente',           1),
  ('clientes.turnos_servicios',  'Turnos de servicios',                'clientes', 'Gestionar turnos de servicios por cliente',                 1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Terminales ───────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('terminales.ver',       'Ver terminales',              'terminales', 'Consultar el catálogo de terminales',         1),
  ('terminales.editar',    'Agregar/editar terminales',   'terminales', 'Crear o editar terminales',                    1),
  ('terminales.borrar',    'Borrar terminales',           'terminales', 'Eliminar terminales del catálogo',             1),
  ('terminales.alertas',   'Configurar alertas',          'terminales', 'Configurar alertas en terminales',             1),
  ('terminales.exportar',  'Exportar terminales',         'terminales', 'Exportar listado de terminales a archivo',    1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Operadores ───────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('operadores.ver',       'Ver operadores',              'operadores', 'Consultar el catálogo de operadores',          1),
  ('operadores.editar',    'Agregar/editar operadores',   'operadores', 'Crear o editar operadores',                     1),
  ('operadores.borrar',    'Borrar operadores',           'operadores', 'Eliminar operadores del catálogo',              1),
  ('operadores.alertas',   'Configurar alertas',          'operadores', 'Configurar alertas relacionadas con operadores',1),
  ('operadores.grupos',    'Gestionar grupos',            'operadores', 'CRUD sobre grupos de operadores',               1),
  ('operadores.exportar',  'Exportar operadores',         'operadores', 'Exportar listado de operadores a archivo',     1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: POIs ─────────────────────────────────────────────────────────
-- Nota: pois.ver ya existe.
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('pois.editar',     'Agregar/editar POIs',           'pois', 'Crear nuevos POIs o editar existentes',     1),
  ('pois.borrar',     'Borrar POIs',                    'pois', 'Eliminar (soft-delete) POIs del catálogo', 1),
  ('pois.alertas',    'Configurar alertas',             'pois', 'Configurar alertas en POIs',                1),
  ('pois.grupos',     'Gestionar grupos de POIs',       'pois', 'CRUD sobre grupos de POIs',                  1),
  ('pois.exportar',   'Exportar POIs',                  'pois', 'Exportar listado de POIs a archivo',        1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Gasolineras ──────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('gasolineras.ver',       'Ver gasolineras',              'gasolineras', 'Consultar el catálogo de gasolineras',           1),
  ('gasolineras.editar',    'Agregar/editar gasolineras',   'gasolineras', 'Crear o editar gasolineras',                      1),
  ('gasolineras.borrar',    'Borrar gasolineras',           'gasolineras', 'Eliminar gasolineras del catálogo',               1),
  ('gasolineras.alertas',   'Configurar alertas',           'gasolineras', 'Configurar alertas relacionadas con gasolineras', 1),
  ('gasolineras.grupos',    'Gestionar grupos',             'gasolineras', 'CRUD sobre grupos de gasolineras',                1),
  ('gasolineras.exportar',  'Exportar gasolineras',         'gasolineras', 'Exportar listado de gasolineras a archivo',      1)
ON CONFLICT (clave) DO NOTHING;

-- ─── CATÁLOGOS: Usuarios ─────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('usuarios.ver',          'Ver usuarios',          'usuarios', 'Consultar el catálogo de usuarios de la empresa',     1),
  ('usuarios.editar',       'Agregar/editar usuarios','usuarios', 'Crear nuevos usuarios o editar existentes',           1),
  ('usuarios.inhabilitar',  'Inhabilitar usuarios',  'usuarios', 'Suspender/reactivar usuarios sin eliminarlos',         1),
  ('usuarios.exportar',     'Exportar usuarios',     'usuarios', 'Exportar listado de usuarios a archivo',               1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Rutas ────────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('rutas.ver',       'Ver rutas',                'rutas', 'Consultar el catálogo de rutas',          1),
  ('rutas.editar',    'Agregar/editar rutas',     'rutas', 'Crear o editar rutas',                     1),
  ('rutas.borrar',    'Borrar rutas',             'rutas', 'Eliminar rutas del catálogo',              1),
  ('rutas.alertas',   'Configurar alertas',       'rutas', 'Configurar alertas en rutas',              1),
  ('rutas.exportar',  'Exportar rutas',           'rutas', 'Exportar listado de rutas a archivo',     1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Itinerarios ──────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('itinerarios.ver',       'Ver itinerarios',                 'itinerarios', 'Consultar el catálogo de itinerarios',         1),
  ('itinerarios.editar',    'Agregar/editar itinerarios',      'itinerarios', 'Crear o editar itinerarios',                    1),
  ('itinerarios.grupos',    'Gestionar grupos de itinerarios', 'itinerarios', 'CRUD sobre grupos de itinerarios',              1),
  ('itinerarios.alertas',   'Configurar alertas',              'itinerarios', 'Configurar alertas en itinerarios',             1),
  ('itinerarios.borrar',    'Borrar itinerarios',              'itinerarios', 'Eliminar itinerarios del catálogo',             1),
  ('itinerarios.exportar',  'Exportar itinerarios',            'itinerarios', 'Exportar listado de itinerarios a archivo',    1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Roles de itinerario ──────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('roles_itin.ver',         'Ver roles de itinerario',           'roles_itin', 'Consultar roles de itinerario',                       1),
  ('roles_itin.editar',      'Agregar/editar rol',                'roles_itin', 'Crear o editar roles de itinerario',                   1),
  ('roles_itin.asignar',     'Asignar roles',                     'roles_itin', 'Asignar roles de itinerario a unidades u operadores', 1),
  ('roles_itin.borrar',      'Borrar roles',                      'roles_itin', 'Eliminar roles de itinerario',                         1),
  ('roles_itin.exportar',    'Exportar',                          'roles_itin', 'Exportar listado de roles a archivo',                  1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Cumplimiento de itinerarios ──────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('cumplimiento.invitado',           'Modo invitado',                          'cumplimiento', 'Acceder al monitor de cumplimiento como invitado',        1),
  ('cumplimiento.monitor',            'Modo monitor',                           'cumplimiento', 'Acceder al monitor con privilegios completos',             1),
  ('cumplimiento.asignar_titulares',  'Asignar unidades titulares',             'cumplimiento', 'Asignar unidades titulares a un servicio',                 1),
  ('cumplimiento.asignar_apoyo',      'Asignar unidades de apoyo',              'cumplimiento', 'Asignar unidades de apoyo a un servicio',                  1),
  ('cumplimiento.asignar_ejecucion',  'Asignar durante ejecución',              'cumplimiento', 'Permitir asignación de unidades durante la ejecución',     1),
  ('cumplimiento.habilitar',          'Habilitar/inhabilitar servicio',         'cumplimiento', 'Habilitar o inhabilitar un servicio en el monitor',        1),
  ('cumplimiento.alertas',            'Configurar alertas y monitoreo',         'cumplimiento', 'Configurar alertas y parámetros de monitoreo',             1),
  ('cumplimiento.token',              'Token de rastreo de cumplimiento',       'cumplimiento', 'Generar token de rastreo para servicios de cumplimiento',  1),
  ('cumplimiento.exportar',           'Exportar',                                'cumplimiento', 'Exportar datos de cumplimiento a archivo',                 1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Aforos ───────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('aforos.ver',       'Ver aforos',                 'aforos', 'Consultar el catálogo de aforos',           1),
  ('aforos.editar',    'Agregar/editar aforos',      'aforos', 'Crear o editar aforos',                      1),
  ('aforos.grupos',    'Gestionar grupos de aforos', 'aforos', 'CRUD sobre grupos de aforos',                1),
  ('aforos.borrar',    'Borrar aforos',              'aforos', 'Eliminar aforos del catálogo',               1),
  ('aforos.exportar',  'Exportar aforos',            'aforos', 'Exportar listado de aforos a archivo',       1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Turnos de servicios cliente ──────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('turnos_cliente.ver',       'Ver turnos de servicios',           'turnos_cliente', 'Consultar turnos de servicios de cliente',        1),
  ('turnos_cliente.editar',    'Agregar/editar turnos',             'turnos_cliente', 'Crear o editar turnos de servicios',              1),
  ('turnos_cliente.borrar',    'Borrar turnos',                     'turnos_cliente', 'Eliminar turnos de servicios',                    1),
  ('turnos_cliente.exportar',  'Exportar',                          'turnos_cliente', 'Exportar listado de turnos a archivo',           1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Semáforos ────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('semaforos.ver',       'Ver semáforos',                  'semaforos', 'Consultar el catálogo de semáforos',     1),
  ('semaforos.editar',    'Agregar/editar semáforos',       'semaforos', 'Crear o editar semáforos',                1),
  ('semaforos.grupos',    'Gestionar grupos de semáforos',  'semaforos', 'CRUD sobre grupos de semáforos',          1),
  ('semaforos.borrar',    'Borrar semáforos',               'semaforos', 'Eliminar semáforos del catálogo',         1),
  ('semaforos.exportar',  'Exportar semáforos',             'semaforos', 'Exportar listado de semáforos a archivo', 1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Monitor de Semáforos ─────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('monitor_sem.titulares',  'Asignar unidades titulares',         'monitor_sem', 'Asignar unidades titulares en monitor de semáforos',   1),
  ('monitor_sem.apoyo',      'Asignar unidades de apoyo',          'monitor_sem', 'Asignar unidades de apoyo en monitor de semáforos',    1),
  ('monitor_sem.ejecucion',  'Asignar durante ejecución',          'monitor_sem', 'Asignar unidades durante ejecución del servicio',      1),
  ('monitor_sem.habilitar',  'Habilitar/inhabilitar servicio',     'monitor_sem', 'Habilitar o inhabilitar servicio en el monitor',        1),
  ('monitor_sem.alertas',    'Configurar alertas y monitoreo',     'monitor_sem', 'Configurar alertas y parámetros de monitoreo',          1),
  ('monitor_sem.exportar',   'Exportar',                            'monitor_sem', 'Exportar datos del monitor de semáforos',              1)
ON CONFLICT (clave) DO NOTHING;

-- ─── OPERACIÓN: Historial de cumplimiento ────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('hist_cumplim.ver',       'Ver historial de cumplimiento', 'hist_cumplim', 'Consultar el historial de cumplimiento',      1),
  ('hist_cumplim.exportar',  'Exportar historial',            'hist_cumplim', 'Exportar historial de cumplimiento a archivo',1)
ON CONFLICT (clave) DO NOTHING;

-- ─── COMBUSTIBLE: Cargas ─────────────────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('cargas.ver',       'Ver cargas de combustible',          'cargas', 'Consultar cargas de combustible',                  1),
  ('cargas.editar',    'Agregar/editar cargas',              'cargas', 'Registrar o editar cargas de combustible',         1),
  ('cargas.alertas',   'Configurar alertas',                 'cargas', 'Configurar alertas de cargas',                      1),
  ('cargas.borrar',    'Borrar cargas',                      'cargas', 'Eliminar cargas de combustible',                    1),
  ('cargas.exportar',  'Exportar cargas',                    'cargas', 'Exportar listado de cargas a archivo',              1)
ON CONFLICT (clave) DO NOTHING;

-- ─── COMBUSTIBLE: Tickets de báscula ─────────────────────────────────────────
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('tickets.ver',       'Ver tickets de báscula',     'tickets', 'Consultar tickets de báscula',                 1),
  ('tickets.editar',    'Agregar/editar tickets',     'tickets', 'Registrar o editar tickets de báscula',         1),
  ('tickets.alertas',   'Configurar alertas',         'tickets', 'Configurar alertas de tickets',                  1),
  ('tickets.borrar',    'Borrar tickets',             'tickets', 'Eliminar tickets de báscula',                    1),
  ('tickets.exportar',  'Exportar tickets',           'tickets', 'Exportar listado de tickets a archivo',          1)
ON CONFLICT (clave) DO NOTHING;

-- ─── REPORTES ────────────────────────────────────────────────────────────────
-- Nota: reportes.ver ya existe (permiso general de acceso al módulo).
-- Estos son reportes ESPECÍFICOS que el usuario puede o no consultar.
INSERT INTO public.t_permisos (clave, nombre, modulo, descripcion, status) VALUES
  ('rep.eventos',                'Reporte de eventos',                          'reportes', 'Consultar reporte de eventos',                                  1),
  ('rep.km_recorridos',          'Reporte de kilómetros recorridos',            'reportes', 'Consultar reporte de kilómetros recorridos',                    1),
  ('rep.recorridos_pp',          'Reporte de recorridos punto a punto',         'reportes', 'Consultar reporte de recorridos punto a punto',                  1),
  ('rep.detalle_recorridos',     'Reporte de detalle de recorridos',            'reportes', 'Consultar reporte detallado de recorridos',                      1),
  ('rep.recorridos_llegadas',    'Reporte de recorridos y llegadas a ubicación','reportes', 'Consultar reporte de recorridos y llegadas a ubicación',         1),
  ('rep.ultimas_llegadas',       'Reporte de últimas llegadas a ubicación',     'reportes', 'Consultar reporte de últimas llegadas a ubicación',              1),
  ('rep.trafico_ubicacion',      'Reporte de tráfico en ubicación',              'reportes', 'Consultar reporte de tráfico en una ubicación',                  1),
  ('rep.llegadas_salidas',       'Reporte de llegadas y salidas de ubicación',  'reportes', 'Consultar reporte de llegadas y salidas',                        1),
  ('rep.aforos',                 'Reporte de aforos',                            'reportes', 'Consultar reporte de aforos',                                    1),
  ('rep.km_vacio_vs_servicio',   'Reporte km vacío vs km servicio',              'reportes', 'Consultar reporte comparativo de km vacíos vs servicio',         1),
  ('rep.excesos_velocidad',      'Reporte de excesos de velocidad',              'reportes', 'Consultar reporte de excesos de velocidad',                      1)
ON CONFLICT (clave) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- Asignar TODOS los permisos al rol admin_empresa.
-- ─────────────────────────────────────────────────────────────────────────────
-- El rol admin_empresa representa "Acceso Total" del legacy. Le damos
-- todos los permisos del catálogo.
--
-- Cada vez que se ejecute esta migración (o se agreguen permisos en otra
-- migración futura), este INSERT cubre los nuevos permisos sin tocar
-- los existentes — gracias al ON CONFLICT.
--
-- El rol sudo_erp NO necesita asignación: el decorador @permiso_required
-- ya tiene bypass por rol, no consulta r_rol_permisos.
INSERT INTO public.r_rol_permisos (id_rol, id_permiso)
SELECT
    r.id_rol,
    p.id_permiso
FROM public.t_roles r
CROSS JOIN public.t_permisos p
WHERE r.clave  = 'admin_empresa'
  AND p.status = 1
ON CONFLICT (id_rol, id_permiso) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────────
-- Verificación post-migración (NO se ejecuta, solo documenta el resultado
-- esperado). Para verificar manualmente después:
-- ─────────────────────────────────────────────────────────────────────────────
--
--   -- ¿Cuántos permisos hay totales?
--   SELECT COUNT(*) FROM t_permisos WHERE status = 1;
--   -- Esperado: ~96 (8 previos + ~88 nuevos)
--
--   -- ¿Cuántos permisos por módulo?
--   SELECT modulo, COUNT(*) FROM t_permisos WHERE status = 1 GROUP BY modulo ORDER BY modulo;
--
--   -- ¿admin_empresa tiene todos los permisos?
--   SELECT COUNT(*) FROM r_rol_permisos rp
--     JOIN t_roles r ON r.id_rol = rp.id_rol
--    WHERE r.clave = 'admin_empresa';
--   -- Esperado: igual que el COUNT total de permisos.

COMMIT;