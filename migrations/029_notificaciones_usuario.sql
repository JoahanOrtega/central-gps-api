-- ============================================================================
-- 029 — Notificaciones persistentes por usuario
-- ============================================================================
-- Las alertas del sistema (hoy: eventos tipo 21 "Sin reportar" del
-- unit_state_worker; mañana: geocercas, excesos, etc.) se persisten por
-- usuario para que sobrevivan al cambio de empresa, al logout y al cierre
-- del navegador — el WS solo las muestra a quien está conectado en ese
-- instante; esta tabla es la memoria.
--
-- Diseño fan-out-on-write: una fila por destinatario. Con decenas de
-- usuarios por empresa es lo simple y correcto (patrón Slack); si algún
-- día hay miles de destinatarios por evento, migrar a fan-out-on-read
-- (tabla de eventos + tabla de lecturas).
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS t_notificaciones_usuario (
    id_notificacion  BIGSERIAL PRIMARY KEY,
    id_usuario       integer      NOT NULL,
    id_empresa       integer      NOT NULL,
    tipo             integer      NOT NULL,   -- 21 = sin reportar; futuros tipos comparten catálogo con eventos
    titulo           varchar(150) NOT NULL,
    mensaje          text,
    id_unidad        integer,                 -- deep-link opcional a la unidad
    leida            boolean      NOT NULL DEFAULT false,
    fecha_registro   timestamp    NOT NULL DEFAULT (now() AT TIME ZONE 'America/Mexico_City'),
    fecha_leida      timestamp
);

-- La campanita pide "las últimas N del usuario en esta empresa": índice
-- compuesto con orden descendente para servir la lista sin sort.
CREATE INDEX IF NOT EXISTS idx_notif_usuario_empresa_fecha
    ON t_notificaciones_usuario (id_usuario, id_empresa, fecha_registro DESC);

-- El badge pide "cuántas no leídas": índice parcial — solo indexa las
-- pendientes, que son pocas; el conteo es un index-only scan barato.
CREATE INDEX IF NOT EXISTS idx_notif_no_leidas
    ON t_notificaciones_usuario (id_usuario, id_empresa)
    WHERE leida = false;

COMMIT;