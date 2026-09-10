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


@pytest.fixture(autouse=True)
def _restore():
    import kcore.eod_cache as ec
    ec._cache_singleton = None        # stale process-global cache would mask per-test state
    yield
    ec._cache_singleton = None
    clock._now = lambda: dt.datetime.now(clock.IST)
    conn = neon_store.connect()
    conn.execute("DELETE FROM eod_daily WHERE date = %s", (FAKE_MON,))
    conn.execute("DELETE FROM eod_mkt WHERE date = %s", (FAKE_MON,))
    conn.execute("DELETE FROM watchlists WHERE dkey = %s", (FAKE_MON.isoformat(),))
    conn.execute("UPDATE universe SET last_seen = (SELECT max(date) FROM eod_daily) "
                 "WHERE last_seen > (SELECT max(date) FROM eod_daily)")
    conn.close()


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


def test_not_posted_yet_skips():
    _freeze(2026, 9, 10, 20, 5)
    sent, alerts = [], []
    res = evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)
    assert res == "skipped:not-posted-yet"
    assert not alerts


def test_not_posted_after_ladder_fails_loud():
    _freeze(2026, 9, 10, 22, 0)
    sent, alerts = [], []
    with pytest.raises(RuntimeError, match="bhavcopy missing"):
        evening.run_evening(_ctx(sent, alerts), harvest_fn=lambda d: None)
    assert any("MISSING" in a[1] for a in alerts)


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
    day = engine_harvest.harvest_day(dt.date(2026, 9, 9))
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
    real = engine_harvest.harvest_day(dt.date(2026, 9, 9))

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
    assert sent and "KAVACH-945 Evening Watchlist" in sent[0]

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
