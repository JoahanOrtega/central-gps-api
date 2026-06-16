-- =============================================================================
-- Migración 016 — Grupos de Operadores (módulo Catálogos — Operadores)
-- =============================================================================
--
-- Contexto:
--   Prepara la base de datos para el catálogo de operadores (conductores).
--   La tabla t_operadores YA EXISTE en el esquema base; esta migración solo
--   agrega la capa de AGRUPACIÓN, que el catálogo del v2.5 manejaba y que
--   replicamos para tener paridad de funcionalidad.
--
--   GRUPOS DE OPERADORES: agrupación simple del catálogo (como carpetas).
--     Un grupo contiene N operadores. Un operador puede pertenecer a varios
--     grupos (N:M vía r_grupo_operadores_operadores). Se usa para organizar
--     la vista del catálogo y para asignar alertas/permisos por grupo.
--
-- Jerarquía tras esta migración:
--   t_operadores ←→ t_grupos_operadores  (vía r_grupo_operadores_operadores)
--   t_operadores → r_unidad_operador → t_unidades   (ya existente)
--   t_operadores → t_pois (id_poi, geocerca del operador — ya existente)
--
-- Adaptaciones vs esquema v3.0:
--   - id_tenant VARCHAR(50) → id_empresa INTEGER con FK (modelo multiempresa
--     consistente con el resto del sistema, no multi-tenant por string).
--   - operadores (contador) → eliminado; se calcula con COUNT cuando se
--     necesite (misma decisión que t_grupos_itinerarios en migración 011).
--   - r_grupo_operadores_operadores sin PK en v3.0 → aquí PK compuesta para
--     impedir duplicados (un operador no puede estar dos veces en un grupo).
--   - Timestamps en UTC-6 (America/Mexico_City) coherente con t_operadores.
--
-- Cómo aplicar:
--   podman exec centralgo_api_1 python migrate.py
--   docker exec proyecto-api-1  python migrate.py
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- GRUPOS DE OPERADORES
-- Agrupación simple para organizar el catálogo de operadores.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS t_grupos_operadores (
    id_grupo_operadores     SERIAL PRIMARY KEY,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    nombre                  VARCHAR(150) NOT NULL,
    observaciones           TEXT DEFAULT '',
    fecha_registro          TIMESTAMP WITHOUT TIME ZONE
                                DEFAULT (now() AT TIME ZONE 'America/Mexico_City'),
    id_usuario_registro     INTEGER,
    fecha_cambio            TIMESTAMP WITHOUT TIME ZONE,
    id_usuario_cambio       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_grupos_operadores_id_empresa
    ON t_grupos_operadores (id_empresa);

CREATE INDEX IF NOT EXISTS idx_grupos_operadores_empresa_nombre
    ON t_grupos_operadores (id_empresa, nombre);

-- ─────────────────────────────────────────────────────────────────────────────
-- RELACIÓN GRUPO ↔ OPERADOR (N:M)
-- Un operador puede estar en varios grupos; un grupo tiene varios operadores.
-- PK compuesta para impedir que un operador aparezca duplicado en un grupo.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS r_grupo_operadores_operadores (
    id_grupo_operadores     INTEGER NOT NULL
                                REFERENCES t_grupos_operadores(id_grupo_operadores)
                                ON DELETE CASCADE,
    id_operador             INTEGER NOT NULL
                                REFERENCES t_operadores(id_operador)
                                ON DELETE CASCADE,
    PRIMARY KEY (id_grupo_operadores, id_operador)
);

CREATE INDEX IF NOT EXISTS idx_r_grupo_op_grupo
    ON r_grupo_operadores_operadores (id_grupo_operadores);

CREATE INDEX IF NOT EXISTS idx_r_grupo_op_operador
    ON r_grupo_operadores_operadores (id_operador);

COMMIT;