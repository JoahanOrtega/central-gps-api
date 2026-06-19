BEGIN;

-- ─── 1. Tabla de configuración del token de rastreo ──────────────────────────

CREATE TABLE IF NOT EXISTS t_clientes_token (
    id_cliente                 INTEGER PRIMARY KEY
                               REFERENCES t_clientes (id_cliente) ON DELETE CASCADE,
    id_empresa                 INTEGER NOT NULL
                               REFERENCES t_empresas (id_empresa),

    -- Acceso y token
    acceso_token_rastreo       BOOLEAN NOT NULL DEFAULT FALSE,
    token                      VARCHAR(15),
    early_access_token_rastreo BOOLEAN NOT NULL DEFAULT FALSE,
    acceso_global              BOOLEAN NOT NULL DEFAULT FALSE,

    -- Clave de acceso opcional (6 dígitos)
    token_requiere_clave_acceso BOOLEAN NOT NULL DEFAULT FALSE,
    token_clave_acceso          VARCHAR(6),
    permite_acceso_clave_usuario BOOLEAN NOT NULL DEFAULT FALSE,

    -- Opciones de visualización del rastreo
    tipo_vista_token                          SMALLINT NOT NULL DEFAULT 0,
    tipo_icono_unidad                         BOOLEAN  NOT NULL DEFAULT FALSE,
    visualizar_info_paradas                   SMALLINT NOT NULL DEFAULT 0,
    tipo_itinerario_visible                   BOOLEAN  NOT NULL DEFAULT FALSE,
    ocultar_itinerarios_terminados            BOOLEAN  NOT NULL DEFAULT FALSE,
    tipo_agrupacion_itinerarios               BOOLEAN  NOT NULL DEFAULT FALSE,
    tipo_ordenamiento_itinerarios             BOOLEAN  NOT NULL DEFAULT FALSE,
    identificacion_automatica_tipo_itinerario BOOLEAN  NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_t_clientes_token_id_empresa
    ON t_clientes_token (id_empresa);

-- Índice parcial para buscar por token (solo los que tienen token activo).
-- El rastreo público resuelve el cliente a partir del token, así que esta
-- búsqueda debe ser rápida.
CREATE UNIQUE INDEX IF NOT EXISTS idx_t_clientes_token_token
    ON t_clientes_token (token)
    WHERE token IS NOT NULL;


-- ─── 2. Tabla de configuración del dashboard de cumplimiento ─────────────────

CREATE TABLE IF NOT EXISTS t_clientes_dashboard (
    id_cliente                      INTEGER PRIMARY KEY
                                    REFERENCES t_clientes (id_cliente) ON DELETE CASCADE,
    id_empresa                      INTEGER NOT NULL
                                    REFERENCES t_empresas (id_empresa),

    acceso_dashboard_cmp            BOOLEAN NOT NULL DEFAULT FALSE,
    token_dashboard                 VARCHAR(20),
    dashboard_requiere_clave_acceso BOOLEAN NOT NULL DEFAULT FALSE,
    dashboard_clave_acceso          VARCHAR(6),

    visualizar_estadistica_aforos   BOOLEAN NOT NULL DEFAULT FALSE,
    visualizar_graficas_generales   BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_t_clientes_dashboard_id_empresa
    ON t_clientes_dashboard (id_empresa);

CREATE UNIQUE INDEX IF NOT EXISTS idx_t_clientes_dashboard_token
    ON t_clientes_dashboard (token_dashboard)
    WHERE token_dashboard IS NOT NULL;


-- ─── 3. Copiar los datos existentes de las columnas planas ───────────────────
-- Convierte smallint (0/1) a boolean con (columna = 1). ON CONFLICT DO NOTHING
-- hace la copia idempotente: si la fila ya existe (migración re-ejecutada), no
-- la sobrescribe.

INSERT INTO t_clientes_token (
    id_cliente,
    id_empresa,
    acceso_token_rastreo,
    token,
    early_access_token_rastreo,
    acceso_global,
    token_requiere_clave_acceso,
    token_clave_acceso,
    permite_acceso_clave_usuario,
    tipo_vista_token,
    tipo_icono_unidad,
    visualizar_info_paradas,
    tipo_itinerario_visible,
    ocultar_itinerarios_terminados,
    tipo_agrupacion_itinerarios,
    tipo_ordenamiento_itinerarios,
    identificacion_automatica_tipo_itinerario
)
SELECT
    c.id_cliente,
    c.id_empresa,
    COALESCE(c.acceso_token_rastreo, 0) = 1,
    -- Solo copiar tokens con formato válido de rastreo (cortos, <=15 chars).
    -- Los datos de prueba tienen hashes largos (64 chars) que NO son tokens de
    -- rastreo legítimos: un token de rastreo debe ser corto para que el enlace
    -- público sea compartible. Los que no caben se descartan (quedan NULL) y se
    -- regeneran al activar el rastreo.
    CASE WHEN LENGTH(c.token) <= 15 THEN NULLIF(c.token, '') ELSE NULL END,
    COALESCE(c.early_access_token_rastreo, 0) = 1,
    COALESCE(c.acceso_global, 0) = 1,
    COALESCE(c.token_requiere_clave_acceso, 0) = 1,
    NULLIF(c.token_clave_acceso, ''),
    COALESCE(c.permite_acceso_clave_usuario, 0) = 1,
    COALESCE(c.tipo_vista_token, 0),
    COALESCE(c.tipo_icono_unidad, 0) = 1,
    COALESCE(c.visualizar_info_paradas, 0),
    COALESCE(c.tipo_itinerario_visible, 0) = 1,
    COALESCE(c.ocultar_itinerarios_terminados, 0) = 1,
    COALESCE(c.tipo_agrupacion_itinerarios, 0) = 1,
    COALESCE(c.tipo_ordenamiento_itinerarios, 0) = 1,
    COALESCE(c.identificacion_automatica_tipo_itinerario, 0) = 1
FROM t_clientes c
ON CONFLICT (id_cliente) DO NOTHING;

INSERT INTO t_clientes_dashboard (
    id_cliente,
    id_empresa,
    acceso_dashboard_cmp,
    token_dashboard,
    dashboard_requiere_clave_acceso,
    dashboard_clave_acceso,
    visualizar_estadistica_aforos,
    visualizar_graficas_generales
)
SELECT
    c.id_cliente,
    c.id_empresa,
    COALESCE(c.acceso_dashboard_cmp, 0) = 1,
    -- Igual que el token de rastreo: descartar hashes largos de prueba.
    CASE WHEN LENGTH(c.token_dashboard) <= 20 THEN NULLIF(c.token_dashboard, '') ELSE NULL END,
    COALESCE(c.dashboard_requiere_clave_acceso, 0) = 1,
    NULLIF(c.dashboard_clave_acceso, ''),
    COALESCE(c.visualizar_estadistica_aforos, 0) = 1,
    COALESCE(c.visualizar_graficas_generales, 0) = 1
FROM t_clientes c
ON CONFLICT (id_cliente) DO NOTHING;

COMMIT;