"""Universe-maintenance scenario tests (live Neon, fake symbols, self-cleaning)."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import neon_store          # noqa: E402
from evening import maintain_universe, SCOREABLE_MIN_SESSIONS  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"), reason="live NEON_DATABASE_URL not set")

FAKE = ["ZUUT1", "ZUUT2", "ZUUT3", "ZUUT4"]


@pytest.fixture(autouse=True)
def _clean():
    conn = neon_store.connect()
    for s in FAKE:
        conn.execute("DELETE FROM universe WHERE symbol = %s", (s,))
        conn.execute("DELETE FROM eod_daily WHERE symbol = %s", (s,))
    conn.close()
    yield
    conn = neon_store.connect()
    for s in FAKE:
        conn.execute("DELETE FROM universe WHERE symbol = %s", (s,))
        conn.execute("DELETE FROM eod_daily WHERE symbol = %s", (s,))
    conn.close()


def _seed(sym, active, scoreable, last_seen):
    conn = neon_store.connect()
    isin = "INEZUT0%06d" % (FAKE.index(sym) + 1)   # build in python: '%' in SQL breaks psycopg params
    conn.execute(
        "INSERT INTO universe (symbol, isin, active, scoreable, first_seen, last_seen) "
        "VALUES (%s,%s,%s,%s,'2026-01-01',%s) ON CONFLICT (symbol) "
        "DO UPDATE SET active=EXCLUDED.active, scoreable=EXCLUDED.scoreable, "
        "last_seen=EXCLUDED.last_seen", (sym, isin, active, scoreable, last_seen))
    conn.close()


def test_new_symbol_added_not_scoreable():
    notes = maintain_universe(neon_store.connect(), set(FAKE[:1]) | {"RELIANCE"},
                              "2026-09-09")
    conn = neon_store.connect()
    row = conn.execute("SELECT active, scoreable FROM universe WHERE symbol='ZUUT1'").fetchone()
    conn.close()
    assert row == (True, False), "new entrant must be active but NOT scoreable (F-16)"
    assert any("+ZUUT1 NEW" in n for n in notes)


def test_absent_counting_and_deactivation():
    # Anchor to the DB's real latest sessions — hardcoded 2026-09-xx dates broke
    # the moment 2026-09-10 landed in eod_daily (absence counts real sessions
    # after last_seen, whatever the DB currently holds).
    conn = neon_store.connect()
    recent = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM eod_daily WHERE date < '2100-01-01' "
        "ORDER BY date DESC LIMIT 6").fetchall()]
    conn.close()
    assert len(recent) == 6, "need >= 6 sessions of eod_daily history"
    latest, one_back, five_back = recent[0], recent[1], recent[5]

    _seed("ZUUT2", True, True, one_back.isoformat())  # absent today, 1 session ago
    conn = neon_store.connect()
    notes = maintain_universe(conn, set(), latest.isoformat())
    row = conn.execute("SELECT active FROM universe WHERE symbol='ZUUT2'").fetchone()
    conn.close()
    assert row == (True,) and any("ZUUT2 absent (1/5)" in n for n in notes)

    # now simulate 5+ sessions since last_seen: shift last_seen back
    conn = neon_store.connect()
    conn.execute("UPDATE universe SET last_seen=%s WHERE symbol='ZUUT2'",
                 (five_back.isoformat(),))
    notes = maintain_universe(conn, set(), latest.isoformat())
    row = conn.execute("SELECT active FROM universe WHERE symbol='ZUUT2'").fetchone()
    conn.close()
    assert row == (False,), "5+ missed sessions must deactivate (P2-05)"
    assert any("ZUUT2 deactivated" in n for n in notes)


def test_reactivation():
    _seed("ZUUT3", False, True, "2026-08-01")
    conn = neon_store.connect()
    notes = maintain_universe(conn, {"ZUUT3"}, "2026-09-09")
    row = conn.execute("SELECT active, last_seen FROM universe WHERE symbol='ZUUT3'").fetchone()
    conn.close()
    assert row[0] is True and str(row[1]) == "2026-09-09", \
        "returning symbol re-activates and stamps last_seen"
    assert any("ZUUT3 re-activated" in n for n in notes)


def test_scoreable_promotion():
    _seed("ZUUT4", True, False, "2026-09-09")
    conn = neon_store.connect()
    # give it exactly SCOREABLE_MIN_SESSIONS EOD rows
    for i in range(SCOREABLE_MIN_SESSIONS):
        conn.execute(
            "INSERT INTO eod_daily (date, symbol, close) VALUES (%s, 'ZUUT4', 100.0)",
            (f"2026-08-{i+1:02d}",))
    maintain_universe(conn, {"ZUUT4"}, "2026-09-09")
    row = conn.execute("SELECT scoreable FROM universe WHERE symbol='ZUUT4'").fetchone()
    conn.close()
    assert row == (True,), f"{SCOREABLE_MIN_SESSIONS}+ sessions must promote scoreability"
