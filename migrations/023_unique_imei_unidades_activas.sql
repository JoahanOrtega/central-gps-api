CREATE UNIQUE INDEX IF NOT EXISTS uniq_imei_unidades_activas
    ON t_unidades (imei)
    WHERE status = 1
      AND imei IS NOT NULL
      AND imei <> '';

COMMENT ON INDEX uniq_imei_unidades_activas IS
    'Garantiza que un IMEI no esté activo en más de una unidad a la vez. '
    'Parcial (status = 1) para permitir el historial de GPS reasignados.';