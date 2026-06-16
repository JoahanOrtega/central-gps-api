-- Qué hace:
--   Recorre toda columna que tenga una secuencia asociada (las columnas SERIAL
--   / IDENTITY) y reajusta su secuencia a MAX(columna). Usa pg_get_serial_sequence
--   para descubrir el vínculo columna→secuencia automáticamente, así no hay que
--   nombrar las 31 tablas a mano y sigue funcionando si se agregan tablas nuevas.
--
--   setval(seq, MAX(col), true)  → el próximo nextval devuelve MAX+1.
--   COALESCE(MAX, 0) + caso tabla vacía → si la tabla está vacía, deja la
--   secuencia en 1 sin marcarla como "usada" (setval con is_called=false).
--
-- Idempotente: correrla varias veces no causa daño — siempre ajusta al MAX
--   actual. Segura para producción.
--
-- Cómo aplicar:
--   docker exec proyecto-api-1 python migrate.py

BEGIN;

DO $$
DECLARE
    rec RECORD;
    seq_name TEXT;
    max_id   BIGINT;
BEGIN
    -- Recorrer todas las columnas de tablas del esquema public que tengan
    -- una secuencia asociada (columnas autoincrementales).
    FOR rec IN
        SELECT
            c.table_name,
            c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.column_default LIKE 'nextval(%'
    LOOP
        -- Descubrir el nombre real de la secuencia ligada a esta columna.
        seq_name := pg_get_serial_sequence(
            format('public.%I', rec.table_name),
            rec.column_name
        );

        IF seq_name IS NULL THEN
            CONTINUE;  -- columna sin secuencia real (caso raro), saltar
        END IF;

        -- Obtener el id máximo actual de la columna.
        EXECUTE format('SELECT COALESCE(MAX(%I), 0) FROM public.%I',
                       rec.column_name, rec.table_name)
        INTO max_id;

        IF max_id > 0 THEN
            -- Hay filas: la secuencia queda en MAX, próximo nextval = MAX+1.
            EXECUTE format('SELECT setval(%L, %s, true)', seq_name, max_id);
            RAISE NOTICE 'Secuencia % ajustada a % (tabla %.%)',
                seq_name, max_id, rec.table_name, rec.column_name;
        ELSE
            -- Tabla vacía: dejar en 1 sin marcar como usada (próximo = 1).
            EXECUTE format('SELECT setval(%L, 1, false)', seq_name);
            RAISE NOTICE 'Secuencia % reiniciada a 1 (tabla vacía %)',
                seq_name, rec.table_name;
        END IF;
    END LOOP;
END $$;

COMMIT;