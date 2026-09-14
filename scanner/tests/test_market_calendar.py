"""P3-11 unit tests: the independent market-open check (no network).
Parses only the DATE of the newest NIFTY bar — robust to Yahoo's end-labeled
bar convention. None = cannot determine -> callers must stay loud."""
import datetime as dt
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scanner"))

from kcore import market_calendar as mc          # noqa: E402


def _resp(stamps):
    return {"chart": {"result": [{"timestamp": stamps}]}}


def _ep(y, m, d, h=10, minute=0):
    return int(dt.datetime(y, m, d, h, minute, tzinfo=dt.timezone.utc).timestamp())


def test_newest_bar_today_means_traded():
    # 2026-09-14 04:15 UTC = 09:45 IST -> same IST date
    assert mc.market_traded_today("2026-09-14",
                                  json_resp=_resp([_ep(2026, 9, 14, 4, 0),
                                                   _ep(2026, 9, 14, 4, 15)])) is True


def test_newest_bar_older_means_closed():
    # newest bar Friday 2026-09-11, "today" Monday 2026-09-14 -> closed
    assert mc.market_traded_today("2026-09-14",
                                  json_resp=_resp([_ep(2026, 9, 10, 4),
                                                   _ep(2026, 9, 11, 10)])) is False


def test_ist_midnight_boundary():
    # 2026-09-13 18:30 UTC = 2026-09-14 00:00 IST -> counts as today
    assert mc.market_traded_today("2026-09-14",
                                  json_resp=_resp([_ep(2026, 9, 13, 18, 30)])) is True


def test_unreachable_returns_none(monkeypatch):
    def boom():
        raise requests.RequestException("network down")
    monkeypatch.setattr(mc, "_fetch", boom)
    assert mc.market_traded_today("2026-09-14") is None


def test_empty_payload_returns_none():
    assert mc.market_traded_today("2026-09-14",
                                  json_resp={"chart": {"result": [{}]}}) is None
    assert mc.market_traded_today("2026-09-14",
                                  json_resp={"chart": {"result": []}}) is None
