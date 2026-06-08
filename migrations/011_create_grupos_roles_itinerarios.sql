-- =============================================================================
-- Migración 011 — Grupos y Roles de Itinerarios (módulo Operación — Entrega 2)
-- =============================================================================
--
-- Contexto:
--   Extiende el módulo de itinerarios con dos capas de organización:
--
--   GRUPOS: agrupación simple del catálogo (como carpetas).
--     Un grupo contiene N itinerarios. Los itinerarios pueden pertenecer
--     a varios grupos (N:M). Úsase para organizar la vista del catálogo
--     sin impactar la lógica de programación.
--
--   ROLES: secuencia ordenada de itinerarios asignable a unidades.
--     Un rol define un "programa de trabajo" de varios días:
--     día 1 → itinerario A, día 2 → itinerario B, día 3 → descanso, etc.
--     Cada unidad puede tener asignado un rol con fecha de inicio,
--     lo que permite calcular qué itinerario le corresponde cada día.
--
-- Jerarquía completa del módulo tras esta migración:
--   t_rutas → t_logisticas_ruta → t_itinerarios → r_itinerario_paradas
--   t_itinerarios ←→ t_grupos_itinerarios  (vía r_grupo_itinerarios_itinerarios)
--   t_roles_itinerarios → r_rol_itinerarios  → t_itinerarios
--   t_roles_itinerarios → r_rol_asignacion_unidades → t_unidades
--
-- Optimizaciones vs esquema MariaDB v2.5:
--   - t_grupos_turnos.turnos (contador) → eliminado, se calcula con COUNT
--   - t_roles_itinerarios.itinerarios (contador) → eliminado, igual
--   - t_roles_itinerarios.asignaciones (contador) → eliminado, igual
--   - r_rol_itinerarios_itinerarios sin PK → ahora PK compuesta
--   - descanso como BOOLEAN en vez de INT(11) DEFAULT 0
--   - last_this_day (columna sin uso documentado) → omitida
--   - Nomenclatura unificada: _itinerarios en vez de mezcla turno/itinerario
--
-- Cómo aplicar:
--   podman exec centralgo_api_1 python migrate.py
--   docker exec proyecto-api-1  python migrate.py
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- GRUPOS DE ITINERARIOS
-- Agrupación simple para organizar el catálogo (sin impactar programación).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS t_grupos_itinerarios (
    id_grupo_itinerarios    SERIAL PRIMARY KEY,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    id_cliente              INTEGER REFERENCES t_clientes(id_cliente),
    nombre                  VARCHAR(150) NOT NULL,
    observaciones           TEXT,
    status                  SMALLINT NOT NULL DEFAULT 1,
    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro     INTEGER,
    fecha_cambio            TIMESTAMP,
    id_usuario_cambio       INTEGER
);

COMMENT ON TABLE t_grupos_itinerarios IS
    'Agrupaciones del catálogo de itinerarios. Antes t_grupos_turnos en v2.5.';

CREATE INDEX IF NOT EXISTS idx_grupos_itinerarios_empresa
    ON t_grupos_itinerarios (id_empresa);
CREATE INDEX IF NOT EXISTS idx_grupos_itinerarios_empresa_activos
    ON t_grupos_itinerarios (id_empresa) WHERE status = 1;


-- Relación N:M entre grupos e itinerarios
CREATE TABLE IF NOT EXISTS r_grupo_itinerarios_itinerarios (
    id_grupo_itinerarios    INTEGER NOT NULL
                                REFERENCES t_grupos_itinerarios(id_grupo_itinerarios) ON DELETE CASCADE,
    id_itinerario           INTEGER NOT NULL
                                REFERENCES t_itinerarios(id_itinerario) ON DELETE CASCADE,
    PRIMARY KEY (id_grupo_itinerarios, id_itinerario)
);

COMMENT ON TABLE r_grupo_itinerarios_itinerarios IS
    'Relación N:M grupo ↔ itinerario. Antes r_grupo_turnos_turnos.';

CREATE INDEX IF NOT EXISTS idx_rgi_id_itinerario
    ON r_grupo_itinerarios_itinerarios (id_itinerario);


-- ─────────────────────────────────────────────────────────────────────────────
-- ROLES DE ITINERARIOS
-- Secuencia ordenada de itinerarios por día, asignable a unidades.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS t_roles_itinerarios (
    id_rol_itinerarios      SERIAL PRIMARY KEY,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),

    -- Identificador corto visible al usuario (ej: "ROL-A", "TRAFICO-1")
    clave                   VARCHAR(50),

    -- Nombre descriptivo del rol
    nombre                  VARCHAR(150) NOT NULL,

    -- Fecha desde la que el rol es válido.
    -- Se usa como punto de referencia para calcular qué día del ciclo
    -- le corresponde a una unidad en una fecha dada.
    fecha_inicio_rol        DATE,

    -- Duración total del ciclo en días (incluyendo descansos).
    -- Ej: un rol de lunes a domingo tiene dias_duracion=7.
    -- 0 = sin duración definida (rol abierto).
    dias_duracion           INTEGER NOT NULL DEFAULT 0,

    observaciones           TEXT,
    status                  SMALLINT NOT NULL DEFAULT 1,
    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro     INTEGER,
    fecha_cambio            TIMESTAMP,
    id_usuario_cambio       INTEGER
);

COMMENT ON TABLE t_roles_itinerarios IS
    'Secuencia ordenada de itinerarios por día, asignable a unidades.
     Antes t_roles_itinerarios en v2.5 (mismo nombre).';
COMMENT ON COLUMN t_roles_itinerarios.dias_duracion IS
    'Duración del ciclo en días (itinerarios + descansos). 0 = sin duración fija.';

CREATE INDEX IF NOT EXISTS idx_roles_itinerarios_empresa
    ON t_roles_itinerarios (id_empresa);
CREATE INDEX IF NOT EXISTS idx_roles_itinerarios_empresa_activos
    ON t_roles_itinerarios (id_empresa) WHERE status = 1;


-- Itinerarios que componen cada rol, con su día y orden dentro del ciclo.
-- Un rol de 5 días podría tener: día1→orden1→itin_A, día2→orden1→itin_B, etc.
CREATE TABLE IF NOT EXISTS r_rol_itinerarios (
    id_rol_itinerarios      INTEGER NOT NULL
                                REFERENCES t_roles_itinerarios(id_rol_itinerarios) ON DELETE CASCADE,
    id_itinerario           INTEGER NOT NULL
                                REFERENCES t_itinerarios(id_itinerario) ON DELETE CASCADE,

    -- Día del ciclo al que pertenece este itinerario (1, 2, 3...).
    -- Si el rol dura 7 días, dia_rol va de 1 a 7.
    dia_rol                 INTEGER NOT NULL,

    -- Posición dentro del día (1, 2, 3...) — un día puede tener múltiples itinerarios.
    orden                   INTEGER NOT NULL DEFAULT 1,

    -- Si es TRUE, este slot representa un día de descanso.
    -- En descanso, id_itinerario puede ser NULL (no hay itinerario asignado).
    es_descanso             BOOLEAN NOT NULL DEFAULT FALSE,

    PRIMARY KEY (id_rol_itinerarios, dia_rol, orden)
);

COMMENT ON TABLE r_rol_itinerarios IS
    'Itinerarios del rol con su día y orden. Antes r_rol_itinerarios_itinerarios.';
COMMENT ON COLUMN r_rol_itinerarios.dia_rol IS
    'Día del ciclo (1-based). Si dias_duracion=7, va de 1 a 7.';
COMMENT ON COLUMN r_rol_itinerarios.es_descanso IS
    'TRUE = día de descanso, no se asigna itinerario a la unidad.';

CREATE INDEX IF NOT EXISTS idx_rol_itinerarios_id_itinerario
    ON r_rol_itinerarios (id_itinerario);


-- Asignación de roles a unidades con fecha de inicio.
-- Permite calcular qué itinerario corresponde a cada unidad en cada fecha.
CREATE TABLE IF NOT EXISTS r_rol_asignacion_unidades (
    id_asignacion           SERIAL PRIMARY KEY,
    id_rol_itinerarios      INTEGER NOT NULL
                                REFERENCES t_roles_itinerarios(id_rol_itinerarios) ON DELETE CASCADE,
    id_unidad               INTEGER NOT NULL REFERENCES t_unidades(id_unidad),

    -- Fecha desde la que la unidad sigue este rol.
    fecha_asignacion        DATE NOT NULL,

    -- Fecha hasta la que aplica (NULL = vigente hasta nueva asignación).
    fecha_baja              DATE,

    -- Posición inicial en el ciclo del rol al momento de la asignación.
    -- Permite que la unidad "entre" al rol en cualquier día del ciclo.
    -- Ej: si el rol tiene 7 días y dia_inicio_rol=3, la unidad comienza
    -- en el día 3 del ciclo.
    dia_inicio_rol          INTEGER NOT NULL DEFAULT 1,

    status                  SMALLINT NOT NULL DEFAULT 1,
    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro     INTEGER,
    fecha_cambio            TIMESTAMP,
    id_usuario_cambio       INTEGER
);

COMMENT ON TABLE r_rol_asignacion_unidades IS
    'Asignación de un rol a una unidad con fecha de inicio del ciclo.
     Antes r_rol_itinerarios_asignacion_unidades en v2.5.';
COMMENT ON COLUMN r_rol_asignacion_unidades.dia_inicio_rol IS
    'Día del ciclo en que entra la unidad (1-based). Permite sincronización.';

CREATE INDEX IF NOT EXISTS idx_rol_asig_id_rol
    ON r_rol_asignacion_unidades (id_rol_itinerarios);
CREATE INDEX IF NOT EXISTS idx_rol_asig_id_unidad
    ON r_rol_asignacion_unidades (id_unidad);
CREATE INDEX IF NOT EXISTS idx_rol_asig_fecha
    ON r_rol_asignacion_unidades (fecha_asignacion);

COMMIT;