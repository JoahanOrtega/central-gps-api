BEGIN;

-- ─────────────────────────────────────────────────────────────────────────────
-- Tabla de eventos por parada
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS t_itinerario_fecha_parada_eventos (
    id_evento                   SERIAL PRIMARY KEY,
    id_itinerario_fecha_parada  INTEGER NOT NULL
                                    REFERENCES t_itinerario_fecha_parada(id_itinerario_fecha_parada)
                                    ON DELETE CASCADE,
    id_itinerario_fecha_unidad  INTEGER NOT NULL
                                    REFERENCES t_itinerario_fecha_unidad(id_itinerario_fecha_unidad)
                                    ON DELETE CASCADE,

    -- Tipo de evento (ver códigos arriba)
    evento                      SMALLINT NOT NULL,

    -- Coordenadas del ping GPS que disparó el evento
    latitud                     NUMERIC(10,8),
    longitud                    NUMERIC(11,8),
    velocidad                   NUMERIC(6,2),
    odometro                    NUMERIC(10,2),
    fecha_hora_gps              TIMESTAMP NOT NULL,

    fecha_registro              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE t_itinerario_fecha_parada_eventos IS
    'Registro histórico de eventos GPS por parada. Antes t_turno_fecha_unidad_parada_eventos.
     Eventos: 1=llegada, 2=salida, 3=abordaje, 4=inicio stop, 5=fin stop.';

CREATE INDEX IF NOT EXISTS idx_ifpe_id_parada
    ON t_itinerario_fecha_parada_eventos (id_itinerario_fecha_parada);
CREATE INDEX IF NOT EXISTS idx_ifpe_id_unidad
    ON t_itinerario_fecha_parada_eventos (id_itinerario_fecha_unidad);
CREATE INDEX IF NOT EXISTS idx_ifpe_fecha_hora
    ON t_itinerario_fecha_parada_eventos (fecha_hora_gps DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- Columna para trackear si la unidad está DENTRO de la geocerca en este momento
-- (necesaria para detectar el evento de SALIDA)
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE t_itinerario_fecha_parada
    ADD COLUMN IF NOT EXISTS dentro_geocerca BOOLEAN DEFAULT FALSE;

COMMENT ON COLUMN t_itinerario_fecha_parada.dentro_geocerca IS
    'TRUE si la unidad está actualmente dentro de la geocerca de esta parada.
     El worker lo actualiza en cada ping para detectar eventos de salida.';


-- ─────────────────────────────────────────────────────────────────────────────
-- Función principal de detección
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION detectar_eventos_parada(
    p_imei          TEXT,
    p_lat           NUMERIC,
    p_lng           NUMERIC,
    p_fecha_hora    TIMESTAMP,
    p_velocidad     NUMERIC DEFAULT 0,
    p_odometro      NUMERIC DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_ifu           RECORD;   -- t_itinerario_fecha_unidad activa para el IMEI
    v_parada        RECORD;   -- parada procesada en el loop
    v_dentro        BOOLEAN;  -- si el ping está dentro de la geocerca
    v_punto_gps     GEOGRAPHY; -- el punto GPS como GEOGRAPHY para ST_DWithin
    v_paradas_abordadas  INTEGER;
    v_total_paradas      INTEGER;
    v_notify_payload     TEXT;
BEGIN
    -- Convertir el punto GPS a GEOGRAPHY una sola vez
    -- PostGIS usa (longitud, latitud) — el orden de los argumentos es x,y = lng,lat
    v_punto_gps := ST_SetSRID(
        ST_MakePoint(p_lng::float, p_lat::float),
        4326
    )::geography;

    -- ── Buscar la unidad activa para este IMEI ────────────────────────────────
    -- Puede haber múltiples asignaciones para el mismo IMEI (titular + apoyo),
    -- tomamos la más reciente que esté en curso.
    SELECT
        ifu.id_itinerario_fecha_unidad,
        ifu.id_itinerario_fecha,
        ifu.id_unidad,
        ifu.vel_max,
        ifu.paradas_abordadas,
        itf.fecha_hora_inicio,
        itf.fecha_hora_fin,
        (SELECT COUNT(*) FROM t_itinerario_fecha_parada
         WHERE id_itinerario_fecha_unidad = ifu.id_itinerario_fecha_unidad) AS total_paradas
    INTO v_ifu
    FROM t_itinerario_fecha_unidad ifu
    INNER JOIN t_itinerario_fecha itf
            ON itf.id_itinerario_fecha = ifu.id_itinerario_fecha
    WHERE ifu.imei = p_imei
      AND ifu.status = 0           -- activa
      AND itf.status = 2           -- en curso
      AND itf.fecha_hora_inicio <= p_fecha_hora
      AND itf.fecha_hora_fin    >= p_fecha_hora - INTERVAL '30 minutes'
    ORDER BY ifu.fecha_registro DESC
    LIMIT 1;

    -- Si no hay unidad activa para este IMEI, no hay nada que hacer
    IF v_ifu IS NULL THEN
        RETURN;
    END IF;

    -- ── Actualizar velocidad máxima ───────────────────────────────────────────
    IF p_velocidad > v_ifu.vel_max THEN
        UPDATE t_itinerario_fecha_unidad
        SET vel_max = p_velocidad
        WHERE id_itinerario_fecha_unidad = v_ifu.id_itinerario_fecha_unidad;
    END IF;

    -- ── Procesar cada parada pendiente o en observación ───────────────────────
    -- Procesamos paradas con status=0 (pendiente) o que están dentro_geocerca=TRUE
    -- (para detectar el evento de salida)
    FOR v_parada IN
        SELECT
            ifp.id_itinerario_fecha_parada,
            ifp.id_parada,
            ifp.numero,
            ifp.hora_abordaje_programada,
            ifp.status,
            ifp.dentro_geocerca,
            ifp.geocerca_punto,
            ifp.geocerca_radio,
            ifp.geocerca_poligono
        FROM t_itinerario_fecha_parada ifp
        WHERE ifp.id_itinerario_fecha_unidad = v_ifu.id_itinerario_fecha_unidad
          AND ifp.status IN (0, 1)  -- pendiente o llegada registrada
        ORDER BY ifp.numero
    LOOP
        -- ── Detectar si el ping está dentro de la geocerca ────────────────────
        IF v_parada.geocerca_poligono IS NOT NULL THEN
            -- Geocerca poligonal: ST_Contains
            v_dentro := ST_Contains(
                v_parada.geocerca_poligono::geometry,
                v_punto_gps::geometry
            );
        ELSE
            -- Geocerca circular: ST_DWithin con radio en metros
            v_dentro := ST_DWithin(
                v_parada.geocerca_punto,
                v_punto_gps,
                v_parada.geocerca_radio
            );
        END IF;

        -- ── Evento LLEGADA: el ping entró a la geocerca ───────────────────────
        IF v_dentro AND NOT v_parada.dentro_geocerca AND v_parada.status = 0 THEN

            -- Registrar evento de llegada
            INSERT INTO t_itinerario_fecha_parada_eventos
                (id_itinerario_fecha_parada, id_itinerario_fecha_unidad,
                 evento, latitud, longitud, velocidad, odometro, fecha_hora_gps)
            VALUES
                (v_parada.id_itinerario_fecha_parada,
                 v_ifu.id_itinerario_fecha_unidad,
                 1, p_lat, p_lng, p_velocidad, p_odometro, p_fecha_hora);

            -- Calcular diferencia con la hora programada (minutos)
            UPDATE t_itinerario_fecha_parada SET
                status            = 1,       -- llegada registrada
                dentro_geocerca   = TRUE,
                fecha_hora_llegada = p_fecha_hora,
                minutos_diferencia = EXTRACT(
                    EPOCH FROM (
                        p_fecha_hora::time - v_parada.hora_abordaje_programada
                    )
                )::integer / 60
            WHERE id_itinerario_fecha_parada = v_parada.id_itinerario_fecha_parada;

            -- Notificar al monitor en tiempo real
            v_notify_payload := json_build_object(
                'evento',           1,
                'tipo',             'llegada',
                'imei',             p_imei,
                'id_parada',        v_parada.id_parada,
                'numero_parada',    v_parada.numero,
                'fecha_hora',       p_fecha_hora,
                'id_itinerario_fecha_unidad', v_ifu.id_itinerario_fecha_unidad
            )::text;
            PERFORM pg_notify('cumplimiento_evento', v_notify_payload);

        -- ── Evento SALIDA: el ping salió de la geocerca ───────────────────────
        ELSIF NOT v_dentro AND v_parada.dentro_geocerca AND v_parada.status = 1 THEN

            -- Registrar evento de salida
            INSERT INTO t_itinerario_fecha_parada_eventos
                (id_itinerario_fecha_parada, id_itinerario_fecha_unidad,
                 evento, latitud, longitud, velocidad, odometro, fecha_hora_gps)
            VALUES
                (v_parada.id_itinerario_fecha_parada,
                 v_ifu.id_itinerario_fecha_unidad,
                 2, p_lat, p_lng, p_velocidad, p_odometro, p_fecha_hora);

            -- Marcar parada como abordada
            UPDATE t_itinerario_fecha_parada SET
                status           = 2,       -- abordada (completada)
                dentro_geocerca  = FALSE,
                fecha_hora_salida = p_fecha_hora
            WHERE id_itinerario_fecha_parada = v_parada.id_itinerario_fecha_parada;

            -- Notificar salida
            v_notify_payload := json_build_object(
                'evento',           2,
                'tipo',             'salida',
                'imei',             p_imei,
                'id_parada',        v_parada.id_parada,
                'numero_parada',    v_parada.numero,
                'fecha_hora',       p_fecha_hora,
                'id_itinerario_fecha_unidad', v_ifu.id_itinerario_fecha_unidad
            )::text;
            PERFORM pg_notify('cumplimiento_evento', v_notify_payload);

        -- ── Actualizar flag dentro_geocerca si no hubo cambio de estado ───────
        ELSE
            UPDATE t_itinerario_fecha_parada
            SET dentro_geocerca = v_dentro
            WHERE id_itinerario_fecha_parada = v_parada.id_itinerario_fecha_parada
              AND dentro_geocerca != v_dentro;
        END IF;

    END LOOP;

    -- ── Actualizar métricas globales de la unidad ─────────────────────────────
    SELECT COUNT(*) INTO v_paradas_abordadas
    FROM t_itinerario_fecha_parada
    WHERE id_itinerario_fecha_unidad = v_ifu.id_itinerario_fecha_unidad
      AND status = 2;  -- abordadas

    v_total_paradas := v_ifu.total_paradas;

    UPDATE t_itinerario_fecha_unidad SET
        paradas_abordadas       = v_paradas_abordadas,
        porcentaje_cumplimiento = CASE
            WHEN v_total_paradas > 0
            THEN ROUND((v_paradas_abordadas::numeric / v_total_paradas) * 100, 2)
            ELSE 0
        END,
        en_ruta                 = TRUE,
        en_curso                = TRUE,
        fecha_hora_update       = NOW()
    WHERE id_itinerario_fecha_unidad = v_ifu.id_itinerario_fecha_unidad;

END;
$$;

COMMENT ON FUNCTION detectar_eventos_parada IS
    'Procesa un ping GPS y detecta eventos de llegada/salida en paradas.
     Llamada por el worker Python en cada ciclo.
     Usa ST_DWithin() para geocercas circulares y ST_Contains() para poligonales.
     Emite pg_notify(''cumplimiento_evento'') para el monitor en tiempo real (3C).';

COMMIT;