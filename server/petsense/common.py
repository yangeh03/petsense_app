"""跨模块共用的小工具。"""

import time
from datetime import datetime, timezone, timedelta

_control_request_times = {}


def shanghai_now(utc_dt=None):
    return (utc_dt or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=8)))


def control_debounced(key):
    now = time.monotonic()
    last = _control_request_times.get(key, 0)
    if now - last < 0.8:
        return True
    _control_request_times[key] = now
    return False
