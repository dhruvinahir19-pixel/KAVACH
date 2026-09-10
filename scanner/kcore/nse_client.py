"""
nse_client.py — NSE archive downloads (bhavcopy family). UA header discipline is
mandatory (bare requests get 403). 404 is a valid outcome: not-posted-yet or holiday —
returned as None, never raised. Small timeouts, bounded retries.
"""
import time

import requests

UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
}
TIMEOUT = (5, 20)          # (connect, read) seconds
RETRIES = 2
BACKOFF = 1.0


def fetch(url: str) -> bytes | None:
    """GET with UA. Returns bytes (200) or None (404 = absent/holiday).
    Raises requests.RequestException after retries on 5xx/network errors."""
    last = None
    for attempt in range(RETRIES + 1):
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.content
        if r.status_code == 404:
            return None
        last = r
        if attempt < RETRIES:
            time.sleep(BACKOFF)
    last.raise_for_status()   # 5xx etc. after retries — loud, never silent
    raise RuntimeError("unreachable")
