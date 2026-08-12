"""
HTTP-сервер для healthcheck-ов (Render / Railway / Docker).

Отвечает на GET /health кодом 200 без зависимостей от внешних библиотек.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
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
