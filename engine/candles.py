"""
candles.py — 5-minute candle store via Upstox v3 public historical API.  FAST v2

API rules (verified empirically + official docs, Sep 2026):
  - No authentication required.
  - GET /v3/historical-candle/{NSE_EQ|ISIN}/minutes/5/{to}/{from}
  - Range limit: intervals 1-15 min -> MAX 1 MONTH per request
                 intervals  >15 min -> max 1 quarter per request
  - Rate limits (historical = "Other Standard APIs"):
      50/sec, 500/min, 2000 per 30 min.
  - Our governor enforces safe margins: 5/sec, 280/min, 1800/30min,
    6 parallel workers. On HTTP 429: global 700s pause (outlasts block).

Storage (128MB budget): data/candles/{SYMBOL}.csv.gz
    epoch_min, open_paise, high_paise, low_paise, close_paise, volume
    (paise ints + epoch minutes; zero-volume filler bars dropped)
Resume-safe: a symbol's file is written only after ALL its months succeed.

Usage:
    python3 candles.py backfill              # 2026-01-01 .. last completed day
    python3 candles.py daily                 # refresh current month (after close)
    python3 candles.py status                # coverage report
"""
import os, sys, gzip, json, time, threading, collections
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd
from config import DATA

CANDLE_DIR = os.path.join(DATA, "candles")
os.makedirs(CANDLE_DIR, exist_ok=True)
ISIN_MAP = os.path.join(DATA, "isin_map.csv.gz")
MISSING_LOG = os.path.join(CANDLE_DIR, "_missing.json")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Accept": "application/json"}
BASE = "https://api.upstox.com/v3/historical-candle"
WORK_FROM = "2026-01-01"          # 2026-only rule
WORKERS = 6


# ---------------------------------------------------------------- governor
class RateGovernor:
    """Thread-safe pacing: <=5 req/s, <=280/min, <=1800/30min + 429 pause."""

    def __init__(self, per_sec=5, per_min=280, per_30min=1800):
        self.gap = 1.0 / per_sec
        self.per_min, self.per_30min = per_min, per_30min
        self.ts = collections.deque()
        self.lock = threading.Lock()
        self.pause_until = 0.0

    def pause(self, seconds=700):
        with self.lock:
            self.pause_until = max(self.pause_until, time.time() + seconds)

    def wait(self):
        while True:
            if time.time() < self.pause_until:
                time.sleep(min(5, self.pause_until - time.time() + 0.1))
                continue
            with self.lock:
                now = time.time()
                while self.ts and self.ts[0] < now - 1800:
                    self.ts.popleft()
                last = self.ts[-1] if self.ts else 0
                n_min = sum(1 for t in self.ts if t > now - 60)
                if (now - last >= self.gap and n_min < self.per_min
                        and len(self.ts) < self.per_30min):
                    self.ts.append(now)
                    return
            time.sleep(0.05)


GOV = RateGovernor()


# ---------------------------------------------------------------- fetch
def fetch_month(key, to_iso, from_iso):
    """One API call, governor-paced, with 429 backoff. rows | None."""
    url = f"{BASE}/{key}/minutes/5/{to_iso}/{from_iso}"
    for attempt in range(4):
        GOV.wait()
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r.json().get("data", {}).get("candles", [])
            if r.status_code == 429:
                print(f"    ! 429 -> global pause 700s", flush=True)
                GOV.pause(700)
                continue
            if r.status_code in (500, 502, 503, 504):
                time.sleep(20 * (attempt + 1))
                continue
            return None                      # other 4xx: permanent for this call
        except requests.RequestException:
            time.sleep(15)
    return None


def to_paise(x):
    return int(round(float(x) * 100))


def store_symbol(sym, key, windows):
    """Fetch all windows for one symbol; write file only if all succeed."""
    path = os.path.join(CANDLE_DIR, f"{sym}.csv.gz")
    rows, missing = [], []
    for to_iso, from_iso, ym in windows:
        candles = fetch_month(key, to_iso, from_iso)
        if candles is None:
            missing.append(ym)
            continue
        for c in candles:
            if len(c) < 6:
                continue
            try:
                vol = int(c[5])
                if vol <= 0:                 # filler bar — no trades
                    continue
                t = dt.datetime.fromisoformat(c[0])
                rows.append((int(t.timestamp() // 60), to_paise(c[1]),
                             to_paise(c[2]), to_paise(c[3]), to_paise(c[4]), vol))
            except (ValueError, TypeError):
                continue
    if missing and not rows:
        return False, missing
    rows.sort()
    seen, out = set(), []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0]); out.append(r)
    cols = ["epoch_min", "open_paise", "high_paise", "low_paise",
            "close_paise", "volume"]
    new = pd.DataFrame(out, columns=cols)
    if os.path.exists(path):                 # merge (works for BOTH directions:
        old = pd.read_csv(path)              #  forward refresh and backward
        new = (pd.concat([old, new]).drop_duplicates("epoch_min")  # extension)
               .sort_values("epoch_min"))
    new.to_csv(path, index=False, compression="gzip")
    if missing:
        ml = json.load(open(MISSING_LOG)) if os.path.exists(MISSING_LOG) else {}
        ml[sym] = missing
        json.dump(ml, open(MISSING_LOG, "w"))
    return True, missing


def month_windows(start_iso, end_iso):
    d = dt.date.fromisoformat(start_iso)
    end = dt.date.fromisoformat(end_iso)
    while d <= end:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        to = min(nxt - dt.timedelta(days=1), end)
        yield to.isoformat(), d.isoformat(), f"{d.year}-{d.month:02d}"
        d = nxt


def load_keys():
    m = pd.read_csv(ISIN_MAP)
    return dict(zip(m["symbol"], m["isin"]))


# ---------------------------------------------------------------- modes
def backfill(end_iso=None):
    keys = load_keys()
    symbols = sorted(keys)
    end = end_iso or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    windows = list(month_windows(WORK_FROM, end))
    done = {f[:-7] for f in os.listdir(CANDLE_DIR) if f.endswith(".csv.gz")}
    todo = [s for s in symbols if s not in done]
    print(f"[candles] {len(symbols)} symbols x {len(windows)} months | "
          f"{len(done)} done | {len(todo)} to fetch | "
          f"~{len(todo)*len(windows)} requests", flush=True)
    t0, ok, fail = time.time(), 0, []

    def work(sym):
        key = f"NSE_EQ%7C{keys[sym]}"
        return sym, *store_symbol(sym, key, windows)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, s): s for s in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            sym, success, missing = fut.result()
            ok += success
            if not success:
                fail.append(sym)
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                rate = (i * len(windows)) / el * 60 if el else 0
                print(f"[candles] {i}/{len(todo)} | ok={ok} fail={len(fail)} | "
                      f"{el/60:.1f} min | {rate:.0f} req/min", flush=True)
    if fail:
        print(f"[candles] FAILED (rerun to retry): {fail}", flush=True)
    print(f"[candles] DONE: +{ok} files, {len(fail)} failed, "
          f"{(time.time()-t0)/60:.1f} min", flush=True)


def daily():
    keys = load_keys()
    today = dt.date.today()
    ms = today.replace(day=1)
    windows = [(today.isoformat(), ms.isoformat(),
                f"{today.year}-{today.month:02d}")]
    def work(sym):
        key = f"NSE_EQ%7C{keys[sym]}"
        return sym, *store_symbol(sym, key, windows)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, s): s for s in sorted(keys)}
        for i, fut in enumerate(as_completed(futs), 1):
            fut.result()
            if i % 50 == 0:
                print(f"[candles-daily] {i}/{len(keys)}", flush=True)
    print("[candles-daily] DONE", flush=True)


def window(from_iso, to_iso):
    """Fetch an arbitrary date window for ALL symbols (merge+dedupe makes
    it safe for already-covered ranges). Used for backward extension."""
    keys = load_keys()
    windows = list(month_windows(from_iso, to_iso))
    print(f"[candles-window] {len(keys)} symbols x {len(windows)} months "
          f"({from_iso} -> {to_iso}) = ~{len(keys)*len(windows)} requests",
          flush=True)
    t0 = time.time()

    def work(sym):
        key = f"NSE_EQ%7C{keys[sym]}"
        return sym, *store_symbol(sym, key, windows)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(work, s): s for s in sorted(keys)}
        for i, fut in enumerate(as_completed(futs), 1):
            fut.result()
            if i % 40 == 0 or i == len(futs):
                print(f"[candles-window] {i}/{len(keys)} | "
                      f"{(time.time()-t0)/60:.1f} min", flush=True)
    print(f"[candles-window] DONE {(time.time()-t0)/60:.1f} min", flush=True)


def status():
    fs = [f for f in os.listdir(CANDLE_DIR) if f.endswith(".csv.gz")
          and not f.startswith("_")]
    total = sum(os.path.getsize(os.path.join(CANDLE_DIR, f)) for f in fs)
    print(f"[candles] {len(fs)} files | {total/1e6:.1f} MB")
    if os.path.exists(MISSING_LOG):
        print("missing months:", json.load(open(MISSING_LOG)))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode == "backfill":
        backfill(end_iso=sys.argv[2] if len(sys.argv) > 2 else None)
    elif mode == "daily":
        daily()
    elif mode == "window":
        window(sys.argv[2], sys.argv[3])
    else:
        status()
