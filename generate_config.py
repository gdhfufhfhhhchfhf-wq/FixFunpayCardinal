"""
Генератор конфигурации configs/_main.cfg из переменных окружения.

Используется при развёртывании на Render / Railway / в Docker,
где интерактивная первичная настройка (first_setup) недоступна.
"""
import os
import sys
from configparser import ConfigParser


DEFAULT_CONFIG = {
    "FunPay": {
        "golden_key": "",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
        "autoRaise": "0",
        "autoResponse": "0",
        "autoDelivery": "0",
        "multiDelivery": "0",
        "autoRestore": "0",
        "autoDisable": "0"
    },
    "Telegram": {
        "enabled": "0",
        "token": "",
        "secretKey": "funpay"
    },
    "BlockList": {
        "blockDelivery": "0",
        "blockResponse": "0",
        "blockNewMessageNotification": "0",
        "blockNewOrderNotification": "0",
        "blockCommandNotification": "0"
    },
    "NewMessageView": {
        "includeMyMessages": "1",
        "includeFPMessages": "1",
        "includeBotMessages": "0",
        "notifyOnlyMyMessages": "0",
        "notifyOnlyFPMessages": "0",
        "notifyOnlyBotMessages": "0"
    },
    "Greetings": {
        "cacheInitChats": "1",
        "sendGreetings": "0",
        "greetingsText": "Привет, $username!"
    },
    "OrderConfirm": {
        "sendReply": "1",
        "replyText": "$username, спасибо за подтверждение заказа $order_id!\n"
                     "Если не сложно, оставь, пожалуйста, отзыв!"
    },
    "ReviewReply": {
        "star1Reply": "0",
        "star2Reply": "0",
        "star3Reply": "0",
        "star4Reply": "0",
        "star5Reply": "0",
        "star1ReplyText": "",
        "star2ReplyText": "",
        "star3ReplyText": "",
        "star4ReplyText": "",
        "star5ReplyText": "",
    },
    "Proxy": {
        "enable": "0",
        "ip": "",
        "port": "",
        "login": "",
        "password": "",
        "check": "0"
    },
    "Other": {
        "watermark": "[👾 FunPay Cardinal 👻]",
        "requestsDelay": "4",
    }
}


def env_get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value != "":
            return value.strip()
    return default


def validate_config(config: ConfigParser) -> None:
    golden_key = config.get("FunPay", "golden_key", fallback="")
    if len(golden_key) != 32:
        print("FATAL: переменная окружения FUNPAY_GOLDEN_KEY обязательна "
              "(32 символа, golden key из настроек аккаунта FunPay).", file=sys.stderr)
        sys.exit(1)

    requests_delay = config.get("Other", "requestsDelay", fallback="4")
    try:
        if not (1 <= int(requests_delay) <= 100):
            raise ValueError
    except ValueError:
        print("FATAL: FUNPAY_REQUESTS_DELAY должен быть числом от 1 до 100.", file=sys.stderr)
        sys.exit(1)


def build_config() -> ConfigParser:
    config = ConfigParser(delimiters=(":", ), interpolation=None)
    config.optionxform = str
    config.read_dict(DEFAULT_CONFIG)

    config.set("FunPay", "golden_key", env_get("FUNPAY_GOLDEN_KEY"))
    config.set("FunPay", "user_agent", env_get("FUNPAY_USER_AGENT",
                                               default=DEFAULT_CONFIG["FunPay"]["user_agent"]))
    config.set("FunPay", "autoRaise", env_get("FUNPAY_AUTO_RAISE", default="0"))
    config.set("FunPay", "autoResponse", env_get("FUNPAY_AUTO_RESPONSE", default="0"))
    config.set("FunPay", "autoDelivery", env_get("FUNPAY_AUTO_DELIVERY", default="0"))
    config.set("FunPay", "multiDelivery", env_get("FUNPAY_MULTI_DELIVERY", default="0"))
    config.set("FunPay", "autoRestore", env_get("FUNPAY_AUTO_RESTORE", default="0"))
    config.set("FunPay", "autoDisable", env_get("FUNPAY_AUTO_DISABLE", default="0"))

    token = env_get("TELEGRAM_BOT_TOKEN")
    enabled = env_get("TELEGRAM_ENABLED", default="1" if token else "0")
    config.set("Telegram", "enabled", enabled)
    config.set("Telegram", "token", token)
    config.set("Telegram", "secretKey", env_get("TELEGRAM_SECRET_KEY", default="funpay"))

    config.set("Greetings", "sendGreetings", env_get("FUNPAY_GREETINGS", default="0"))
    config.set("Greetings", "greetingsText", env_get("FUNPAY_GREETINGS_TEXT",
                                                     default=DEFAULT_CONFIG["Greetings"]["greetingsText"]))
    config.set("OrderConfirm", "sendReply", env_get("FUNPAY_ORDER_CONFIRM_REPLY", default="1"))

    config.set("Proxy", "enable", env_get("PROXY_ENABLE", default="0"))
    config.set("Proxy", "ip", env_get("PROXY_IP"))
    config.set("Proxy", "port", env_get("PROXY_PORT"))
    config.set("Proxy", "login", env_get("PROXY_LOGIN"))
    config.set("Proxy", "password", env_get("PROXY_PASSWORD"))
    config.set("Proxy", "check", env_get("PROXY_CHECK", default="0"))

    config.set("Other", "requestsDelay", env_get("FUNPAY_REQUESTS_DELAY", default="4"))
    config.set("Other", "watermark", env_get("FUNPAY_WATERMARK",
                                             default=DEFAULT_CONFIG["Other"]["watermark"]))
    return config


def main() -> None:
    os.makedirs("configs", exist_ok=True)
    config = build_config()
    validate_config(config)
    with open("configs/_main.cfg", "w", encoding="utf-8") as f:
        config.write(f)
    for name in ("auto_response.cfg", "auto_delivery.cfg"):
        path = os.path.join("configs", name)
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").close()
    print("configs/_main.cfg сгенерирован из переменных окружения.")


if __name__ == "__main__":
    main()
