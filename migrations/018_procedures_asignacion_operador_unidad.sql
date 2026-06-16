-- =============================================================================
-- Migración 018 — Procedures de asignación operador↔unidad
-- =============================================================================
--
-- Contexto:
--   La relación operador↔unidad es EXCLUSIVA 1:1 — un operador maneja una
--   sola unidad y una unidad tiene un solo operador. Asignar implica:
--     1. Desasignar cualquier vínculo previo de AMBOS lados (el operador
--        podía tener otra unidad, la unidad otro operador).
--     2. Crear el nuevo vínculo en r_unidad_operador.
--     3. Sincronizar id_unidad_operador en t_operadores y t_unidades.
--
--   Esta lógica se implementa con procedures (portados del v3.0) en lugar de
--   Python por decisión explícita. NOTA PARA MANTENEDORES: es la única parte
--   del sistema con lógica en stored procedures; el resto vive en services
--   Python. Si se refactoriza a Python en el futuro, replicar exactamente la
--   semántica de exclusividad de aquí.
--
-- Adaptaciones vs v3.0:
--   - id_usuario como INTEGER (no VARCHAR(50)): coincide con el tipo real
--     de id_usuario_registro/cambio en r_unidad_operador.
--   - id_unidad_operador = 0 como "sin asignación" (mismo sentinel que v3.0).
--
-- Procedures:
--   desasignar_unidad_operador(id_unidad, id_operador, id_usuario)
--   asignar_operador_unidad(id_unidad, id_operador, id_usuario, fecha_asignacion)
--
-- Cómo aplicar:
--   docker exec proyecto-api-1 python migrate.py
-- =============================================================================

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- DESASIGNAR — rompe el vínculo de la unidad y/o el operador indicados.
-- Pone id_unidad_operador = 0 en ambas tablas y sella la fila de relación
-- con fecha/usuario de cambio (auditoría; no se borra físicamente).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE PROCEDURE desasignar_unidad_operador(
    IN id_unidad_in   INTEGER,
    IN id_operador_in INTEGER,
    IN id_usuario_in  INTEGER
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF id_unidad_in <> 0 THEN
        UPDATE t_unidades u
        SET id_unidad_operador = 0
        FROM r_unidad_operador r
        WHERE r.id_unidad_operador = u.id_unidad_operador
          AND u.id_unidad = id_unidad_in;

        UPDATE t_operadores o
        SET id_unidad_operador = 0
        FROM r_unidad_operador r
        INNER JOIN t_unidades u ON u.id_unidad_operador = r.id_unidad_operador
        WHERE o.id_unidad_operador = r.id_unidad_operador
          AND u.id_unidad = id_unidad_in;

        UPDATE r_unidad_operador r
        SET id_usuario_cambio = id_usuario_in,
            fecha_cambio = NOW()
        FROM t_unidades u
        WHERE r.id_unidad_operador = u.id_unidad_operador
          AND u.id_unidad = id_unidad_in;
    END IF;

    IF id_operador_in <> 0 THEN
        UPDATE t_operadores o
        SET id_unidad_operador = 0
        FROM r_unidad_operador r
        WHERE r.id_unidad_operador = o.id_unidad_operador
          AND o.id_operador = id_operador_in;

        UPDATE t_unidades u
        SET id_unidad_operador = 0
        FROM r_unidad_operador r
        INNER JOIN t_operadores o ON o.id_unidad_operador = r.id_unidad_operador
        WHERE u.id_unidad_operador = r.id_unidad_operador
          AND o.id_operador = id_operador_in;

        UPDATE r_unidad_operador r
        SET id_usuario_cambio = id_usuario_in,
            fecha_cambio = NOW()
        FROM t_operadores o
        WHERE r.id_unidad_operador = o.id_unidad_operador
          AND o.id_operador = id_operador_in;
    END IF;
END;
$$;

-- ─────────────────────────────────────────────────────────────────────────────
-- ASIGNAR — vincula operador y unidad de forma exclusiva.
-- Primero desasigna vínculos previos de ambos lados; luego crea el nuevo y
-- sincroniza id_unidad_operador en las tres tablas. No hace nada si ya están
-- vinculados entre sí (idempotente).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE PROCEDURE asignar_operador_unidad(
    IN id_unidad_in        INTEGER,
    IN id_operador_in      INTEGER,
    IN id_usuario_in       INTEGER,
    IN fecha_asignacion_in VARCHAR(15)
)
LANGUAGE plpgsql
AS $$
DECLARE
    id_unidad_actual      INTEGER := 0;
    id_operador_actual    INTEGER := 0;
    nuevo_id_unidad_oper  INTEGER;
BEGIN
    -- ¿Qué operador tiene actualmente la unidad?
    IF id_unidad_in <> 0 THEN
        SELECT COALESCE(r.id_operador, 0) INTO id_operador_actual
        FROM t_unidades u
        LEFT JOIN r_unidad_operador r ON r.id_unidad_operador = u.id_unidad_operador
        WHERE u.id_unidad = id_unidad_in;
    END IF;

    -- ¿Qué unidad tiene actualmente el operador?
    IF id_operador_in <> 0 THEN
        SELECT COALESCE(r.id_unidad, 0) INTO id_unidad_actual
        FROM t_operadores o
        LEFT JOIN r_unidad_operador r ON r.id_unidad_operador = o.id_unidad_operador
        WHERE o.id_operador = id_operador_in;
    END IF;

    -- Si el vínculo cambia, romper los anteriores de ambos lados.
    IF id_operador_actual <> id_operador_in OR id_unidad_in <> id_unidad_actual THEN
        CALL public.desasignar_unidad_operador(id_unidad_in, id_operador_in, id_usuario_in);
    END IF;

    -- Crear el nuevo vínculo (solo si ambos IDs son válidos y cambia algo).
    IF id_operador_in <> 0 AND id_unidad_in <> 0
       AND (id_operador_actual <> id_operador_in OR id_unidad_in <> id_unidad_actual) THEN
        INSERT INTO r_unidad_operador (
            id_operador, id_unidad, id_usuario_registro, fecha_registro, fecha_asignacion
        )
        VALUES (
            id_operador_in, id_unidad_in, id_usuario_in, NOW(), fecha_asignacion_in::date
        )
        RETURNING id_unidad_operador INTO nuevo_id_unidad_oper;

        UPDATE t_operadores
        SET id_unidad_operador = nuevo_id_unidad_oper
        WHERE id_operador = id_operador_in;

        UPDATE t_unidades
        SET id_unidad_operador = nuevo_id_unidad_oper
        WHERE id_unidad = id_unidad_in;
    END IF;
END;
$$;

COMMIT;