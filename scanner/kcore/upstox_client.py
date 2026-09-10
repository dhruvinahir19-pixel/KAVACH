"""
upstox_client.py — authenticated Upstox market data (1-year Analytics token).

Rules (verified by research + live probes, P0/P1):
  - The free no-auth v3 endpoint does NOT serve same-day candles (verified live
    2026-09-10) — the morning job MUST use authenticated endpoints.
  - 401/403 = dead/revoked token -> raise UpstoxAuthError IMMEDIATELY (no retries;
    retrying a dead token hides the exact failure the operator must see).
  - 429/5xx -> bounded backoff retries (0.5/1/2s). Network errors -> same ladder.
"""
import time

import requests

AUTH_TIMEOUT = (5, 10)
BACKOFF = (0.5, 1.0, 2.0)


class UpstoxAuthError(RuntimeError):
    pass


class UpstoxError(RuntimeError):
    pass


class UpstoxClient:
    V3_HIST = "https://api.upstox.com/v3/historical-candle"
    V2_INTRADAY = "https://api.upstox.com/v2/intraday-candle"

    def __init__(self, token: str, timeout=AUTH_TIMEOUT, sleep=time.sleep, session=None):
        self.timeout = timeout
        self._sleep = sleep
        self.s = session or requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    # ------------------------------------------------------------------ core
    def _get(self, url: str, params: dict | None = None):
        last = None
        for attempt in range(len(BACKOFF) + 1):
            try:
                r = self.s.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                if attempt < len(BACKOFF):
                    self._sleep(BACKOFF[attempt])
                    continue
                raise UpstoxError(f"network failure after retries: {url}") from e
            if r.status_code in (401, 403):
                raise UpstoxAuthError(
                    f"Upstox token rejected (HTTP {r.status_code}) — regenerate the "
                    f"Analytics token and update UPSTOX_ACCESS_TOKEN")
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429 or r.status_code >= 500:   # transient -> backoff
                last = r
                if attempt < len(BACKOFF):
                    self._sleep(BACKOFF[attempt])
                    continue
                raise UpstoxError(f"retries exhausted (last HTTP {r.status_code}): {url}")
            r.raise_for_status()     # permanent 4xx (400/404/...): fail fast, loud
            raise UpstoxError(f"unexpected HTTP {r.status_code}: {url}")
        raise UpstoxError(f"retries exhausted (last HTTP "
                          f"{getattr(last, 'status_code', type(last).__name__)}): {url}")

    # ---------------------------------------------------------------- public
    def get_intraday_5m(self, instrument_key: str) -> list:
        """Same-day 5-min candles (v2 intraday-candle). Exact endpoint/lag verified in T1."""
        key = requests.utils.quote(instrument_key, safe="")
        data = self._get(f"{self.V2_INTRADAY}/{key}/minutes/5")
        return (data.get("data") or {}).get("candles") or []

    def get_historical_5m(self, instrument_key: str, to_iso: str, from_iso: str) -> list:
        """Completed-day 5-min candles (v3; to BEFORE from — API quirk). Max 1 month span."""
        key = requests.utils.quote(instrument_key, safe="")
        data = self._get(f"{self.V3_HIST}/{key}/minutes/5/{to_iso}/{from_iso}")
        return (data.get("data") or {}).get("candles") or []
