-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 009: crear tabla de control de migraciones
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Hasta ahora las migraciones se aplicaban manualmente sin registro de cuáles
--   ya estaban en cada entorno (local, servidor). Esto generó el problema del
--   deploy del 2026-06-08 donde t_rutas no existía en producción.
--
--   Esta migración crea la tabla schema_migrations que registra cada archivo
--   aplicado junto con su timestamp y checksum. A partir de aquí, el script
--   migrate.py usa esta tabla para saber qué migraciones están pendientes y
--   aplica solo las que faltan, en orden.
--
-- Idempotencia:
--   CREATE TABLE IF NOT EXISTS — seguro de correr más de una vez.
--
-- Cómo aplicar (esta migración es la única que se aplica manualmente):
--   podman exec -i centralgo_db_1 psql -U postgres -d centralgps_project \
--     < migrations/009_create_schema_migrations.sql
--
--   Todas las migraciones posteriores se aplican con:
--   podman exec centralgo_api_1 python migrate.py
--
-- Cómo revertir:
--   DROP TABLE IF EXISTS public.schema_migrations;
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- Tabla de control de migraciones.
-- Cada fila representa un archivo SQL que ya fue aplicado exitosamente.
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    -- Nombre del archivo de migración, ej: "008_create_t_rutas.sql"
    -- Es la clave primaria — no puede aplicarse el mismo archivo dos veces.
    filename        varchar(255)  NOT NULL PRIMARY KEY,

    -- SHA-256 del contenido del archivo al momento de aplicarse.
    -- Sirve para detectar si un archivo ya aplicado fue modificado
    -- accidentalmente después del deploy.
    checksum        varchar(64)   NOT NULL,

    -- Fecha y hora exacta en que se aplicó la migración (UTC).
    applied_at      timestamp     NOT NULL DEFAULT NOW(),

    -- Tiempo que tardó en ejecutarse el archivo (en milisegundos).
    -- Útil para detectar migraciones lentas en producción.
    duration_ms     integer       NOT NULL DEFAULT 0
);

COMMENT ON TABLE public.schema_migrations IS
    'Registro de migraciones SQL aplicadas. Manejado por migrate.py — no editar manualmente.';

COMMENT ON COLUMN public.schema_migrations.filename IS
    'Nombre del archivo SQL aplicado. Debe coincidir exactamente con el archivo en migrations/.';

COMMENT ON COLUMN public.schema_migrations.checksum IS
    'SHA-256 del contenido del archivo. Se verifica en cada run para detectar modificaciones post-deploy.';

-- Índice por fecha para consultas de historial ordenado cronológicamente.
CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON public.schema_migrations (applied_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- Registrar las migraciones 001-008 como ya aplicadas.
--
-- Estas migraciones ya existen en todos los entornos (se aplicaron manualmente
-- antes de que existiera este sistema de control). Las registramos con
-- checksum='legacy' para indicar que no fueron verificadas al momento de
-- aplicarse — solo que se sabe que están en la BD.
--
-- Si en algún entorno alguna de estas NO está aplicada, migrate.py la
-- detectará porque el archivo existe en migrations/ pero no en esta tabla,
-- y la aplicará automáticamente.
-- ─────────────────────────────────────────────────────────────────────────────
INSERT INTO public.schema_migrations (filename, checksum, applied_at, duration_ms)
VALUES
    ('001_add_status_to_pois.sql',              'legacy', NOW(), 0),
    ('002_seed_legacy_permissions.sql',          'legacy', NOW(), 0),
    ('003_add_status_to_pois.sql',              'legacy', NOW(), 0),
    ('004_migrate_role_permissions_to_users.sql','legacy', NOW(), 0),
    ('005_create_r_poi_unidades.sql',            'legacy', NOW(), 0),
    ('006_create_t_alertas_poi.sql',             'legacy', NOW(), 0),
    ('007_add_global_speed_events.sql',          'legacy', NOW(), 0),
    ('008_create_t_rutas.sql',                   'legacy', NOW(), 0),
    ('009_create_schema_migrations.sql',         'legacy', NOW(), 0)
ON CONFLICT (filename) DO NOTHING;

COMMIT;