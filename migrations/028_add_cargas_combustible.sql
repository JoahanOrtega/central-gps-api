BEGIN;

CREATE TABLE IF NOT EXISTS t_cargas_combustible (
    id_combustible  SERIAL PRIMARY KEY,
    id_empresa      INTEGER NOT NULL REFERENCES t_empresas(id_empresa),
    id_unidad       INTEGER NOT NULL REFERENCES t_unidades(id_unidad),
    
    fecha_carga     TIMESTAMPTZ NOT NULL,
    gasolinera      VARCHAR(200),
    grupo_unidades  VARCHAR(200),
    folio           VARCHAR(100) NOT NULL,
    
    litros          NUMERIC(20, 2) NOT NULL DEFAULT 0.00,
    costo_litro     NUMERIC(20, 2) NOT NULL DEFAULT 0.00,
    importe         NUMERIC(22, 2) NOT NULL DEFAULT 0.00,

    referencia      VARCHAR(200),
    
    kms_gps         NUMERIC(20, 2),
    kms_vacio       NUMERIC(20, 2),
    porc_vacio      NUMERIC(5, 2),
    rend_gps        NUMERIC(10, 2),
    kms_odo         NUMERIC(20, 2),
    rend_odo        NUMERIC(20, 2),

    rendimiento_establecido NUMERIC(10, 2),

    fecha_registro  TIMESTAMPTZ DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'America/Mexico_City')
);

-- 2. Crear índices de la tabla
CREATE INDEX IF NOT EXISTS idx_fuel_cargas_empresa ON t_cargas_combustible (id_empresa);
CREATE INDEX IF NOT EXISTS idx_fuel_cargas_unidad ON t_cargas_combustible (id_unidad);
CREATE INDEX IF NOT EXISTS idx_fuel_cargas_folio ON t_cargas_combustible (folio);

-- 3. Crear la función del Trigger
CREATE OR REPLACE FUNCTION fn_actualizar_odometro_fisico_por_inicial()
RETURNS TRIGGER AS $$
BEGIN
    IF (TG_OP = 'INSERT') OR (NEW.odometro_inicial IS DISTINCT FROM OLD.odometro_inicial) THEN
        NEW.odometro_fisico := COALESCE(NEW.odometro_inicial, 0.0) + COALESCE(
            (SELECT SUM(kms_odo) FROM t_cargas_combustible WHERE id_unidad = NEW.id_unidad),
            0.0
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. Crear el Trigger sobre t_unidades
DROP TRIGGER IF EXISTS trg_actualizar_odometro_fisico_inicial ON t_unidades;
CREATE TRIGGER trg_actualizar_odometro_fisico_inicial
BEFORE INSERT OR UPDATE OF odometro_inicial
ON t_unidades
FOR EACH ROW
EXECUTE FUNCTION fn_actualizar_odometro_fisico_por_inicial();

COMMIT;