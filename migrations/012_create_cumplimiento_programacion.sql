--   Materializa los itinerarios en fechas concretas con unidades asignadas.
--   Es la capa de datos que el monitor y el histórico consultan.


BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. ITINERARIO × FECHA
--    Un registro por cada (itinerario, fecha) programada.
--    Se crea al programar el itinerario para una fecha concreta.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_itinerario_fecha (
    id_itinerario_fecha     SERIAL PRIMARY KEY,
    id_itinerario           INTEGER NOT NULL
                                REFERENCES t_itinerarios(id_itinerario) ON DELETE CASCADE,
    id_empresa              INTEGER NOT NULL REFERENCES t_empresas(id_empresa),

    -- Fecha concreta en que se ejecuta este itinerario
    fecha                   DATE NOT NULL,

    -- Ventana horaria absoluta (fecha + hora del itinerario base).
    -- Se precalcula al crear el registro para no recalcular en cada query.
    fecha_hora_inicio       TIMESTAMP,
    fecha_hora_fin          TIMESTAMP,

    -- Estado del itinerario en esta fecha.
    -- 0=cancelado, 1=programado, 2=en curso, 3=completado
    status                  SMALLINT NOT NULL DEFAULT 1
                                CHECK (status IN (0, 1, 2, 3)),

    -- Número de apoyos (unidades adicionales asignadas)
    apoyos                  SMALLINT NOT NULL DEFAULT 0,

    fecha_registro          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro     INTEGER,
    fecha_cambio            TIMESTAMP,
    id_usuario_cambio       INTEGER,

    -- Un itinerario solo puede programarse una vez por fecha
    UNIQUE (id_itinerario, fecha)
);

COMMENT ON TABLE t_itinerario_fecha IS
    'Materialización de un itinerario en una fecha concreta. Antes t_turno_fecha.';
COMMENT ON COLUMN t_itinerario_fecha.status IS
    '0=cancelado, 1=programado, 2=en curso, 3=completado.';

CREATE INDEX IF NOT EXISTS idx_if_id_itinerario
    ON t_itinerario_fecha (id_itinerario);
CREATE INDEX IF NOT EXISTS idx_if_empresa_fecha
    ON t_itinerario_fecha (id_empresa, fecha);
CREATE INDEX IF NOT EXISTS idx_if_fecha_status
    ON t_itinerario_fecha (fecha, status);


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. UNIDAD EJECUTORA
--    Qué unidad ejecuta el itinerario en esa fecha, con todas sus métricas
--    de cumplimiento. Fusiona t_turno_fecha_unidad + t_turno_fecha_unidad_alertas
--    de la v2.5 (eran la misma entidad, separadas solo por rendimiento en
--    tablas MEMORY de MariaDB).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_itinerario_fecha_unidad (
    id_itinerario_fecha_unidad  SERIAL PRIMARY KEY,
    id_itinerario_fecha         INTEGER NOT NULL
                                    REFERENCES t_itinerario_fecha(id_itinerario_fecha) ON DELETE CASCADE,
    id_unidad                   INTEGER NOT NULL REFERENCES t_unidades(id_unidad),

    -- IMEI de la unidad al momento de la asignación (se desnormaliza para
    -- no depender del catálogo si la unidad cambia de equipo)
    imei                        VARCHAR(20),

    -- 1=titular (unidad asignada directamente al itinerario)
    -- 2=apoyo   (unidad de refuerzo)
    tipo_asignacion             SMALLINT NOT NULL DEFAULT 1
                                    CHECK (tipo_asignacion IN (1, 2)),

    -- ── Hitos de la ejecución ──────────────────────────────────────────────
    -- Cada hito tiene fecha_hora real y odómetro al momento del evento.
    -- Equivalen a los campos fecha_hora_*/odometro_* de t_turno_fecha_unidad.

    fecha_hora_encendido        TIMESTAMP,  -- motor encendido
    odometro_encendido          NUMERIC(10,2),

    fecha_hora_arranque         TIMESTAMP,  -- unidad en movimiento
    odometro_arranque           NUMERIC(10,2),

    fecha_hora_llegada_f1       TIMESTAMP,  -- llegó a primera parada
    odometro_llegada_f1         NUMERIC(10,2),

    fecha_hora_salida_f1        TIMESTAMP,  -- salió de primera parada
    odometro_salida_f1          NUMERIC(10,2),

    fecha_hora_llegada_destino  TIMESTAMP,  -- llegó al destino final
    odometro_llegada_destino    NUMERIC(10,2),

    fecha_hora_salida_destino   TIMESTAMP,  -- salió del destino
    odometro_salida_destino     NUMERIC(10,2),

    -- Punto GPS donde estaba la unidad al inicio del servicio
    lat_origen                  NUMERIC(10,8),
    lng_origen                  NUMERIC(11,8),

    -- ── Métricas calculadas por el worker (3B) ────────────────────────────
    vel_max                     NUMERIC(6,2) DEFAULT 0,
    eventos_vel_max             INTEGER DEFAULT 0,
    kms_totales                 NUMERIC(8,3) DEFAULT 0,
    kms_servicio                NUMERIC(8,3) DEFAULT 0,  -- km dentro de ruta
    kms_vacio                   NUMERIC(8,3) DEFAULT 0,  -- km fuera de ruta antes del servicio
    m_fuera_ruta                INTEGER DEFAULT 0,       -- metros fuera del trazo
    m_fuera_ruta_pct            NUMERIC(5,2) DEFAULT 0,
    m_en_ruta                   INTEGER DEFAULT 0,
    tiempo_total                INTEGER DEFAULT 0,       -- segundos totales
    tiempo_en_ruta              INTEGER DEFAULT 0,       -- segundos dentro del trazo
    tiempo_fuera_ruta           INTEGER DEFAULT 0,

    -- ── Progreso de paradas ───────────────────────────────────────────────
    paradas_abordadas           SMALLINT DEFAULT 0,
    paradas_omitidas            SMALLINT DEFAULT 0,
    abordajes                   INTEGER DEFAULT 0,       -- total de pasajeros
    porcentaje_paradas          NUMERIC(5,2) DEFAULT 0,
    porcentaje_ruta             NUMERIC(5,2) DEFAULT 0,
    porcentaje_cumplimiento     NUMERIC(5,2) DEFAULT 0,
    progreso_ruta               NUMERIC(5,2) DEFAULT 0,

    -- ── Estado en tiempo real (actualizado por el worker) ─────────────────
    -- Equivale a t_turno_fecha_unidad_alertas de la v2.5
    en_ruta                     BOOLEAN DEFAULT FALSE,
    en_curso                    BOOLEAN DEFAULT FALSE,
    index_trazo                 INTEGER DEFAULT 0,       -- índice en el trazo codificado
    distancia_a_inicio          NUMERIC(10,2) DEFAULT 0,

    -- Parada actual, anterior y siguiente (ids para el monitor en tiempo real)
    id_parada_actual            INTEGER REFERENCES t_paradas_ruta(id_parada),
    id_parada_anterior          INTEGER REFERENCES t_paradas_ruta(id_parada),
    id_parada_siguiente         INTEGER REFERENCES t_paradas_ruta(id_parada),

    -- Alarmas activas (booleanos — el monitor las visualiza con iconos)
    alarma_encendido            BOOLEAN DEFAULT FALSE,
    alarma_arranque             BOOLEAN DEFAULT FALSE,
    alarma_llegada_f1           BOOLEAN DEFAULT FALSE,
    alarma_salida_f1            BOOLEAN DEFAULT FALSE,
    alarma_en_ruta              BOOLEAN DEFAULT FALSE,
    alarma_relenti              BOOLEAN DEFAULT FALSE,
    alarma_unidad_detenida      BOOLEAN DEFAULT FALSE,
    alarma_anticipacion         BOOLEAN DEFAULT FALSE,
    alarma_retraso              BOOLEAN DEFAULT FALSE,
    alarma_desviacion           BOOLEAN DEFAULT FALSE,
    alarma_parada_omitida       BOOLEAN DEFAULT FALSE,

    -- Timestamps de alarmas para calcular duraciones
    fecha_hora_alarma_relenti           TIMESTAMP,
    fecha_hora_alarma_unidad_detenida   TIMESTAMP,
    fecha_hora_desviacion_ruta          TIMESTAMP,
    fecha_hora_incorporacion_ruta       TIMESTAMP,
    fecha_hora_intercepcion_ruta        TIMESTAMP,

    -- Última vez que el worker actualizó este registro
    fecha_hora_update           TIMESTAMP,

    -- 0=en progreso, 1=completado, 2=cancelado
    status                      SMALLINT NOT NULL DEFAULT 0
                                    CHECK (status IN (0, 1, 2)),

    fecha_registro              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro         INTEGER,
    fecha_cambio                TIMESTAMP,
    id_usuario_cambio           INTEGER
);

COMMENT ON TABLE t_itinerario_fecha_unidad IS
    'Unidad ejecutora de un itinerario en una fecha, con métricas de cumplimiento.
     Fusiona t_turno_fecha_unidad + t_turno_fecha_unidad_alertas de la v2.5.';
COMMENT ON COLUMN t_itinerario_fecha_unidad.tipo_asignacion IS
    '1=titular (asignación directa), 2=apoyo (unidad de refuerzo).';

CREATE INDEX IF NOT EXISTS idx_ifu_id_itinerario_fecha
    ON t_itinerario_fecha_unidad (id_itinerario_fecha);
CREATE INDEX IF NOT EXISTS idx_ifu_id_unidad
    ON t_itinerario_fecha_unidad (id_unidad);
CREATE INDEX IF NOT EXISTS idx_ifu_imei
    ON t_itinerario_fecha_unidad (imei)
    WHERE imei IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ifu_status
    ON t_itinerario_fecha_unidad (status);


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. ESTADO DE PARADAS POR EJECUCIÓN
--    Una fila por cada parada dentro de la ejecución de un itinerario.
--    Incluye columnas GEOGRAPHY de PostGIS para detección directa en 3B:
--    el worker usará ST_DWithin(geocerca, punto_gps, radio) para detectar
--    si la unidad está dentro de la geocerca de la parada.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_itinerario_fecha_parada (
    id_itinerario_fecha_parada  SERIAL PRIMARY KEY,
    id_itinerario_fecha_unidad  INTEGER NOT NULL
                                    REFERENCES t_itinerario_fecha_unidad(id_itinerario_fecha_unidad) ON DELETE CASCADE,
    id_parada                   INTEGER NOT NULL
                                    REFERENCES t_paradas_ruta(id_parada),

    -- Número de orden de la parada en el itinerario
    numero                      INTEGER NOT NULL,

    -- Hora programada de abordaje (del itinerario base)
    hora_abordaje_programada    TIME,

    -- Hora real en que la unidad llegó/pasó por esta parada
    fecha_hora_llegada          TIMESTAMP,
    fecha_hora_salida           TIMESTAMP,

    -- Diferencia en minutos respecto a la hora programada
    -- Positivo = retraso, negativo = anticipación
    minutos_diferencia          SMALLINT,

    -- ── Geometría para detección PostGIS (preparado para Entrega 3B) ──────
    -- Copia la geocerca de t_paradas_ruta al momento de crear la programación.
    -- Permite que el worker de 3B use ST_DWithin() sin hacer JOIN a t_paradas_ruta
    -- en cada ping GPS — mejora significativa de rendimiento.
    --
    -- Se llena en la 3A al crear t_itinerario_fecha_parada.
    -- Se usa en la 3B para: ST_DWithin(geocerca_punto, ping_gps, radio)
    geocerca_punto              GEOGRAPHY(Point, 4326),   -- centro de la geocerca circular
    geocerca_radio              INTEGER,                   -- radio en metros
    geocerca_poligono           GEOGRAPHY(Polygon, 4326), -- si es geocerca poligonal

    -- Estado de la parada en esta ejecución
    -- 0=pendiente, 1=abordada, 2=omitida, 3=parcial
    status                      SMALLINT NOT NULL DEFAULT 0
                                    CHECK (status IN (0, 1, 2, 3)),

    fecha_registro              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (id_itinerario_fecha_unidad, id_parada)
);

COMMENT ON TABLE t_itinerario_fecha_parada IS
    'Estado de cada parada en la ejecución de un itinerario.
     Las columnas geocerca_* están preparadas para detección PostGIS en Entrega 3B.';
COMMENT ON COLUMN t_itinerario_fecha_parada.geocerca_punto IS
    'Centro de la geocerca circular. Usado en 3B: ST_DWithin(geocerca_punto, ping, radio).';
COMMENT ON COLUMN t_itinerario_fecha_parada.geocerca_poligono IS
    'Polígono de la geocerca. Usado en 3B: ST_Contains(geocerca_poligono, ping).';
COMMENT ON COLUMN t_itinerario_fecha_parada.status IS
    '0=pendiente, 1=abordada (unidad pasó por la parada), 2=omitida, 3=parcial.';

CREATE INDEX IF NOT EXISTS idx_ifp_id_itinerario_fecha_unidad
    ON t_itinerario_fecha_parada (id_itinerario_fecha_unidad);
CREATE INDEX IF NOT EXISTS idx_ifp_id_parada
    ON t_itinerario_fecha_parada (id_parada);
-- Índice espacial para ST_DWithin en el worker de 3B
CREATE INDEX IF NOT EXISTS idx_ifp_geocerca_punto
    ON t_itinerario_fecha_parada USING GIST (geocerca_punto);
CREATE INDEX IF NOT EXISTS idx_ifp_geocerca_poligono
    ON t_itinerario_fecha_parada USING GIST (geocerca_poligono)
    WHERE geocerca_poligono IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ifp_status_pendiente
    ON t_itinerario_fecha_parada (id_itinerario_fecha_unidad)
    WHERE status = 0;  -- el worker solo procesa paradas pendientes

COMMIT;