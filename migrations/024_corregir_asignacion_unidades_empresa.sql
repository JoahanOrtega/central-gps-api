-- ════════════════════════════════════════════════════════════════════════════
-- 024_corregir_asignacion_unidades_empresa.sql
-- ════════════════════════════════════════════════════════════════════════════
--
-- CONTEXTO:
--   La migración 022 desactivó unidades con IMEI duplicado asumiendo que los
--   vehículos pertenecían a la empresa 2. Posteriormente se confirmó que la
--   empresa propietaria real (Tadimex) es la empresa 1, NO la 2:
--     - Empresa 1  = Tadimex (propietaria real de march, yaris, np300, test)
--     - Empresa 2  = Servicio Industrial Autoexpress (copias basura de migración)
--     - Empresa 11 = Particular #1 (propietaria del Robust)
--
--   Por lo tanto, la 022 desactivó las unidades EQUIVOCADAS. Esta migración
--   corrige el rumbo dejando cada vehículo activo en su empresa correcta y un
--   único registro activo por IMEI.
--
-- ORDEN (importa por el índice único uniq_imei_unidades_activas de la 023):
--   1. Desactivar primero las copias basura (libera los IMEIs).
--   2. Reactivar después las unidades correctas (ya sin colisión).
--   Si se hiciera al revés, el índice único rechazaría la reactivación.
--
-- IDEMPOTENTE:
--   Cada UPDATE filtra por el status de origen, así que correrla varias veces
--   no causa daño. Reversible: solo cambia status, no borra datos.
-- ════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    desactivadas INTEGER;
    reactivadas  INTEGER;
BEGIN
    -- ── PASO 1: desactivar copias basura de Autoexpress (empresa 2) ──────────
    -- Estas unidades son duplicados de migración y comparten IMEI con las de
    -- Tadimex. Desactivarlas libera los IMEIs para reactivar las correctas.
    UPDATE t_unidades
    SET status = 0
    WHERE id_unidad IN (1258, 1259, 1260)
      AND id_empresa = 2
      AND status = 1;

    GET DIAGNOSTICS desactivadas = ROW_COUNT;
    RAISE NOTICE 'Copias basura de Autoexpress desactivadas: %', desactivadas;

    -- ── PASO 2: reactivar unidades de Tadimex desactivadas por error ─────────
    -- La 022 desactivó la yaris (id 2) y np300 (id 1261) de Tadimex creyendo
    -- que eran de otra empresa. Se reactivan.
    UPDATE t_unidades
    SET status = 1
    WHERE id_unidad IN (2, 1261)
      AND id_empresa = 1
      AND status = 0;

    GET DIAGNOSTICS reactivadas = ROW_COUNT;
    RAISE NOTICE 'Unidades de Tadimex reactivadas: %', reactivadas;

    -- ── PASO 3: reactivar el Robust de Particular #1 (empresa 11) ────────────
    -- Su IMEI (1610010527) es el correcto del Robust; estaba desactivado por la
    -- colisión con la copia basura de Autoexpress, ya resuelta en el paso 1.
    UPDATE t_unidades
    SET status = 1
    WHERE id_unidad = 1262
      AND id_empresa = 11
      AND status = 0;

    GET DIAGNOSTICS reactivadas = ROW_COUNT;
    RAISE NOTICE 'Robust de Particular #1 reactivado: %', reactivadas;

    -- ── Verificación final: ningún IMEI debe quedar activo en 2+ unidades ────
    IF EXISTS (
        SELECT 1
        FROM t_unidades
        WHERE status = 1
          AND imei IS NOT NULL
          AND imei <> ''
        GROUP BY imei
        HAVING COUNT(*) > 1
    ) THEN
        RAISE WARNING 'Aún existen IMEIs duplicados en unidades activas — revisar.';
    ELSE
        RAISE NOTICE 'OK: cada IMEI activo está en una sola unidad.';
    END IF;
END $$;