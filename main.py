import time
from pip._internal.cli.main import main as pip_main

try:
    import lxml
except ModuleNotFoundError:
    pip_main(["install", "-U", "lxml>=5.3.0"])
except:
    pass
try:
    import bcrypt
except ModuleNotFoundError:
    pip_main(["install", "-U", "bcrypt>=4.2.0"])
except:
    pass
try:
    import socks
except ModuleNotFoundError:
    pip_main(["install", "-U", "pysocks>=1.7.1"])
except:
    pass
import Utils.cardinal_tools
import Utils.config_loader as cfg_loader
from first_setup import first_setup
from colorama import Fore, Style
from Utils.logger import configure_logging
import logging
import colorama
import sys
import os
import threading
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
from locales.localizer import Localizer
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


# Инструментовка Runner.get_updates: пульс и ошибки поллинга сообщений в /diag
import FunPayAPI.updater.runner as _fpc_runner_mod
_orig_get_updates = _fpc_runner_mod.Runner.get_updates


def _safe_get_updates(self, *args, **kwargs):
    try:
        result = _orig_get_updates(self, *args, **kwargs)
        diag_state.STATE["runner_ok_at"] = time.strftime("%H:%M:%S")
        diag_state.STATE["runner_err"] = ""
        return result
    except Exception as e:
        diag_state.STATE["runner_err"] = f"{type(e).__name__}: {e}"
        diag_state.STATE["runner_err_at"] = time.strftime("%H:%M:%S")
        raise


_fpc_runner_mod.Runner.get_updates = _safe_get_updates


logo = "[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m[38;5;0m.[0m"

VERSION = "0.1.17.13"

Utils.cardinal_tools.set_console_title(f"FunPay Cardinal v{VERSION}")

if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(__file__))

folders = ["configs", "logs", "storage", "storage/cache", "storage/plugins", "storage/products", "plugins"]
for i in folders:
    if not os.path.exists(i):
        os.makedirs(i)

files = ["configs/auto_delivery.cfg", "configs/auto_response.cfg"]
for i in files:
    if not os.path.exists(i):
        with open(i, "w", encoding="utf-8") as f:
            ...

colorama.init()

configure_logging()
logging.raiseExceptions = False
logger = logging.getLogger("main")
logger.debug("-------------------Новый запуск.-------------------")

# Healthcheck-сервер (для Render / Railway / Docker). Порт из env PORT.
start_healthcheck()
diag_state.STATE["stage"] = "healthcheck_started"


# Keep-alive: периодический запрос к собственному публичному URL (RENDER_EXTERNAL_URL),
# чтобы Render free не усыплял инстанс после 15 минут без внешнего трафика.
def _keep_alive_loop():
    port = int(os.getenv("PORT", "8080"))
    public = os.getenv("RENDER_EXTERNAL_URL")
    url = (public.rstrip("/") + "/health") if public else "http://127.0.0.1:{0}/health".format(port)
    while True:
        time.sleep(240)
        try:
            urllib.request.urlopen(url, timeout=20)
        except Exception:
            pass


threading.Thread(target=_keep_alive_loop, daemon=True, name="keep-alive").start()


# Присутствие в FunPay: лёгкий авторизованный GET главной страницы каждые 2 минуты,
# чтобы аккаунт показывался в FunPay как «в сети» и сессия не протухала.
def _funpay_presence_loop(cardinal):
    while True:
        time.sleep(120)
        try:
            if cardinal.account and cardinal.account.phpsessid:
                cardinal.account.session.get("https://funpay.com/", timeout=15)
                diag_state.STATE["presence_at"] = time.strftime("%H:%M:%S")
        except Exception:
            pass


print(f"{Style.RESET_ALL}{logo}")
print(f"{Fore.RED}{Style.BRIGHT}v{VERSION}{Style.RESET_ALL}\n")
print(f"{Fore.MAGENTA}{Style.BRIGHT}By {Fore.BLUE}{Style.BRIGHT}Woopertail, @sidor0912{Style.RESET_ALL}")

if not os.path.exists("configs/_main.cfg"):
    first_setup()
    sys.exit()


try:
    logger.info("$MAGENTAЗагружаю конфиг _main.cfg...")
    MAIN_CFG = cfg_loader.load_main_config("configs/_main.cfg")
    localizer = Localizer(MAIN_CFG["Other"]["language"])
    _ = localizer.translate

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
    logger.warning("TRACEBACK", exc_info=True)
    logger.error("Завершаю программу...")
    sys.exit()

localizer = Localizer(MAIN_CFG["Other"]["language"])

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
            threading.Thread(target=_funpay_presence_loop, args=(cardinal,), daemon=True,
                             name="funpay-presence").start()
        except Exception as e:
            diag_state.STATE["presence_thread"] = f"error: {e}"
        try:
            tg = cardinal.MAIN_CFG["Telegram"]
            diag_state.STATE["tg_enabled"] = str(tg.getboolean("enabled"))
            diag_state.STATE["tg_token_present"] = str(bool(tg.get("token")))
            diag_state.STATE["tg_secret"] = str(tg.get("secretKey"))
            diag_state.STATE["tg_alive"] = str(getattr(cardinal, "telegram_thread", None).is_alive() if getattr(cardinal, "telegram_thread", None) else "not_started")
        except Exception as e:
            diag_state.STATE["tg_diag_err"] = repr(e)
        try:
            fp = cardinal.MAIN_CFG["FunPay"]
            diag_state.STATE["autoraise"] = str(fp.getboolean("autoRaise"))
            diag_state.STATE["autoresp"] = str(fp.getboolean("autoResponse"))
            diag_state.STATE["autodeliv"] = str(fp.getboolean("autoDelivery"))
            diag_state.STATE["raise_cats"] = str(fp.get("autoRaiseCategoryIds", fallback=""))
            diag_state.STATE["ar_commands"] = str(sorted(AR_CFG.sections()))
        except Exception as e:
            diag_state.STATE["fp_diag_err"] = repr(e)
        cardinal.run()
    except KeyboardInterrupt:
        logger.info("Завершаю программу...")
        sys.exit()
    except Exception:
        diag_state.STATE["stage"] = "cardinal_exception"
        diag_state.STATE["cardinal_init"] = "error:" + repr(__import__("traceback").format_exc())
        logger.critical("При работе Кардинала произошла необработанная ошибка. Перезапускаю через 30 секунд...")
        logger.warning("TRACEBACK", exc_info=True)
        time.sleep(30)
