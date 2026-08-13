"""
Авто-подбор рабочего прокси из публичного списка iplocate/free-proxy-list.

Запускается в docker-entrypoint.sh ПЕРЕД запуском бота. Берёт golden key из
окружения (FUNPAY_GOLDEN_KEY) или из configs/_main.cfg, скачивает список прокси,
быстро отсеивает мёртвые (TCP-проверка) и проверяет оставшиеся на реальном
подключении к FunPay. Первый рабочий прокси прописывается в секцию [Proxy]
configs/_main.cfg, после чего бот идёт через него.

Если задана переменная PROXY_IP — ручной прокси имеет приоритет, авто-подбор
пропускается.
"""
import os
import sys
import time
import socket
import configparser
import urllib.request


LIST_URL = "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/all-proxies.txt"
CFG_PATH = "configs/_main.cfg"
MAX_TESTS = int(os.getenv("PICK_PROXY_MAX", "120"))


def get_golden_key() -> str:
    gk = os.getenv("FUNPAY_GOLDEN_KEY", "").strip()
    if gk:
        return gk
    try:
        c = configparser.ConfigParser()
        c.read(CFG_PATH)
        return c.get("FunPay", "golden_key", fallback="").strip()
    except Exception:
        return ""


def fetch_list():
    req = urllib.request.Request(LIST_URL, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=20).read().decode(errors="ignore")
    out = []
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            scheme, rest = line.split("://", 1)
        else:
            scheme, rest = "http", line
        if ":" not in rest:
            continue
        host, _, port = rest.rpartition(":")
        if not port.isdigit():
            continue
        out.append((scheme.lower(), host, int(port)))
    return out


def tcp_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def test_proxy(scheme: str, host: str, port: int):
    from FunPayAPI import Account
    url = f"{scheme}://{host}:{port}" if scheme in ("socks5", "socks4") else f"http://{host}:{port}"
    proxies = {"http": url, "https": url}
    try:
        acc = Account(golden_key=get_golden_key(), proxy=proxies, requests_timeout=6)
        acc.get()
        return True
    except Exception:
        return False


def patch_config(scheme: str, host: str, port: int) -> None:
    c = configparser.ConfigParser()
    c.read(CFG_PATH)
    if "Proxy" not in c:
        c.add_section("Proxy")
    c.set("Proxy", "enable", "1")
    c.set("Proxy", "ip", f"{scheme}://{host}")
    c.set("Proxy", "port", str(port))
    c.set("Proxy", "login", "")
    c.set("Proxy", "password", "")
    c.set("Proxy", "check", "0")
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        c.write(f)


def main() -> None:
    if os.getenv("PROXY_IP"):
        print("[pick_proxy] PROXY_IP задан вручную — авто-подбор пропускаю.")
        return
    gk = get_golden_key()
    if not gk:
        print("[pick_proxy] нет golden_key — пропускаю.")
        return

    print("[pick_proxy] загружаю список прокси...", flush=True)
    try:
        proxies = fetch_list()
    except Exception as e:
        print("[pick_proxy] не удалось загрузить список:", repr(e)[:160], flush=True)
        return
    print(f"[pick_proxy] в списке {len(proxies)} прокси, проверяю (до {MAX_TESTS})...", flush=True)

    # Жёсткие таймауты, чтобы подбор не вешал старт контейнера.
    socket.setdefaulttimeout(8)
    deadline = time.time() + int(os.getenv("PICK_PROXY_TIME", "40"))

    tested = 0
    for scheme, host, port in proxies:
        tested += 1
        if tested > MAX_TESTS or time.time() > deadline:
            break
        if not tcp_alive(host, port):
            continue
        ok = test_proxy(scheme, host, port)
        print(f"  [{tested}] {scheme}://{host}:{port} -> {'OK' if ok else 'fail'}", flush=True)
        if ok:
            print(f"[pick_proxy] РАБОЧИЙ прокси: {scheme}://{host}:{port}", flush=True)
            patch_config(scheme, host, port)
            return
    print("[pick_proxy] рабочий прокси не найден в выборке (будет запуск без прокси).", flush=True)


if __name__ == "__main__":
    main()
