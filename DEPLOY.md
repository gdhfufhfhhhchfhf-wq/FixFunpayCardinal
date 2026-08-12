# Деплой FunPay Cardinal в облако (Render / Railway)

Репозиторий подготовлен для автоматического развёртывания:
`Dockerfile`, `docker-compose.yml`, healthcheck (`GET /health`),
генерация конфига из переменных окружения.

## Переменные окружения

| Переменная | Обязательна | Описание |
|---|---|---|
| `FUNPAY_GOLDEN_KEY` | да (32 симв.) | Golden key FunPay: Настройки → API-ключ |
| `TELEGRAM_BOT_TOKEN` | нет* | Токен бота от @BotFather (*если задан — TG-бот включится) |
| `TELEGRAM_SECRET_KEY` | нет | Пароль доступа к панели TG (по умолч. `funpay`) |
| `FUNPAY_USER_AGENT` | нет | User-agent |
| `FUNPAY_AUTO_RAISE` / `FUNPAY_AUTO_RESPONSE` / `FUNPAY_AUTO_DELIVERY` | нет | Автофункции: `1`/`0` |
| `FUNPAY_MULTI_DELIVERY` / `FUNPAY_AUTO_RESTORE` / `FUNPAY_AUTO_DISABLE` | нет | Автофункции: `1`/`0` |
| `FUNPAY_REQUESTS_DELAY` | нет | Задержка между запросами FunPay (1–100, по умолч. 4) |
| `PROXY_ENABLE` / `PROXY_IP` / `PROXY_PORT` / `PROXY_LOGIN` / `PROXY_PASSWORD` / `PROXY_CHECK` | нет | Прокси |
| `FUNPAY_WATERMARK` | нет | Водяной знак сообщений |

## Render (рекомендуется)

1. Dashboard → **New → Web Service** → подключить GitHub-репозиторий.
2. Render сам определит **Dockerfile**. Build/Start Command оставить пустыми.
3. **Health Check Path:** `/health`.
4. Добавить переменные окружения из таблицы выше.
5. Создать **Persistent Disk** (например, 1 GB) и смонтировать в `/data`
   (конфиги/логи переживут редеплои).
6. **Auto-Deploy:** on.
7. Нажать **Create Web Service**. Render соберёт образ, запустит и будет
   пинговать `/health` (интервал 30 с / таймаут 10 с / 3 попытки — в Dockerfile).

## Railway

1. Railway → **New Project → Deploy from GitHub** → выбрать репозиторий.
2. Railway сам определит **Dockerfile**.
3. Добавить переменные окружения.
4. **Volumes:** создать volume и смонтировать в `/data` (для конфигов/логов).
5. Railway автоматически слушает `PORT` и держит сервис живым по логам;
   для публичного healthcheck включите **Public Networking** на нужный порт.

## Локально (Docker)

```bash
cp .env.example .env   # заполнить FUNPAY_GOLDEN_KEY и TELEGRAM_BOT_TOKEN
docker compose up -d --build
docker compose ps      # статус + healthcheck
curl http://localhost:8080/health
```

## Проверка

- `GET /health` → `200 OK`.
- Логи: Render — вкладка **Logs**; Railway — вкладка **Deployments/Logs**.
- Telegram: напишите своему боту и отправьте `TELEGRAM_SECRET_KEY` для доступа к панели (`/menu`).
