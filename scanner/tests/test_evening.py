"""Evening-job behavior tests: gates, retry semantics, fail-loud paths.
Live Neon required. The full-path integration test relabels a REAL harvested
day to a far-future fake Monday (2099-01-05) and cleans up after itself."""
import copy
import datetime as dt
import os
import sys
from pathlib import Path

import pytest
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import clock, neon_store          # noqa: E402
from kcore import market_calendar            # noqa: E402
import evening                                # noqa: E402
import harvest as engine_harvest              # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"), reason="live NEON_DATABASE_URL not set")

FAKE_MON = dt.date(2099, 1, 5)          # far-future Monday; never real data


def _ctx(sent, alerts):
    return {"url": None,
            "connect": lambda: neon_store.connect(),
            "send": lambda msg: (sent.append(msg), True)[1],
            "alert": lambda msg, severity="ALERT": (alerts.append((severity, msg)), True)[1]}


def _freeze(y, m, d, hh=20, mm=5):
    clock._now = lambda: dt.datetime(y, m, d, hh, mm, tzinfo=clock.IST)


def _day_from_store(day_iso):
    """harvest_day()-shaped dict from the LOCAL research store — no live NSE
    download (2026-09-14: the sandbox IP got NSE-403-blocked after heavy
    verification use; tests must not depend on NSE archives being reachable,
    and shouldn't burn NSE bandwidth on every run anyway)."""
    import pandas as pd
    root = ROOT / "engine" / "data"

    def frame(name):
        df = pd.read_csv(root / f"{name}.csv.gz")
        return df[df["date"] == day_iso].copy()

    def rowdict(name):
        df = pd.read_csv(root / f"{name}.csv.gz")
        r = df[df["date"] == day_iso].iloc[0]
        return {k: (None if pd.isna(v) else v) for k, v in r.items() if k != "date"}

    return {"cash": frame("cash"), "fut": frame("fut"), "opt": frame("opt"),
            "part": {"date": day_iso, **rowdict("part")},
            "mkt": {"date": day_iso, **rowdict("mkt")}}


def _scrub_fake_day():
    """Remove ALL traces of the far-future fake day — BEFORE each test and
    again after. Teardown-only cleanup is not kill-safe: a run that dies
    mid-test (observed 2026-09-11: timeout-killed suite) leaves 2099-01-05
    rows in eod_daily, which slide the EodCache trailing window and silently
    drift parity. Universe repair is exact per symbol: last_seen = the
    symbol's true max real date in eod_daily (a healthy row already equals
    it, so only polluted rows are touched)."""
    conn = neon_store.connect()
    try:
        conn.execute("DELETE FROM eod_daily WHERE date = %s", (FAKE_MON,))
        conn.execute("DELETE FROM eod_mkt WHERE date = %s", (FAKE_MON,))
        conn.execute("DELETE FROM watchlists WHERE dkey = %s", (FAKE_MON.isoformat(),))
        conn.execute(
            "UPDATE universe u SET last_seen = s.mx "
            "FROM (SELECT symbol, max(date) AS mx FROM eod_daily GROUP BY symbol) s "
            "WHERE u.symbol = s.symbol AND u.last_seen > s.mx")
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _restore():
    import kcore.eod_cache as ec
    ec._cache_singleton = None        # stale process-global cache would mask per-test state
    _scrub_fake_day()                 # self-heal rows a killed earlier run may have left
    yield
    ec._cache_singleton = None
    clock._now = lambda: dt.datetime.now(clock.IST)
    _scrub_fake_day()


def test_weekend_skip():
    _freeze(2026, 9, 12)                       # Saturday
    sent, alerts = [], []
    assert evening.run_evening(_ctx(sent, alerts)) == "skipped:weekend"
    assert not sent


def test_holiday_skip():
    _freeze(2026, 1, 26)                       # Republic Day, Monday, in table
    sent, alerts = [], []
    assert evening.run_evening(_ctx(sent, alerts)) == "skipped:holiday"


def test_off_schedule_raises():
    _freeze(2026, 9, 10, 17, 18)               # before window (P2-10)
    with pytest.raises(RuntimeError, match="off-schedule"):
        evening.run_evening(_ctx([], []))


def test_not_posted_yet_skips(monkeypatch):
    _freeze(2026, 9, 10, 20, 5)
    # market DID trade -> normal not-posted-yet ladder (no holiday inference)
    monkeypatch.setattr(market_calendar, "market_traded_today",
                        lambda t=None, **k: True)
    sent, alerts = [], []
    res = evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)
    assert res == "skipped:not-posted-yet"
    assert not alerts


def test_not_posted_after_ladder_fails_loud(monkeypatch):
    _freeze(2026, 9, 10, 22, 0)
    monkeypatch.setattr(market_calendar, "market_traded_today",
                        lambda t=None, **k: True)
    sent, alerts = [], []
    with pytest.raises(RuntimeError, match="bhavcopy missing"):
        evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)
    assert any("MISSING" in a[1] for a in alerts)


def test_unlisted_holiday_clean_skip(monkeypatch):
    """P3-11 (2026-09-14 incident): holiday MISSING from the calendar ->
    bhavcopy absent AND the independent check says market closed -> ONE info
    note, clean skip, no error storm."""
    _freeze(2026, 9, 21, 20, 5)            # plain Monday, not in holidays
    monkeypatch.setattr(market_calendar, "market_traded_today",
                        lambda t=None, **k: False)
    sent, alerts = [], []
    res = evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)
    assert res == "skipped:market-closed (inferred, not in holiday calendar)"
    assert len(alerts) == 1 and "CLOSED" in alerts[0][1] and not sent


def test_unlisted_holiday_check_unreachable_still_loud(monkeypatch):
    """Independent check unreachable (None) -> the loud failure path stays."""
    _freeze(2026, 9, 21, 22, 0)
    monkeypatch.setattr(market_calendar, "market_traded_today",
                        lambda t=None, **k: None)
    sent, alerts = [], []
    with pytest.raises(RuntimeError, match="bhavcopy missing"):
        evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)


def test_fo_late_is_retry_not_failure():
    _freeze(2026, 9, 10, 20, 35)

    def fo_late(d):
        raise RuntimeError("cash OK but FO missing for 2026-09-10 — investigate")

    sent, alerts = [], []
    res = evening.run_evening(_ctx(sent, alerts), harvest_fn=fo_late)
    assert res.startswith("skipped:not-posted-yet")
    assert not alerts


def test_vix_missing_fails_loud_before_writes():
    _freeze(2099, 1, 5, 20, 5)
    day = _day_from_store("2026-09-09")          # local fixture, no NSE
    sent, alerts = [], []
    with pytest.raises(RuntimeError, match="VIX unavailable"):
        evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: day,
                            vix_fn=lambda a, b: _empty_df())
    conn = neon_store.connect()
    n = conn.execute("SELECT count(*) FROM eod_daily WHERE date=%s", (FAKE_MON,)).fetchone()[0]
    conn.close()
    assert n == 0, "VIX failure must not leave partial writes (D2 ordering)"


def _empty_df():
    import pandas as pd
    return pd.DataFrame(columns=["date", "vix"])


def test_full_path_on_fake_date():
    """Integration: real harvest relabeled to 2099-01-05 -> full pipeline ->
    watchlist rows + message; duplicate trigger overwrites identically."""
    _freeze(2099, 1, 5, 20, 5)
    real = _day_from_store("2026-09-09")         # local fixture, no NSE

    def relabeled(d):
        day = {"cash": real["cash"].copy(), "fut": real["fut"].copy(),
               "opt": real["opt"].copy(), "part": dict(real["part"]),
               "mkt": dict(real["mkt"])}
        for k in ("cash", "fut", "opt"):
            day[k]["date"] = FAKE_MON.isoformat()
        day["part"]["date"] = FAKE_MON.isoformat()
        day["mkt"]["date"] = FAKE_MON.isoformat()
        return day

    vdf = pd.DataFrame([{"date": FAKE_MON.isoformat(), "vix": 13.5}])

    sent, alerts = [], []
    res = evening.run_evening(_ctx(sent, alerts), harvest_fn=relabeled,
                              vix_fn=lambda a, b: vdf)
    assert res.startswith("done:"), f"full path failed: {res}"
    assert not alerts
    assert sent and "🌙" in sent[0] and "1️⃣ IDEA" in sent[0]

    conn = neon_store.connect()
    n = conn.execute("SELECT count(*) FROM eod_daily WHERE date=%s", (FAKE_MON,)).fetchone()[0]
    w = conn.execute("SELECT count(*) FROM watchlists WHERE dkey=%s",
                     (FAKE_MON.isoformat(),)).fetchone()[0]
    first = conn.execute("SELECT symbol, prob FROM watchlists WHERE dkey=%s ORDER BY rank",
                         (FAKE_MON.isoformat(),)).fetchall()
    conn.close()
    assert n >= 200 and w == 10

    # duplicate trigger: same result, no duplication
    sent2, alerts2 = [], []
    res2 = evening.run_evening(_ctx(sent2, alerts2), harvest_fn=relabeled,
                               vix_fn=lambda a, b: vdf)
    assert res2.startswith("done:")
    conn = neon_store.connect()
    second = conn.execute("SELECT symbol, prob FROM watchlists WHERE dkey=%s ORDER BY rank",
                          (FAKE_MON.isoformat(),)).fetchall()
    conn.close()
    assert first == second, "duplicate trigger changed the watchlist"
