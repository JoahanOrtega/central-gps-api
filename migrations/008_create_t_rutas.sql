-- =============================================================================
-- Migración 008 — Catalogo de Rutas (modulo Operación)
-- =============================================================================
--
-- Relación:
--   t_rutas (1) ──< (1-2) t_logisticas_ruta (1) ──< (N) t_paradas_ruta
--   t_rutas (N) >──< (N) t_grupos_rutas   vía  r_grupo_rutas_rutas
-- =============================================================================

-- Tabla principal: rutas
CREATE TABLE IF NOT EXISTS t_rutas (
    id_ruta             SERIAL PRIMARY KEY,
    id_empresa          INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    clave               VARCHAR(50),
    nombre              VARCHAR(500) NOT NULL,
    -- 1=personal, 2=reparto, 3=viaje especial, 4=colectivo
    tipo                SMALLINT DEFAULT 1,
    id_cliente          INTEGER REFERENCES t_clientes(id_cliente),
    observaciones       TEXT,
    -- token corto para acceso público al seguimiento de la ruta (como en v2.5)
    token               VARCHAR(12),
    status              SMALLINT NOT NULL DEFAULT 1,
    -- auditoría
    fecha_registro      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro INTEGER,
    fecha_cambio        TIMESTAMP,
    id_usuario_cambio   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rutas_id_empresa ON t_rutas (id_empresa);
CREATE INDEX IF NOT EXISTS idx_rutas_empresa_activas
    ON t_rutas (id_empresa) WHERE status = 1;
CREATE INDEX IF NOT EXISTS idx_rutas_id_cliente ON t_rutas (id_cliente);

-- Logísticas: cada sentido de la ruta (A→B ida, B→A vuelta)
CREATE TABLE IF NOT EXISTS t_logisticas_ruta (
    id_logistica_ruta    SERIAL PRIMARY KEY,
    id_ruta              INTEGER NOT NULL REFERENCES t_rutas(id_ruta) ON DELETE CASCADE,
    -- 1 = A-B (entrada/ida) | 2 = B-A (salida/vuelta)
    tipo_logistica       SMALLINT NOT NULL DEFAULT 1,
    direccion_inicio     VARCHAR(500),
    direccion_fin        VARCHAR(500),
    fecha_inicio         DATE,
    tiempo_recorrido_min INTEGER,
    kilometros           NUMERIC(8,2),
    -- El trazo completo de la ruta como polyline codificado de Google.
    -- Una sola cadena en vez de cientos de filas de coordenadas.
    encoded_path         TEXT,
    -- Color de la línea en el mapa
    trace_color          VARCHAR(20) DEFAULT '#2563eb',
    -- contador denormalizado para no hacer COUNT en cada listado
    total_paradas        INTEGER DEFAULT 0,
    fecha_registro       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro  INTEGER,
    fecha_cambio         TIMESTAMP,
    id_usuario_cambio    INTEGER
);

CREATE INDEX IF NOT EXISTS idx_logisticas_id_ruta ON t_logisticas_ruta (id_ruta);

-- Paradas (puntos de abordaje) de cada logística
CREATE TABLE IF NOT EXISTS t_paradas_ruta (
    id_parada           SERIAL PRIMARY KEY,
    id_logistica_ruta   INTEGER NOT NULL REFERENCES t_logisticas_ruta(id_logistica_ruta) ON DELETE CASCADE,
    id_ruta             INTEGER NOT NULL REFERENCES t_rutas(id_ruta) ON DELETE CASCADE,
    -- orden de la parada dentro de la logística (1, 2, 3...)
    numero              INTEGER NOT NULL,
    nombre              VARCHAR(300),
    direccion           VARCHAR(500),
    latitud             NUMERIC(10,8) NOT NULL,
    longitud            NUMERIC(11,8) NOT NULL,
    -- geocerca de la parada — mismo modelo que los POIs
    tipo_geocerca       VARCHAR(20) DEFAULT 'circular',  -- circular | poligonal | rectangular
    radio               INTEGER DEFAULT 100,
    -- vértices si es poligonal/rectangular, como JSON [[lat,lng],...]
    poligono            TEXT,
    fecha_registro      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paradas_id_logistica ON t_paradas_ruta (id_logistica_ruta);
CREATE INDEX IF NOT EXISTS idx_paradas_id_ruta ON t_paradas_ruta (id_ruta);

-- Grupos de rutas (para organizar el catálogo)
CREATE TABLE IF NOT EXISTS t_grupos_rutas (
    id_grupo_rutas      SERIAL PRIMARY KEY,
    id_empresa          INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    nombre              VARCHAR(200) NOT NULL,
    status              SMALLINT NOT NULL DEFAULT 1,
    fecha_registro      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro INTEGER
);

CREATE INDEX IF NOT EXISTS idx_grupos_rutas_id_empresa ON t_grupos_rutas (id_empresa);

-- Relación N:N entre rutas y grupos
CREATE TABLE IF NOT EXISTS r_grupo_rutas_rutas (
    id_grupo_rutas      INTEGER NOT NULL REFERENCES t_grupos_rutas(id_grupo_rutas) ON DELETE CASCADE,
    id_ruta             INTEGER NOT NULL REFERENCES t_rutas(id_ruta) ON DELETE CASCADE,
    PRIMARY KEY (id_grupo_rutas, id_ruta)
);