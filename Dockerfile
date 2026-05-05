FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn gevent
COPY . .
EXPOSE 5000
# Variables configurables via docker-compose.yml environment:
#   WORKERS           : numero de workers gevent (default 4)
#   WORKER_CONNECTIONS: conexiones SSE por worker (default 1000)
#   TIMEOUT           : segundos antes de matar un worker lento (default 120)
#   PORT              : puerto de escucha (default 5000)
CMD ["sh", "-lc", "gunicorn \
    --worker-class gevent \
    --workers ${WORKERS:-4} \
    --worker-connections ${WORKER_CONNECTIONS:-1000} \
    --config gunicorn.conf.py \
    --timeout ${TIMEOUT:-120} \
    -b 0.0.0.0:${PORT:-5000} \
    \"app:create_app()\""]