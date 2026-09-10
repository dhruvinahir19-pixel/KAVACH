"""
telegram.py — message sending. Returns bool: True = delivered. Callers MUST treat
False as an incident (log + backlog), never swallow it. Rate-limit aware: one retry
on 429. We send <10 messages/day, far inside Telegram limits.
"""
import time

import requests

TIMEOUT = (5, 10)
RETRY_AFTER_S = 1.0


def send(text: str, token: str, chat_id: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    for attempt in (1, 2):
        try:
            r = requests.post(url, json=payload, timeout=TIMEOUT)
        except requests.RequestException:
            return False
        if r.status_code == 200:
            return True
        if r.status_code == 429 and attempt == 1:
            time.sleep(RETRY_AFTER_S)
            continue
        return False
    return False


def alert(text: str, token: str, chat_id: str, severity: str = "ALERT") -> bool:
    return send(f"[{severity}] KAVACH: {text}", token, chat_id)
