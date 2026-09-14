"""
market_calendar.py — independent "did the market trade today?" check (P3-11).

Incident (live 2026-09-14, Ganesh Chaturthi): the holiday was missing from
the holidays table (which had been derived from PAST data gaps and therefore
knew nothing about future holidays). The morning job treated the closed
market as a total data failure and sent ~36 error messages (P3-06 firing in
the wrong situation).

Primary gate stays: weekend check + holidays table (NSE official list).
This module is the SAFETY NET for a holiday that is missing from the table:
before aborting with "all fetches failed", ask an INDEPENDENT source whether
the market traded at all today.

Check: NIFTY 50 index (^NSEI) 5-minute bars via the Yahoo chart API (free,
browser UA — the known emergency source). Only the DATE of the newest bar
matters (robust to Yahoo's end-labeled bar convention):
  - newest bar dated TODAY   -> market traded  -> True  (real data failure)
  - newest bar dated EARLIER -> market closed -> False (holiday/unexpected)
  - unreachable / ambiguous  -> None (caller MUST stay on the loud path —
                                 never silence)

Yahoo is NEVER part of the signal chain (standing rule); this is diagnosis
only, same family as emergency_yahoo_signal.py.
"""
import datetime as dt

import requests

from . import clock

YAHOO_NIFTY = ("https://query1.finance.yahoo.com/v8/finance/chart/"
               "%5ENSEI?interval=5m&range=5d")
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36")}


def market_traded_today(today_iso=None, *, json_resp=None):
    """True = market has bars dated today (trading). False = newest bar is
    older than today (market did not trade). None = cannot determine.
    `json_resp` injectable for tests."""
    today_iso = today_iso or clock.today_key()
    try:
        resp = json_resp if json_resp is not None else _fetch()
        stamps = (resp.get("chart", {}).get("result")
                  and resp["chart"]["result"][0].get("timestamp"))
        if not stamps:
            return None
        newest = dt.datetime.fromtimestamp(max(stamps), dt.timezone.utc)
        newest_ist = newest.astimezone(clock.IST).date().isoformat()
        return newest_ist == today_iso          # True: traded; False: closed
    except Exception:                            # noqa: BLE001
        return None                              # stay loud — never silence


def _fetch():
    r = requests.get(YAHOO_NIFTY, headers=UA, timeout=(5, 10))
    r.raise_for_status()
    return r.json()
