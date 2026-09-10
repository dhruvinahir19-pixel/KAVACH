"""P3 morning-engine tests. Offline math tests (no network) + live-Neon job
tests using the clock seam. The offline parity vs the research morning table
and protocol_v2.select_book lives in the P3 verification gate run
(1,710 symbol-days exact; 474/474 trades identical — see FAILURE_MODES P3)."""
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import clock, neon_store          # noqa: E402
from morning import (                        # noqa: E402
    morning_metrics, confirm_book, format_message, OR_LO, CN_LO)

LIVE = pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"),
                          reason="live NEON_DATABASE_URL not set")


# ---------------------------------------------------------------- bar math
def _bar(mod, o, h, l, c, v=100):
    return (mod, o, h, l, c, v)


def test_metrics_or_and_c945():
    bars = [_bar(555, 10, 11, 9.5, 10.5), _bar(560, 10.5, 12, 10, 11),
            _bar(565, 11, 11.5, 10.5, 11), _bar(570, 11, 13, 11, 12.5),
            _bar(575, 12.5, 14, 12, 13), _bar(580, 13, 15, 13, 14.5),
            _bar(585, 99, 99, 99, 99)]              # forming bar: must be ignored
    m = morning_metrics(bars)
    assert m["or15_h"] == 12 and m["or15_l"] == 9.5
    assert m["c945"] == 14.5                        # close of the 09:40 bar


def test_metrics_empty_windows():
    assert morning_metrics([]) == {"or15_h": None, "or15_l": None, "c945": None}
    # zero-volume confirm bars -> c945 falls back to last bar WITH volume
    bars = [_bar(555, 10, 11, 9.5, 10.5), _bar(570, 10.5, 11, 10, 11),
            _bar(580, 0, 0, 0, 0, v=0)]
    m = morning_metrics(bars)
    assert m["c945"] == 11


# ---------------------------------------------------------------- book/cap
def _picks(rows):
    return pd.DataFrame([dict(symbol=s, prob=0.5, prior=0, prior20h=h,
                              prior20l=l, or15_h=oh, or15_l=ol, c945=c)
                         for s, h, l, oh, ol, c in rows])


def test_confirm_and_cap():
    # 4 LONG candidates (c945 > or15_h) -> cap keeps 2 calmest (lowest range20)
    rows = [("A", 110, 100, 10, 9, 10.5), ("B", 120, 100, 10, 9, 10.4),
            ("C", 101, 100, 10, 9, 10.2), ("D", 130, 100, 10, 9, 10.1)]
    # range20 = (h-l)/c945: C=0.098 A=0.952 B=1.92 D=2.97 -> keep C and A
    book = confirm_book(_picks(rows))
    assert sorted(book["symbol"]) == ["A", "C"]
    # 3 or fewer -> trade them all
    rows3 = rows[:3]
    book3 = confirm_book(_picks(rows3))
    assert len(book3) == 3
    # short side: c945 < or15_l
    shorts = _picks([("S", 110, 100, 12, 11, 10.9), ("T", 105, 100, 12, 11, 10.8)])
    book_s = confirm_book(shorts)
    assert sorted(book_s["symbol"]) == ["S", "T"] and set(book_s["side"]) == {-1}


def test_no_confirmation_flat():
    inside = _picks([("X", 110, 100, 11, 10, 10.5)])       # inside OR -> nothing
    book = confirm_book(inside)
    assert not len(book)
    msg = format_message("2026-09-11", book, "v1", [], None)
    assert "NO CONFIRMATION" in msg and "stay flat" in msg


def test_message_has_entry_and_stop():
    book = confirm_book(_picks([("A", 110, 100, 10, 9, 10.5)]))
    msg = format_message("2026-09-11", book, "v1", ["ZZZ"], "4 broke OR15 up -> kept 2 calmest")
    assert "LONG  A @ 10.50" in msg and f"SL {10.5 * 0.99:.2f}" in msg
    assert "no-data: ZZZ" in msg and "cap" in msg


# ---------------------------------------------------------------- job gates
def _ctx(sent, alerts):
    return {"connect": lambda: neon_store.connect(),
            "send": lambda m: (sent.append(m), True)[1],
            "alert": lambda m, severity="ALERT": (alerts.append(m), True)[1]}


def _freeze(y, m, d, hh, mm, ss=0):
    clock._now = lambda: dt.datetime(y, m, d, hh, mm, ss, tzinfo=clock.IST)


@LIVE
class TestJobGates:
    def teardown_method(self):
        clock._now = lambda: dt.datetime.now(clock.IST)

    def test_weekend_skip(self):
        _freeze(2026, 9, 12, 9, 46)
        assert __import__("morning").run_morning(_ctx([], [])) == "skipped:weekend"

    def test_holiday_skip(self):
        _freeze(2026, 1, 26, 9, 46)                      # Republic Day
        assert __import__("morning").run_morning(_ctx([], [])) == "skipped:holiday"

    def test_off_schedule_raises(self):
        _freeze(2026, 9, 11, 11, 30)
        with pytest.raises(RuntimeError, match="off-schedule"):
            __import__("morning").run_morning(_ctx([], []))

    def test_too_early_skips(self):
        _freeze(2026, 9, 11, 9, 41)
        assert __import__("morning").run_morning(_ctx([], [])) == "skipped:too-early (09:40 bar not final until 09:45:40)"

    def test_full_path_on_fake_now(self):
        """Freeze to a real trading window on a day whose watchlist exists
        (uses the 2026-09-09 watchlist as 'yesterday' for 2026-09-10) with
        injected bars -> signals written + message sent; cleanup after."""
        from morning import run_morning
        bars = [_bar(555, 10, 11, 9.5, 10.5), _bar(560, 10.5, 11.2, 10, 11),
                _bar(570, 11, 13, 11, 12.5), _bar(575, 12.5, 14, 12, 13),
                _bar(580, 13, 15, 13, 14.5)]
        sent, alerts = [], []
        # make 2026-09-10 look like a valid morning: watchlist 09-09 exists,
        # last eod_mkt session before 09-10 is 09-09 -> fresh
        _freeze(2026, 9, 10, 9, 46)
        res = run_morning(_ctx(sent, alerts), fetch_fn=lambda k: bars)
        assert res.startswith(("done:", "flat:"))
        assert sent and "KAVACH-945 Morning — 2026-09-10" in sent[0]
        if "done" in res:
            conn = neon_store.connect()
            rows = conn.execute("SELECT count(*) FROM signals WHERE dkey='2026-09-10'").fetchone()[0]
            conn.execute("DELETE FROM signals WHERE dkey='2026-09-10'")
            conn.close()
            assert rows > 0
        # idempotent re-run
        sent2, _ = [], []
        res2 = run_morning(_ctx(sent2, []), fetch_fn=lambda k: bars)
        assert res2 == res and sent2[0] == sent[0]
        conn = neon_store.connect()                      # P2-09: tests clean up
        conn.execute("DELETE FROM signals WHERE dkey='2026-09-10'")
        conn.close()


# ------------------------------------------------------- token + staleness
def test_stale_data_rejected():
    """Previous-session cache must raise, never feed the math (P3-05)."""
    from morning import StaleDataError, _parse_intraday
    yesterday = (dt.date(2026, 9, 10)).isoformat()
    payload = {"data": {"candles": [
        [f"{yesterday}T09:15:00+05:30", 10, 11, 9.5, 10.5, 100],
        [f"{yesterday}T09:40:00+05:30", 13, 15, 13, 14.5, 100]]}}
    with pytest.raises(StaleDataError):
        _parse_intraday(payload, "2026-09-11")


def test_parse_today_ok_and_zero_volume_dropped():
    from morning import _parse_intraday
    today = "2026-09-11"
    payload = {"data": {"candles": [
        [f"{today}T09:15:00+05:30", 10, 11, 9.5, 10.5, 100],
        [f"{today}T09:20:00+05:30", 0, 0, 0, 0, 0],
        [f"{today}T09:40:00+05:30", 13, 15, 13, 14.5, 100]]}}
    bars = _parse_intraday(payload, today)
    assert bars == [(555, 10.0, 11.0, 9.5, 10.5, 100), (580, 13.0, 15.0, 13.0, 14.5, 100)]


def test_auth_fallback_used_when_public_fails():
    from morning import fetch_intraday_bars
    calls = []

    def fake_http(key, token=None):
        calls.append(token)
        if token is None:
            raise RuntimeError("401 unauthorized")       # public fails
        return [(555, 10, 11, 9.5, 10.5, 100)]           # auth works

    import morning as M
    orig = M._intraday_http
    M._intraday_http = fake_http
    try:
        bars = fetch_intraday_bars("NSE_EQ|INE669E01016", token="TESTTOKEN")
        assert bars and calls == [None, "TESTTOKEN"]     # tried public, then auth
    finally:
        M._intraday_http = orig


@LIVE
class TestDataFailureAbort:
    def teardown_method(self):
        clock._now = lambda: dt.datetime.now(clock.IST)

    def test_total_failure_aborts_not_flat(self):
        """ALL fetches down -> loud abort; 'stay flat' must never be sent."""
        from morning import run_morning
        conn = neon_store.connect()                      # order-independent
        conn.execute("DELETE FROM signals WHERE dkey='2026-09-10'")
        conn.close()
        _freeze(2026, 9, 10, 9, 46)                     # watchlist 09-09 exists
        sent, alerts = [], []

        def dead_fetch(key):
            raise RuntimeError("public (401) | auth (no token)")

        with pytest.raises(RuntimeError, match="all morning fetches failed"):
            run_morning(_ctx(sent, alerts), fetch_fn=dead_fetch)
        assert alerts and not sent                      # alerted, NOTHING sent
        conn = neon_store.connect()
        n = conn.execute("SELECT count(*) FROM signals WHERE dkey='2026-09-10'").fetchone()[0]
        conn.close()
        assert n == 0

    def test_partial_failure_proceeds_with_note(self):
        """Some symbols down -> signal still sent, missing names listed."""
        from morning import run_morning
        _freeze(2026, 9, 10, 9, 46)
        sent, alerts = [], []
        bars = [_bar(555, 10, 11, 9.5, 10.5), _bar(560, 10.5, 11.2, 10, 11),
                _bar(570, 11, 13, 11, 12.5), _bar(575, 12.5, 14, 12, 13),
                _bar(580, 13, 15, 13, 14.5)]
        calls = []

        def flaky(key):
            calls.append(key)
            if len(calls) == 1:
                return bars                             # first pick has data
            raise RuntimeError("down")                  # the rest fail

        res = run_morning(_ctx(sent, alerts), fetch_fn=flaky)
        assert res.startswith("done:1")
        assert "no-data:" in sent[0]
        conn = neon_store.connect()
        conn.execute("DELETE FROM signals WHERE dkey='2026-09-10'")
        conn.close()
