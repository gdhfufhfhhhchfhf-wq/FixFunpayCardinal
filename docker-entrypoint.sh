#!/usr/bin/env bash
# Точка входа контейнера: генерирует конфиг из env и запускает FunPay Cardinal.
set -e

# /app/configs, /app/storage и /app/logs — symlink на /data/* (см. Dockerfile).
# Создаём каталоги-цели в /data, иначе mkdir через симлинк упадёт с "File exists".
mkdir -p /data/configs /data/storage/cache /data/storage/plugins /data/storage/products /data/logs
mkdir -p /app/configs /app/storage/cache /app/storage/plugins /app/storage/products /app/logs

echo "[entrypoint] Генерирую configs/_main.cfg из переменных окружения (всегда)..."
python /app/generate_config.py

echo "[entrypoint] Копирую шаблоны правил из репо, если их нет в /data/configs (persistent-free)..."
cp -n /app/config_templates/auto_response.cfg /data/configs/auto_response.cfg 2>/dev/null || true
cp -n /app/config_templates/auto_delivery.cfg /data/configs/auto_delivery.cfg 2>/dev/null || true

echo "[entrypoint] Запускаю FunPay Cardinal..."
exec python /app/main.py
