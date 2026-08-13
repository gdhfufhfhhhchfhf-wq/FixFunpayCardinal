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
import requests

# FunPayAPI выполняет HTTP-запросы без таймаута, из-за чего init() может
# зависнуть бесконечно, если FunPay временно не отвечает. Добавляем
# таймаут по умолчанию ко всем запросам в процессе.
_ORIGINAL_SESSION_REQUEST = requests.Session.request


def _session_request_with_timeout(self, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = 60
    return _ORIGINAL_SESSION_REQUEST(self, *args, **kwargs)


requests.Session.request = _session_request_with_timeout

from cardinal import Cardinal
from healthcheck import start_healthcheck
import diag_state
import Utils.exceptions as excs
import sys
import threading
import traceback


def _record_exc(et, ev, tb):
    try:
        diag_state.STATE["last_error"] = "".join(traceback.format_exception(et, ev, tb))[-1200:]
    except Exception:
        pass


sys.excepthook = _record_exc
threading.excepthook = lambda args: _record_exc(args.exc_type, args.exc_value, args.exc_traceback)


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
diag_state.STATE["stage"] = "healthcheck_started"


# Keep-alive: периодический запрос к собственному /health, чтобы сервис
# не уходил в спячку при отсутствии внешнего трафика.
def _keep_alive_loop():
    port = int(os.getenv("PORT", "8080"))
    url = "http://127.0.0.1:{0}/health".format(port)
start_healthcheck()
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


diag_state.STATE["stage"] = "config_loaded"

while True:
    try:
        diag_state.STATE["stage"] = "before_cardinal_ctor"
        cardinal = Cardinal(MAIN_CFG, AD_CFG, AR_CFG, RAW_AR_CFG, VERSION)
        diag_state.STATE["stage"] = "after_cardinal_ctor"
        cardinal.init()
        diag_state.STATE["stage"] = "after_cardinal_init"
        diag_state.STATE["cardinal_init"] = "ok"
        try:
            tg = cardinal.MAIN_CFG["Telegram"]
            diag_state.STATE["tg_enabled"] = str(tg.getboolean("enabled"))
            diag_state.STATE["tg_token_present"] = str(bool(tg.get("token")))
            diag_state.STATE["tg_secret"] = str(tg.get("secretKey"))
            diag_state.STATE["tg_alive"] = str(cardinal.telegram.is_alive() if hasattr(cardinal, "telegram") else "noattr")
        except Exception as e:
            diag_state.STATE["tg_diag_err"] = repr(e)
        try:
            fp = cardinal.MAIN_CFG["FunPay"]
            diag_state.STATE["autoraise"] = str(fp.getboolean("autoRaise"))
            diag_state.STATE["autoresp"] = str(fp.getboolean("autoResponse"))
            diag_state.STATE["autodeliv"] = str(fp.getboolean("autoDelivery"))
            diag_state.STATE["raise_cats"] = str(fp.get("autoRaiseCategoryIds", fallback=""))
        except Exception as e:
            diag_state.STATE["fp_diag_err"] = repr(e)
        cardinal.run()
    except KeyboardInterrupt:
        logger.info("Завершаю программу...")
        sys.exit()
    except Exception:
        diag_state.STATE["stage"] = "cardinal_exception"
        diag_state.STATE["cardinal_init"] = "error:" + repr(__import__("traceback").format_exc())
        logger.critical("При работе Кардинала произошла необработанная ошибка. Перезапускаю через 5 секунд...")
        logger.debug("TRACEBACK", exc_info=True)
        time.sleep(5)
