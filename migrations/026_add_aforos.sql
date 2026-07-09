BEGIN;

CREATE TABLE IF NOT EXISTS t_grupos_aforos (
    id_grupo_aforos         SERIAL PRIMARY KEY,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    id_cliente              INTEGER NOT NULL REFERENCES t_clientes(id_cliente),
    clave                   VARCHAR(50) NOT NULL,
    nombre                  VARCHAR(200) NOT NULL,
    observaciones           VARCHAR(200),
    id_ruta                 INTEGER REFERENCES t_rutas(id_ruta) ON DELETE SET NULL,
    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS t_aforos (
    id_aforo                SERIAL PRIMARY KEY,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    id_grupo_aforos         INTEGER REFERENCES t_grupos_aforos(id_grupo_aforos) ON DELETE SET NULL,
    rfid                    VARCHAR(50) UNIQUE NOT NULL,
    clave                   VARCHAR(50),
    nombre                  VARCHAR(200) NOT NULL,
    departamento            VARCHAR(200),
    direccion               VARCHAR(200),
    id_ruta                 INTEGER REFERENCES t_rutas(id_ruta) ON DELETE SET NULL,
    referencia              VARCHAR(200),
    fecha_asignacion        DATE NOT NULL,
    is_blacklist            BOOLEAN NOT NULL DEFAULT FALSE,
    blacklist_date          TIMESTAMP,
    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aforos_blacklist 
    ON t_aforos (is_blacklist) 
    WHERE is_blacklist = TRUE;

CREATE INDEX IF NOT EXISTS idx_aforos_empresa ON t_aforos (id_empresa);
CREATE INDEX IF NOT EXISTS idx_aforos_grupo ON t_aforos (id_grupo_aforos);
CREATE INDEX IF NOT EXISTS idx_aforos_ruta ON t_aforos (id_ruta);
CREATE INDEX IF NOT EXISTS idx_grupos_ruta ON t_grupos_aforos (id_ruta);

COMMIT;