BEGIN;

CREATE TABLE IF NOT EXISTS public.t_grupos_whatsapp (
    id_grupo_whatsapp SERIAL PRIMARY KEY,
    id_empresa INTEGER NOT NULL,
    nombre VARCHAR(150) NOT NULL,
    chatid VARCHAR(100) NOT NULL,
    status SMALLINT DEFAULT 1 NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_t_grupos_whatsapp_empresa 
    ON public.t_grupos_whatsapp (id_empresa) WHERE status = 1;

CREATE TABLE IF NOT EXISTS public.t_alertas_grupo_whatsapp (
    id_whatsapp SERIAL PRIMARY KEY,
    id_empresa INTEGER NOT NULL,
    id_grupo_whatsapp INTEGER,
    tipo_alerta VARCHAR(20) NOT NULL CHECK (tipo_alerta IN ('geocerca', 'velocidad')),
    mensaje TEXT NOT NULL,
    fecha TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL,
    fecha_evento TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    status SMALLINT DEFAULT 0 NOT NULL,
    fecha_envio TIMESTAMP WITHOUT TIME ZONE,
    CONSTRAINT fk_alerta_grupo FOREIGN KEY (id_grupo_whatsapp) 
        REFERENCES public.t_grupos_whatsapp (id_grupo_whatsapp) ON DELETE CASCADE,
    CONSTRAINT uq_alerta_grupo_evento UNIQUE (id_grupo_whatsapp, fecha_evento, mensaje)
);

CREATE INDEX IF NOT EXISTS idx_t_alertas_whatsapp_status 
    ON public.t_alertas_grupo_whatsapp (status) WHERE status = 0;

COMMIT;