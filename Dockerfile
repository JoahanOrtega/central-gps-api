FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn gevent
COPY . .
EXPOSE 5000
CMD ["sh", "-lc", "gunicorn \
    --worker-class gevent \
    --workers ${WORKERS:-4} \
    --worker-connections ${WORKER_CONNECTIONS:-1000} \
    --timeout ${TIMEOUT:-120} \
    -b 0.0.0.0:${PORT:-5000} \
    \"app:create_app()\""]