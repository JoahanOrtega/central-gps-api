BEGIN;

DROP TABLE IF EXISTS public.t_alertas_grupo_whatsapp CASCADE;
DROP TABLE IF EXISTS public.t_grupos_whatsapp CASCADE;

CREATE TABLE IF NOT EXISTS public.t_destinos_whatsapp (
    id_destino_whatsapp SERIAL PRIMARY KEY,
    id_empresa INTEGER NOT NULL,
    nombre VARCHAR(150) NOT NULL,
    tipo VARCHAR(10) NOT NULL CHECK (tipo IN ('grupo', 'persona')),
    chatid VARCHAR(100),
    telefono VARCHAR(20),
    status SMALLINT DEFAULT 1 NOT NULL,
    CONSTRAINT chk_destino_contacto CHECK (
        (tipo = 'persona' AND (telefono IS NOT NULL OR chatid IS NOT NULL)) OR 
        (tipo = 'grupo' AND chatid IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_t_destinos_whatsapp_empresa 
    ON public.t_destinos_whatsapp (id_empresa) WHERE status = 1;

CREATE TABLE IF NOT EXISTS public.t_alertas_whatsapp (
    id_whatsapp SERIAL PRIMARY KEY,
    id_empresa INTEGER NOT NULL,
    id_destino_whatsapp INTEGER,
    tipo_alerta VARCHAR(20) NOT NULL CHECK (tipo_alerta IN ('geocerca', 'velocidad')),
    mensaje TEXT NOT NULL,
    fecha TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL,
    fecha_evento TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    status SMALLINT DEFAULT 0 NOT NULL,
    fecha_envio TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT fk_alerta_destino FOREIGN KEY (id_destino_whatsapp) 
        REFERENCES public.t_destinos_whatsapp (id_destino_whatsapp) ON DELETE CASCADE,
    CONSTRAINT uq_alerta_destino_evento UNIQUE (id_destino_whatsapp, fecha_evento, mensaje)
);

CREATE INDEX IF NOT EXISTS idx_t_alertas_whatsapp_status 
    ON public.t_alertas_whatsapp (status) WHERE status = 0;

COMMIT;