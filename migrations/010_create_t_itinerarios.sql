BEGIN;
-- Cada fila es un horario programado sobre una logística concreta de una ruta.
CREATE TABLE IF NOT EXISTS t_itinerarios (
    id_itinerario       SERIAL PRIMARY KEY,
    id_empresa          INTEGER NOT NULL REFERENCES t_empresas(id_empresa),

    -- La ruta y la logística (sentido ida/vuelta) sobre la que corre el itinerario.
    -- Si se borra la ruta o la logística, los itinerarios asociados se eliminan.
    id_ruta             INTEGER NOT NULL REFERENCES t_rutas(id_ruta) ON DELETE CASCADE,
    id_logistica_ruta   INTEGER NOT NULL
                            REFERENCES t_logisticas_ruta(id_logistica_ruta) ON DELETE CASCADE,

    -- Código del turno visible al usuario: '1', '2', '3', '1A', etc.
    -- Identifica el itinerario dentro de su ruta (turno matutino, vespertino...).
    turno               VARCHAR(10),

    -- Tipo de itinerario. En la v2.5 era un INT sin documentar.
    -- 1 = regular (días fijos de la semana)
    -- 2 = especial (fecha concreta, no recurrente)
    tipo                SMALLINT NOT NULL DEFAULT 1
                            CHECK (tipo IN (1, 2)),

    -- Días de la semana en que aplica el itinerario, como array de enteros.
    -- Convención: 0=domingo, 1=lunes, ... 6=sábado (igual que la v2.5).
    -- Ejemplo: '{1,2,3,4,5}' = lunes a viernes.
    dias                SMALLINT[] DEFAULT '{}',

    -- Ventana horaria del itinerario.
    hora_inicio         TIME,
    hora_fin            TIME,

    -- Tolerancias en minutos (eran VARCHAR en v2.5 → ahora enteros).
    -- inicio:       margen para arrancar tarde sin marcar incumplimiento
    -- fin:          margen para terminar tarde
    -- anticipacion: margen para arrancar antes de lo programado
    minutos_tolerancia_inicio       SMALLINT NOT NULL DEFAULT 30,
    minutos_tolerancia_fin          SMALLINT NOT NULL DEFAULT 0,
    minutos_tolerancia_anticipacion SMALLINT NOT NULL DEFAULT 10,

    -- Contador denormalizado de paradas (evita COUNT en cada listado),
    -- igual que total_paradas en t_logisticas_ruta.
    total_paradas       INTEGER NOT NULL DEFAULT 0,

    -- Fecha desde la cual el itinerario está vigente.
    fecha_inicio        DATE,

    -- Token corto para acceso público (mismo patrón que t_rutas).
    token               VARCHAR(15),

    status              SMALLINT NOT NULL DEFAULT 1,

    -- Auditoría (mismo patrón que el resto del sistema).
    fecha_registro      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    id_usuario_registro INTEGER,
    fecha_cambio        TIMESTAMP,
    id_usuario_cambio   INTEGER
);

COMMENT ON TABLE t_itinerarios IS
    'Horarios programados sobre una logística de ruta. Antes t_turnos en v2.5.';
COMMENT ON COLUMN t_itinerarios.tipo IS
    '1=regular (días recurrentes de la semana), 2=especial (fecha concreta).';
COMMENT ON COLUMN t_itinerarios.dias IS
    'Días de la semana donde aplica. 0=domingo..6=sábado. Ej: {1,2,3,4,5}=L-V.';

CREATE INDEX IF NOT EXISTS idx_itinerarios_id_empresa
    ON t_itinerarios (id_empresa);
CREATE INDEX IF NOT EXISTS idx_itinerarios_empresa_activos
    ON t_itinerarios (id_empresa) WHERE status = 1;
CREATE INDEX IF NOT EXISTS idx_itinerarios_id_ruta
    ON t_itinerarios (id_ruta);
CREATE INDEX IF NOT EXISTS idx_itinerarios_id_logistica
    ON t_itinerarios (id_logistica_ruta);

CREATE TABLE IF NOT EXISTS r_itinerario_paradas (
    id_itinerario   INTEGER NOT NULL
                        REFERENCES t_itinerarios(id_itinerario) ON DELETE CASCADE,
    id_parada       INTEGER NOT NULL
                        REFERENCES t_paradas_ruta(id_parada) ON DELETE CASCADE,

    -- Hora a la que la unidad debe abordar/pasar por esta parada.
    hora_abordaje   TIME,

    -- Tiempos de recorrido estimados hasta esta parada (en segundos).
    -- continuo = sin tráfico; mixto = con tráfico estimado.
    -- Eran nullable en v2.5 y se usan en el cálculo de cumplimiento (Entrega 3).
    segundos_recorrido_continuo INTEGER,
    segundos_recorrido_mixto    INTEGER,

    PRIMARY KEY (id_itinerario, id_parada)
);

COMMENT ON TABLE r_itinerario_paradas IS
    'Paradas de cada itinerario con su hora de abordaje. Antes r_turno_paradas.';

CREATE INDEX IF NOT EXISTS idx_itin_paradas_id_parada
    ON r_itinerario_paradas (id_parada);

COMMIT;