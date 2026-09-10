"""
morning.py — P3: the Morning 9:45 engine (production).

Reproduces the validated research logic EXACTLY (engine/morning.py +
engine/protocol_v2.py — the 986-trade / worst-day -4.80% book):

  candidates  = last evening's top-10 watchlist (P2 output)
  OR15        = high/low of 5-min bars STAMPED 09:15..09:29 (covers 09:15-09:30)
  confirm     = bars STAMPED 09:30..09:44; c945 = close of the 09:40 bar
                (the bar that completes at 09:45:00)
  zero-volume bars are DROPPED (research candle store never kept them)
  LONG        = c945 > or15_h     SHORT = c945 < or15_l
  cap/side    = >3 confirmed -> keep the 2 CALMEST by
                range20 = (prior20h - prior20l) / c945   [lowest first]
  no confirm  -> FLAT morning (no forced trades — research rule 3)
  entry       = c945 ; disaster stop = 1.0% adverse

Data source: Upstox v3 INTRADAY candle API, PUBLIC — verified live
2026-09-10 after close: HTTP 200 with no auth, 75 bars, last closes ==
NSE official bhavcopy closes (IDEA 14.90 == 14.90). Bars stamped >= 09:45
are DISCARDED always: the last (forming) candle sits behind a ~30s CDN
cache (UpstoxSupport, Jan 2026) — at fetch time the 09:40 bar is
second-to-last and therefore exact. Fetch at >= 09:45:40 only.

Job semantics mirror evening.py: return "skipped:..." for benign states,
raise = failed + alert. Idempotent: re-trigger overwrites the day's rows.
"""
import datetime as dt
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from kcore import clock                                # noqa: E402
from kcore.neon_store import connect as db_connect     # noqa: E402
from evening import load_holidays                      # noqa: E402

# ------------------------------------------------------------------ constants
WIN_LO, WIN_HI = (9, 40), (9, 49)   # job window 09:40..09:49:59 IST
OR_LO, OR_HI = 555, 569             # bar-stamp minute-of-day (IST): 09:15..09:29
CN_LO, CN_HI = 570, 584             # 09:30..09:44; the 09:40 bar completes 09:45:00
C945_READY = (9, 45, 40)            # wall time after which the 09:40 bar is final
THRESHOLD = 3                       # filter a side only when candidates exceed this
TOPN = 2                            # how many to keep when filtering
SL_PCT = 0.01                       # disaster stop 1.0% adverse from entry
FETCH_TRIES, FETCH_SLEEP = 3, 5

UPSTOX = "https://api.upstox.com/v3/historical-candle/intraday"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Accept": "application/json"}


# ------------------------------------------------------------------ data
class StaleDataError(RuntimeError):
    """Intraday response is not today's session (e.g. a previous-day cache).
    Never compute OR15/c945 from stale bars — that would signal yesterday's
    market as if it were today (P3-05, silent-killer)."""


def _parse_intraday(json_resp, today_iso):
    """-> [(mod, o, h, l, c, v)], zero-volume bars dropped. Raises
    StaleDataError when the newest bar is not dated TODAY."""
    out = []
    for c in json_resp.get("data", {}).get("candles", []):
        if len(c) < 6:
            continue
        try:
            vol = int(c[5])
            if vol <= 0:
                continue                             # filler bar
            t = dt.datetime.fromisoformat(c[0])
            out.append((t.date().isoformat(), t.hour * 60 + t.minute,
                        float(c[1]), float(c[2]), float(c[3]), float(c[4]), vol))
        except (ValueError, TypeError):
            continue
    if out and max(b[0] for b in out) != today_iso:
        raise StaleDataError(f"intraday bars dated {max(b[0] for b in out)}, "
                             f"expected {today_iso} (stale/previous-session)")
    return [b[1:] for b in out]


def _intraday_http(instrument_key, token=None, today_iso=None):
    """One HTTP GET (public, or Bearer-authenticated when token given)."""
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(FETCH_TRIES):
        try:
            r = requests.get(f"{UPSTOX}/{instrument_key}/minutes/5",
                             headers=headers, timeout=15)
            if r.status_code == 200:
                return _parse_intraday(r.json(),
                                       today_iso or clock.today_key())
            if r.status_code in (500, 502, 503, 504):
                time.sleep(FETCH_SLEEP * (attempt + 1))
                continue
            raise RuntimeError(f"upstox {r.status_code}: {r.text[:80]}")
        except requests.RequestException as e:
            if attempt == FETCH_TRIES - 1:
                raise RuntimeError(f"upstox unreachable: {e}") from e
            time.sleep(FETCH_SLEEP)
    raise RuntimeError("upstox failed after retries")


def load_token():
    """Analytics token from env var UPSTOX_ACCESS_TOKEN or ~/.upstox_token
    (mode 600). Never hardcoded, never committed, never logged."""
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if tok:
        return tok
    try:
        with open("/home/user/.upstox_token") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def fetch_intraday_bars(instrument_key, token=None):
    """PUBLIC call first (verified working after close 2026-09-10); if it
    fails during live market (user reports it may not serve live data
    without auth), fall back to the AUTHENTICATED call when a token is
    configured. Both paths validate bars are from TODAY's session."""
    last = None
    try:
        return _intraday_http(instrument_key)
    except Exception as e:                               # noqa: BLE001
        last = e
        tok = token or load_token()
        if not tok:
            raise
    try:
        return _intraday_http(instrument_key, tok)
    except Exception as e2:                              # noqa: BLE001
        raise RuntimeError(f"public ({last}) | auth ({e2})") from e2


# ------------------------------------------------------------------ bar math
def morning_metrics(bars):
    """bars: [(mod, o, h, l, c, v)]. Zero-volume filler bars are dropped HERE
    as defense-in-depth (the fetcher drops them too) — the research morning
    table was built from a store that never kept them."""
    bars = [b for b in bars if b[5] > 0]
    orw = [b for b in bars if OR_LO <= b[0] <= OR_HI]
    cnf = [b for b in bars if CN_LO <= b[0] <= CN_HI]
    return {
        "or15_h": max(b[2] for b in orw) if orw else None,
        "or15_l": min(b[3] for b in orw) if orw else None,
        "c945": cnf[-1][4] if cnf else None,   # last confirm-window close
    }


def confirm_book(picks):
    """picks: DataFrame(symbol, prob, prior, prior20h, prior20l, or15_h,
    or15_l, c945). Confirm + cap — mirrors protocol_v2.select_book exactly."""
    rows = []
    for r in picks.itertuples():
        if r.or15_h is None or r.or15_l is None or r.c945 is None:
            continue                          # no usable morning data -> excluded
        if r.c945 > r.or15_h:
            rows.append(dict(symbol=r.symbol, side=1, c945=r.c945,
                             or15_h=r.or15_h, or15_l=r.or15_l, prob=r.prob,
                             prior=r.prior, prior20h=r.prior20h,
                             prior20l=r.prior20l))
        elif r.c945 < r.or15_l:
            rows.append(dict(symbol=r.symbol, side=-1, c945=r.c945,
                             or15_h=r.or15_h, or15_l=r.or15_l, prob=r.prob,
                             prior=r.prior, prior20h=r.prior20h,
                             prior20l=r.prior20l))
    c = pd.DataFrame(rows)
    if not len(c):
        return c
    c["range20_m"] = (c["prior20h"] - c["prior20l"]) / c["c945"]
    keep = []
    for _, g in c.groupby("side"):
        if len(g) <= THRESHOLD:
            keep.append(g)
        else:                                 # calmest = lowest range20 first
            keep.append(g.sort_values("range20_m", kind="stable").head(TOPN))
    return pd.concat(keep).reset_index(drop=True)


def format_message(T, book, version, excluded, capped):
    lines = [f"KAVACH-945 Morning — {T} (model {version})"]
    if not len(book):
        lines.append("NO CONFIRMATION — stay flat.")
    for i, r in enumerate(book.itertuples(), 1):
        side = "LONG " if r.side == 1 else "SHORT"
        stop = r.c945 * (1 - SL_PCT) if r.side == 1 else r.c945 * (1 + SL_PCT)
        lines.append(f"{i}. {side} {r.symbol} @ {r.c945:.2f} | SL {stop:.2f} "
                     f"| OR15 {r.or15_l:.2f}-{r.or15_h:.2f} "
                     f"| basis {'▲' if r.prior == 1 else '▼' if r.prior == -1 else '—'}")
    if capped:
        lines.append(f"(cap: {capped})")
    if excluded:
        lines.append(f"no-data: {', '.join(excluded)}")
    lines.append("enter by ~09:47 · SL is a disaster-stop, not a target")
    return "\n".join(lines)


# ------------------------------------------------------------------ job
def run_morning(ctx, fetch_fn=None):
    """Evening-style ctx: connect / send / alert. Returns 'done:N' / 'flat:N'
    or 'skipped:...'; raises = failed + alert."""
    now = clock.now()
    T = clock.today_key()
    if now.weekday() >= 5:
        return "skipped:weekend"
    conn = ctx["connect"]()
    try:
        if T in load_holidays(conn):
            return "skipped:holiday"
        if not (now.hour == WIN_LO[0] and WIN_LO[1] <= now.minute <= WIN_HI[1]):
            raise RuntimeError(f"morning job off-schedule at {now:%H:%M} IST "
                               f"(window 09:40-09:50, P3-10)")
        if (now.hour, now.minute, now.second) < C945_READY:
            return "skipped:too-early (09:40 bar not final until 09:45:40)"

        # ---- fresh evening watchlist (previous completed session)
        wl = pd.DataFrame(conn.execute(
            "SELECT w.symbol, w.rank, w.prob, w.prior, w.prior20h, w.prior20l "
            "FROM watchlists w WHERE w.dkey = "
            "(SELECT max(dkey) FROM watchlists WHERE dkey < %s) ORDER BY w.rank",
            (T,)).fetchall(),
            columns=["symbol", "rank", "prob", "prior", "prior20h", "prior20l"])
        wl_dkey = conn.execute("SELECT max(dkey) FROM watchlists WHERE dkey < %s",
                               (T,)).fetchone()[0]
        last_session = conn.execute(
            "SELECT max(date) FROM eod_mkt WHERE date < %s", (T,)).fetchone()[0]
        version = conn.execute(
            "SELECT version FROM model_blob ORDER BY created_at DESC LIMIT 1"
        ).fetchone()[0]
        if not len(wl) or wl_dkey is None:
            raise RuntimeError("no watchlist before today — aborting (P3-04)")
        if str(last_session) != wl_dkey:
            raise RuntimeError(f"stale watchlist {wl_dkey} vs last session "
                               f"{last_session} — evening job did not complete (P3-04)")

        # ---- fetch live bars for the picks
        isins = dict(conn.execute(
            "SELECT symbol, isin FROM universe WHERE symbol = ANY(%s)",
            (list(wl["symbol"]),)).fetchall())
        excluded, picks = [], []
        for r in wl.itertuples():
            isin = isins.get(r.symbol)
            if not isin:
                excluded.append(r.symbol)
                continue
            try:
                bars = (fetch_fn or fetch_intraday_bars)(f"NSE_EQ|{isin}")
            except Exception as e:                       # noqa: BLE001
                ctx["alert"](f"morning fetch error {r.symbol}: {e}")
                excluded.append(r.symbol)
                continue
            m = morning_metrics(bars)
            if m["c945"] is None or m["or15_h"] is None:
                excluded.append(r.symbol)
                continue
            picks.append(dict(symbol=r.symbol, prob=r.prob, prior=r.prior,
                              prior20h=r.prior20h, prior20l=r.prior20l, **m))
        picks = pd.DataFrame(picks)

        # total data failure must NEVER look like a genuine 'stay flat'
        if not len(picks) and len(excluded) == len(wl):
            ctx["alert"](f"morning data unavailable for ALL {len(wl)} watchlist "
                         f"stocks — aborting. Data failure is NOT a flat signal (P3-06).")
            raise RuntimeError("all morning fetches failed — aborted, no signal sent")

        book = confirm_book(picks) if len(picks) else pd.DataFrame()

        # cap note (from the FULL candidate set, pre-cap — like select_book)
        capped = None
        if len(picks):
            n_up = int((picks["c945"] > picks["or15_h"]).sum())
            n_dn = int((picks["c945"] < picks["or15_l"]).sum())
            if n_up > THRESHOLD:
                capped = f"{n_up} broke OR15 up -> kept 2 calmest"
            elif n_dn > THRESHOLD:
                capped = f"{n_dn} broke OR15 down -> kept 2 calmest"

        msg = format_message(T, book, version, excluded, capped)
        ok = ctx["send"](msg) if ctx.get("send") else False
        if not ok:
            ctx["alert"](f"morning TELEGRAM DELIVERY FAILED for {T}")
            raise RuntimeError("morning telegram delivery failed")

        # ---- persist signals (idempotent: overwrite the day's rows)
        if len(book):
            with conn.transaction():
                conn.execute("DELETE FROM signals WHERE dkey = %s", (T,))
                for r in book.itertuples():
                    stop = (r.c945 * (1 - SL_PCT) if r.side == 1
                            else r.c945 * (1 + SL_PCT))
                    conn.execute(
                        "INSERT INTO signals (dkey, symbol, side, entry, stop, "
                        "or15_h, or15_l, c945, status) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'sent')",
                        (T, r.symbol, int(r.side), float(r.c945),
                         round(float(stop), 2), float(r.or15_h),
                         float(r.or15_l), float(r.c945)))
        return f"{'done' if len(book) else 'flat'}:{len(book)} signals, {len(excluded)} no-data"
    finally:
        conn.close()
