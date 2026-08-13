#!/usr/bin/env bash
# Точка входа контейнера: генерирует конфиг из env и запускает FunPay Cardinal.
set -e

# /app/configs, /app/storage и /app/logs — symlink на /data/* (см. Dockerfile).
# Создаём каталоги-цели в /data, иначе mkdir через симлинк упадёт с "File exists".
mkdir -p /data/configs /data/storage/cache /data/storage/plugins /data/storage/products /data/logs
mkdir -p /app/configs /app/storage/cache /app/storage/plugins /app/storage/products /app/logs

echo "[entrypoint] Генерирую configs/_main.cfg из переменных окружения (всегда)..."
python /app/generate_config.py

echo "[entrypoint] Запускаю FunPay Cardinal..."
exec python /app/main.py
