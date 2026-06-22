-- ════════════════════════════════════════════════════════════════════════════
-- 025_create_t_unidades_token.sql
-- ════════════════════════════════════════════════════════════════════════════
--
-- DECISIÓN DE DISEÑO — tabla separada:
--   Igual que el token de cliente vive en su propia tabla y no como columnas de
--   t_clientes, el token de unidad vive aquí y no engorda t_unidades (que ya
--   ronda las 45 columnas). Relación 1:1 con la unidad vía PK = id_unidad.
--
-- ALCANCE:
--   Esta primera versión cubre solo el rastreo de posición. Los campos de clave
--   de acceso se crean ahora (para no migrar de nuevo después) pero la
--   verificación de la clave se implementará en una iteración posterior.
--
-- IDEMPOTENTE:
--   CREATE TABLE IF NOT EXISTS + índices IF NOT EXISTS. Correrla varias veces
--   no causa daño. Reversible con DROP TABLE t_unidades_token.
-- ════════════════════════════════════════════════════════════════════════════

BEGIN;

-- ─── Tabla de configuración del token de rastreo de unidad ───────────────────

CREATE TABLE IF NOT EXISTS t_unidades_token (
    id_unidad                   INTEGER PRIMARY KEY
                                REFERENCES t_unidades (id_unidad) ON DELETE CASCADE,
    id_empresa                  INTEGER NOT NULL
                                REFERENCES t_empresas (id_empresa),

    -- Acceso y token
    acceso_token_rastreo        BOOLEAN NOT NULL DEFAULT FALSE,
    token                       VARCHAR(15),

    -- Clave de acceso opcional (6 dígitos). Campos listos; la verificación se
    -- implementará en una iteración posterior.
    token_requiere_clave_acceso BOOLEAN NOT NULL DEFAULT FALSE,
    token_clave_acceso          VARCHAR(6),

    -- Caducidad del token. NULL = permanente (comportamiento actual). Cuando se
    -- implemente la expiración, el rastreo público rechazará tokens cuya
    -- fecha_expiracion ya pasó. La v2.5 ofrece 1/2/4/8/12/24h o permanente.
    fecha_expiracion            TIMESTAMP,

    -- Auditoría mínima: cuándo se creó la fila y cuándo se regeneró el token.
    -- UTC-6 como el resto del pipeline (ver migración 015).
    fecha_creacion              TIMESTAMP NOT NULL
                                DEFAULT (NOW() AT TIME ZONE 'America/Mexico_City'),
    fecha_actualizacion         TIMESTAMP NOT NULL
                                DEFAULT (NOW() AT TIME ZONE 'America/Mexico_City')
);

-- Búsqueda por empresa (listados y validación de pertenencia).
CREATE INDEX IF NOT EXISTS idx_t_unidades_token_id_empresa
    ON t_unidades_token (id_empresa);

-- Índice único parcial sobre el token. El rastreo público resuelve la unidad a
-- partir del token, así que esta búsqueda debe ser rápida y el token irrepetible.
-- Parcial (WHERE token IS NOT NULL) porque una fila puede existir sin token aún.
CREATE UNIQUE INDEX IF NOT EXISTS idx_t_unidades_token_token
    ON t_unidades_token (token)
    WHERE token IS NOT NULL;

COMMIT;