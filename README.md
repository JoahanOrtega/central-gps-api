# central-gps-api

API REST de **CentralGPS**, plataforma de rastreo y gestión de vehículos por GPS. Expone los servicios que consume el frontend [`central-gps`].

## Stack

- **Flask 3.1** como framework web
- **PostgreSQL** con **PostGIS** (geometrías) vía **psycopg2**
- **TimescaleDB** (en GCP) para la telemetría de alta frecuencia
- **JWT** (Flask-JWT-Extended) para autenticación, con refresh tokens
- **marshmallow** para validación de entrada
- **Redis** para caché de geocodificación y soporte de SSE
- **APScheduler** para tareas programadas (workers)
- **shapely** para cálculos geométricos de geocercas
- **flask-sock** / **simple-websocket** para WebSocket (monitor en vivo)
- **gunicorn** + **gevent** como servidor de producción

## Ejecución con Docker (recomendado)

En desarrollo, la API corre dentro de un contenedor orquestado por el repo de infraestructura (`docker-compose.yml` / `podman compose`), junto con la base de datos y Redis. La imagen se construye a partir del `Dockerfile` de este repo.

Desde el repo de infraestructura:

```bash
podman compose up -d --build        # levanta API + DB + Redis
podman compose ps                   # verifica que los contenedores estén healthy
```

> **Importante:** las imágenes usan `COPY` al construirse, así que `podman compose restart` **no** recarga el código. Tras cambiar código, reconstruye con `--build`.

Las migraciones se aplican dentro del contenedor:

```bash
podman exec centralgo_api_1 python migrate.py
```

## Ejecución standalone (sin Docker)

Para correr la API de forma aislada, fuera del entorno orquestado:

```bash
python -m venv venv
source venv/bin/activate        # o venv\Scripts\activate
pip install -r requirements.txt
flask run                       # desarrollo
```

Producción standalone (gunicorn + gevent, según `gunicorn.conf.py`):

```bash
gunicorn -c gunicorn.conf.py app:app
```

## Variables de entorno

Crea un archivo `.env` en la raíz con la configuración de conexión y secretos. Como mínimo:

```
DATABASE_URL=postgresql://usuario:password@host:puerto/centralgps_project
TIMESCALE_URL=postgresql://usuario:password@host:puerto/telemetria
REDIS_URL=redis://localhost:6379/0
JWT_SECRET_KEY=tu_secreto
GOOGLE_MAPS_API_KEY=tu_clave   # geocodificación del lado servidor
```

Revisa `config.py` para la lista completa de variables que el proyecto reconoce.

## Migraciones

El esquema se versiona con migraciones SQL numeradas en `migrations/`. Para aplicarlas:

```bash
python migrate.py                              # standalone
podman exec centralgo_api_1 python migrate.py  # con docker
```

Cada migración se registra en la tabla `schema_migrations`, de modo que solo se aplican las pendientes.

## Autenticación

La API usa JWT. El token incluye el `sub` (identificador del usuario) y el contexto de empresa. La mayoría de los endpoints requieren un token válido; el de rastreo público es la excepción, donde el token de la URL actúa como credencial.

## Zona horaria

Todo el procesamiento de timestamps se hace en `America/Mexico_City` (UTC-6), de forma consistente con el frontend y la base de datos.