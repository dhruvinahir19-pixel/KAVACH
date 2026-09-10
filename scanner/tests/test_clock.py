from datetime import datetime

import pytest

from kcore import clock


@pytest.fixture(autouse=True)
def _restore_now():
    yield
    clock._now = lambda: datetime.now(clock.IST)


def _freeze(y, m, d, hh=10, mm=0):
    dt = datetime(y, m, d, hh, mm, tzinfo=clock.IST)
    clock._now = lambda: dt


def test_today_key():
    _freeze(2026, 9, 10, 10, 30)
    assert clock.today_key() == "2026-09-10"


def test_minute_of_day_boundaries():
    _freeze(2026, 9, 10, 9, 45)
    assert clock.minute_of_day() == 585          # Session.ENTRY
    _freeze(2026, 9, 10, 15, 10)
    assert clock.minute_of_day() == 910          # Session.EXIT_LAST
    _freeze(2026, 9, 10, 9, 15)
    assert clock.minute_of_day() == 555          # Session.OPEN


def test_is_trading_day():
    _freeze(2026, 9, 10)                          # Thursday
    assert clock.is_trading_day() is True
    _freeze(2026, 9, 12)                          # Saturday
    assert clock.is_trading_day() is False
    _freeze(2026, 9, 13)                          # Sunday
    assert clock.is_trading_day() is False
    _freeze(2026, 9, 10)
    assert clock.is_trading_day(holidays=frozenset({"2026-09-10"})) is False


def test_market_hours():
    _freeze(2026, 9, 10, 10, 0)
    assert clock.is_market_hours() is True
    _freeze(2026, 9, 10, 8, 0)
    assert clock.is_market_hours() is False


def test_session_constants_match_engine_convention():
    assert clock.Session.OPEN == 555
    assert clock.Session.OR_END == 569
    assert clock.Session.ENTRY == 585
    assert clock.Session.EXIT_LAST == 910
