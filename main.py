import Utils.config_loader as cfg_loader
from first_setup import first_setup
from colorama import Fore, Style
import Utils.logger
from Utils.logger import LOGGER_CONFIG
import logging.config
import colorama
import sys
import json
import os
import threading
import time
import urllib.request
from cardinal import Cardinal
from healthcheck import start_healthcheck
import Utils.exceptions as excs


logo = "XDDDDDDDDDDDDDDDDDDDDDDD"


VERSION = "0.0.8.8"


if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(__file__))

folders = ["configs", "logs", "storage", "storage/cache", "storage/plugins", "storage/products"]
for i in folders:
    if not os.path.exists(i):
        os.makedirs(i)

files = ["configs/auto_delivery.cfg", "configs/auto_response.cfg"]
for i in files:
    if not os.path.exists(i):
        with open(i, "w", encoding="utf-8") as f:
            ...


colorama.init()


logging.config.dictConfig(LOGGER_CONFIG)
logging.raiseExceptions = False
logger = logging.getLogger("main")
logger.debug("-------------------Новый запуск.-------------------")

# Healthcheck-сервер (для Render / Railway / Docker). Порт из env PORT.
start_healthcheck()


# Keep-alive: периодический запрос к собственному /health, чтобы сервис
# не уходил в спячку при отсутствии внешнего трафика.
def _keep_alive_loop():
    port = int(os.getenv("PORT", "8080"))
    url = "http://127.0.0.1:{0}/health".format(port)
    while True:
        time.sleep(300)
        try:
            urllib.request.urlopen(url, timeout=5)
        except Exception:
            pass


threading.Thread(target=_keep_alive_loop, daemon=True, name="keep-alive").start()


print(logo)

if not os.path.exists("configs/_main.cfg"):
    first_setup()
    sys.exit()


try:
    logger.info("$MAGENTAЗагружаю конфиг _main.cfg...")
    MAIN_CFG = cfg_loader.load_main_config("configs/_main.cfg")

    logger.info("$MAGENTAЗагружаю конфиг auto_response.cfg...")
    AR_CFG = cfg_loader.load_auto_response_config("configs/auto_response.cfg")
    RAW_AR_CFG = cfg_loader.load_raw_auto_response_config("configs/auto_response.cfg")

    logger.info("$MAGENTAЗагружаю конфиг auto_delivery.cfg...")
    AD_CFG = cfg_loader.load_auto_delivery_config("configs/auto_delivery.cfg")
except excs.ConfigParseError as e:
    logger.error(e)
    logger.error("Завершаю программу...")
    sys.exit()
except UnicodeDecodeError:
    logger.error("Произошла ошибка при расшифровке UTF-8. Убедитесь, что кодировка файла = UTF-8, "
                 "а формат конца строк = LF.")
    logger.error("Завершаю программу...")
    sys.exit()
except:
    logger.critical("Произошла непредвиденная ошибка.")
    logger.debug("TRACEBACK", exc_info=True)
    logger.error("Завершаю программу...")
    sys.exit()


while True:
    try:
        Cardinal(MAIN_CFG, AD_CFG, AR_CFG, RAW_AR_CFG, VERSION).init().run()
    except KeyboardInterrupt:
        logger.info("Завершаю программу...")
        sys.exit()
    except Exception:
        logger.critical("При работе Кардинала произошла необработанная ошибка. Перезапускаю через 5 секунд...")
        logger.debug("TRACEBACK", exc_info=True)
        time.sleep(5)
