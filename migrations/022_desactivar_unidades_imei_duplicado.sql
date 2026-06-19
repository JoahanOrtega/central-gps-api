DO $$
DECLARE
    filas_afectadas INTEGER;
BEGIN
    -- Desactivar los registros duplicados que NO son de empresa 2,
    -- únicamente para los tres IMEIs confirmados como basura de migración.
    UPDATE t_unidades
    SET status = 0
    WHERE imei IN ('0890001454', '0950068608', '1610010527')
      AND id_empresa <> 2
      AND status = 1;

    GET DIAGNOSTICS filas_afectadas = ROW_COUNT;
    RAISE NOTICE 'Unidades desactivadas por IMEI duplicado: %', filas_afectadas;

    -- Verificación: tras la limpieza, ningún IMEI debe quedar en más de una
    -- unidad ACTIVA. Si esto falla, hay más duplicados de los previstos.
    IF EXISTS (
        SELECT 1
        FROM t_unidades
        WHERE status = 1
          AND imei IS NOT NULL
          AND imei <> ''
        GROUP BY imei
        HAVING COUNT(*) > 1
    ) THEN
        RAISE WARNING 'Aún existen IMEIs duplicados en unidades activas — revisar manualmente.';
    ELSE
        RAISE NOTICE 'OK: ningún IMEI duplicado entre unidades activas.';
    END IF;
END $$;