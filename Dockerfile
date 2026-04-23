FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
EXPOSE 5000
CMD ["sh","-lc","gunicorn -w ${WORKERS:-4} -b 0.0.0.0:${PORT:-5000} \"app:create_app()\""]
