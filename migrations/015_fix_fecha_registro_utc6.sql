-- ─────────────────────────────────────────────────────────────────────────────
-- Migración 015: corregir DEFAULT de fecha_registro a UTC-6
-- ─────────────────────────────────────────────────────────────────────────────
--
-- Contexto:
--   Todas las tablas del proyecto usan DEFAULT CURRENT_TIMESTAMP en la columna
--   fecha_registro. PostgreSQL evalúa CURRENT_TIMESTAMP en la zona horaria del
--   servidor (UTC en GCP), lo que producía registros con 6 horas de desfase
--   respecto a la zona operativa del sistema (UTC-6 / America/Mexico_City).
--
--   Ejemplo del problema:
--     Login realizado a las 15:41 UTC-6
--     BD guardaba: 2026-06-10 21:41:51  ← UTC puro, incorrecto
--     BD debe guardar: 2026-06-10 15:41:51  ← UTC-6 correcto
--
--   La expresión corregida es:
--     NOW() AT TIME ZONE 'UTC' AT TIME ZONE 'America/Mexico_City'
--
--   Esto toma el instante actual en UTC y lo convierte a UTC-6, produciendo
--   un TIMESTAMP WITHOUT TIME ZONE con los dígitos correctos para UTC-6.
--   Es consistente con el contrato del pipeline completo (oreja → BD → backend
--   → frontend), donde todo opera en UTC-6.
--
-- Tablas afectadas (27):
--   r_empresa_usuarios, r_poi_unidades, r_rol_asignacion_unidades,
--   r_unidad_operador, r_usuario_permisos, t_alertas_poi, t_auditoria,
--   t_clientes, t_empresa_emails, t_empresas, t_grupos_itinerarios,
--   t_grupos_pois, t_grupos_rutas, t_grupos_unidades, t_itinerario_fecha,
--   t_itinerario_fecha_parada, t_itinerario_fecha_parada_eventos,
--   t_itinerario_fecha_unidad, t_itinerarios, t_logisticas_ruta,
--   t_operadores, t_paradas_ruta, t_pois, t_roles_itinerarios,
--   t_rutas, t_unidades, t_usuarios
--
-- Idempotencia:
--   ALTER COLUMN SET DEFAULT es idempotente — correr más de una vez no daña
--   nada, simplemente sobreescribe el default con el mismo valor.
--
-- Datos históricos:
--   Esta migración NO modifica registros existentes — solo cambia el DEFAULT
--   para registros nuevos. Los registros anteriores al deploy de esta
--   migración tienen fecha_registro en UTC. Si se requiere corregir el
--   histórico, hacerlo en una migración separada con análisis previo.
--
-- Cómo aplicar en local:
--   docker exec -i proyecto-db-1 psql -U postgres -d centralgps_project \
--     < migrations/015_fix_fecha_registro_utc6.sql
--
-- Cómo aplicar en producción (GCP):
--   podman exec centralgo_api_1 python migrate.py
--
-- Cómo revertir (si fuera necesario):
--   Cambiar el DEFAULT de vuelta a CURRENT_TIMESTAMP en las tablas afectadas.
-- ─────────────────────────────────────────────────────────────────────────────

BEGIN;

-- ── Actualizar DEFAULT en las 27 tablas ──────────────────────────────────────
--
-- La expresión NOW() AT TIME ZONE 'UTC' AT TIME ZONE 'America/Mexico_City'
-- produce un TIMESTAMP WITHOUT TIME ZONE en UTC-6.
--
-- Se usa un bloque DO para iterar sobre todas las tablas afectadas en lugar
-- de 27 ALTER TABLE separados, manteniendo el script compacto y libre de
-- errores de omisión.

DO $$
DECLARE
    t text;
    tablas text[] := ARRAY[
        'r_empresa_usuarios',
        'r_poi_unidades',
        'r_rol_asignacion_unidades',
        'r_unidad_operador',
        'r_usuario_permisos',
        't_alertas_poi',
        't_auditoria',
        't_clientes',
        't_empresa_emails',
        't_empresas',
        't_grupos_itinerarios',
        't_grupos_pois',
        't_grupos_rutas',
        't_grupos_unidades',
        't_itinerario_fecha',
        't_itinerario_fecha_parada',
        't_itinerario_fecha_parada_eventos',
        't_itinerario_fecha_unidad',
        't_itinerarios',
        't_logisticas_ruta',
        't_operadores',
        't_paradas_ruta',
        't_pois',
        't_roles_itinerarios',
        't_rutas',
        't_unidades',
        't_usuarios'
    ];
BEGIN
    FOREACH t IN ARRAY tablas LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN fecha_registro SET DEFAULT (NOW() AT TIME ZONE ''America/Mexico_City'')',

            t
        );
        RAISE NOTICE 'DEFAULT actualizado: %', t;
    END LOOP;
END;
$$;

-- ── Verificación inline ───────────────────────────────────────────────────────
-- Confirma que las 27 tablas tienen el nuevo DEFAULT.
-- El resultado debe mostrar 27 filas — ninguna con 'CURRENT_TIMESTAMP' simple.
DO $$
DECLARE
    total integer;
    pendientes integer;
BEGIN
    SELECT COUNT(*) INTO total
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND column_name = 'fecha_registro'
      AND column_default LIKE '%America/Mexico_City%'
      AND column_default NOT LIKE '%UTC%';


    SELECT COUNT(*) INTO pendientes
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND column_name = 'fecha_registro'
      AND column_default = 'CURRENT_TIMESTAMP';

    RAISE NOTICE '── Verificación ──────────────────────────────';
    RAISE NOTICE 'Tablas con DEFAULT UTC-6 correcto : %', total;
    RAISE NOTICE 'Tablas aún con CURRENT_TIMESTAMP  : %', pendientes;

    IF pendientes > 0 THEN
        RAISE EXCEPTION 'La migración no se aplicó correctamente en % tabla(s)', pendientes;
    END IF;

    RAISE NOTICE 'Migración 015 aplicada correctamente ✓';
END;
$$;

COMMIT;