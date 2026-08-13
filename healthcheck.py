"""
HTTP-сервер для healthcheck-ов (Render / Railway / Docker).

Отвечает на GET /health кодом 200 без зависимостей от внешних библиотек.
Эндпоинт /diag временно используется для диагностики подключения к FunPay.
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import diag_state


_fp_cache = {"status": "unknown", "ts": 0.0}
_fp_lock = threading.Lock()


def _check_funpay() -> str:
    gk = os.getenv("FUNPAY_GOLDEN_KEY")
    if not gk:
        return "no_golden_key"
    try:
        from FunPayAPI import Account
        acc = Account(golden_key=gk)
        acc.get()
        return "connected:%s" % acc.username
    except Exception as e:  # noqa: BLE001
        return "error:%s:%s" % (type(e).__name__, e)


def funpay_status() -> str:
    now = time.time()
    with _fp_lock:
        if now - _fp_cache["ts"] < 60:
            return _fp_cache["status"]
        try:
            st = _check_funpay()
        except Exception as e:  # noqa: BLE001
            st = "error:%s" % e
        _fp_cache["status"] = st
        _fp_cache["ts"] = now
        return st


def cardinal_init_status() -> str:
    try:
        return diag_state.STATE.get("cardinal_init", "unknown")[:500]
    except Exception:
        return "unknown"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path == "/diag":
            try:
                state = dict(diag_state.STATE)
            except Exception:
                state = {}
            body = ("funpay=%s diag=%s" % (funpay_status(), state)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_healthcheck() -> None:
    """
    Запускает healthcheck-сервер в отдельном демон-потоке.

    Порт берётся из переменной окружения PORT (задаётся Render/Railway),
    по умолчанию — 8080.
    """
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="healthcheck")
    thread.start()
