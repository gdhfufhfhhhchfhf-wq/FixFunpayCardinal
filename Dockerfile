FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# curl нужен для Docker HEALTHCHECK
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Данные (конфиги, storage, логи) храним в /data — сюда монтируется
# Persistent Disk (Render) / Volume (Railway) для переживания редеплоев.
RUN mkdir -p /data \
    && ln -sfn /data/configs /app/configs \
    && ln -sfn /data/storage /app/storage \
    && ln -sfn /data/logs /app/logs \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["bash", "/app/docker-entrypoint.sh"]
