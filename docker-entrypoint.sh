#!/usr/bin/env bash
# Точка входа контейнера: генерирует конфиг из env и запускает FunPay Cardinal.
set -e

mkdir -p /app/configs /app/storage/cache /app/storage/plugins /app/storage/products /app/logs

if [ ! -f /app/configs/_main.cfg ]; then
  echo "[entrypoint] configs/_main.cfg не найден. Генерирую из переменных окружения..."
  python /app/generate_config.py
fi

echo "[entrypoint] Запускаю FunPay Cardinal..."
exec python /app/main.py
