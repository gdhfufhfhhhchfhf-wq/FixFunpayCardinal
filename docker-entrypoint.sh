#!/usr/bin/env bash
# Точка входа контейнера: генерирует конфиг из env и запускает FunPay Cardinal.
set -e

# /app/configs, /app/storage и /app/logs — symlink на /data/* (см. Dockerfile).
# Создаём каталоги-цели в /data, иначе mkdir через симлинк упадёт с "File exists".
mkdir -p /data/configs /data/storage/cache /data/storage/plugins /data/storage/products /data/logs
mkdir -p /app/configs /app/storage/cache /app/storage/plugins /app/storage/products /app/logs

if [ ! -f /app/configs/_main.cfg ]; then
  echo "[entrypoint] configs/_main.cfg не найден. Генерирую из переменных окружения..."
  python /app/generate_config.py
fi

echo "[entrypoint] Запускаю FunPay Cardinal..."
exec python /app/main.py
