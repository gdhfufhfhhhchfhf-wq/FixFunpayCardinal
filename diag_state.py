"""Общий модуль для обмена диагностическим состоянием между main.py и healthcheck.py.

Так как оба модуля исполняются в одном процессе Python, изменения атрибута
STATE видны и в healthcheck (эндпоинт /diag), и в main.py.
"""
import time

START_TIME = time.time()

STATE = {
    "cardinal_init": "unknown",
}
