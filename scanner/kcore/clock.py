"""
clock.py — THE only time source in the scanner. Nothing else may call datetime.now().

IST (UTC+5:30), no DST. All schedule decisions derive from here so clock drift is a
one-file problem, and tests can freeze time by overriding `_now`.
"""
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# ---- the single override point (tests replace this) --------------------------
_now = lambda: datetime.now(IST)


def now() -> datetime:
    """Timezone-aware current IST datetime."""
    return _now()


def today_key(d: date | None = None) -> str:
    """YYYY-MM-DD key for the IST 'business day' (used in every idempotency lock)."""
    return (d or now().date()).isoformat()


def minute_of_day(dt: datetime | None = None) -> int:
    dt = dt or now()
    return dt.hour * 60 + dt.minute


# ---- NSE cash session (bar-start minute-of-day, matches engine convention) ----
class Session:
    OPEN = 555          # 09:15
    OR_END = 569        # 09:30 (OR15 window = 555..569)
    ENTRY = 585         # 09:45 (confirm window = 570..584, entry at its close)
    EXIT_LAST = 910     # 15:10 (last bar START <= this; exit at its close)
    CLOSE = 930         # 15:30


def is_trading_day(d: date | None = None, holidays: frozenset[str] = frozenset()) -> bool:
    """Weekday and not in the holiday set. The authoritative holiday table lives in
    Neon (P2); callers pass it in so this function stays pure and testable."""
    d = d or now().date()
    return d.weekday() < 5 and d.isoformat() not in holidays


def is_market_hours(dt: datetime | None = None) -> bool:
    m = minute_of_day(dt)
    return Session.OPEN <= m <= Session.CLOSE
