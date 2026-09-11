"""P4-07: internal scheduler decision logic (pure functions, no I/O)."""
import datetime as dt
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kcore import clock                                # noqa: E402
from kcore.scheduler import due_triggers, due_watchdogs  # noqa: E402

IST = clock.IST


def at(h, m, s=0, wd=None):
    t = dt.datetime(2026, 9, 11, h, m, s, tzinfo=IST)   # Friday
    if wd is not None:                                   # force weekday
        t += dt.timedelta(days=(wd - t.weekday()) % 7)
    return t


def test_triggers_fire_inside_windows():
    assert due_triggers(at(9, 45, 59)) == []
    assert due_triggers(at(9, 46, 5)) == ["morning"]
    assert due_triggers(at(9, 47, 30)) == ["morning"]          # grace
    assert due_triggers(at(9, 48, 10)) == ["morning"]          # retry slot
    assert due_triggers(at(9, 50, 30)) == []                   # past grace
    assert due_triggers(at(20, 2, 5)) == ["evening"]
    assert due_triggers(at(20, 33, 0)) == ["evening"]          # retry slot
    assert due_triggers(at(21, 0, 0)) == []


def test_weekend_nothing_fires():
    assert due_triggers(at(9, 46, 5, wd=5)) == []              # Saturday
    assert due_triggers(at(20, 2, 5, wd=5)) == []
    assert due_watchdogs(at(9, 52, 0, wd=6)) == []             # Sunday


def test_watchdog_windows():
    assert due_watchdogs(at(9, 51, 59)) == []
    assert due_watchdogs(at(9, 52, 10)) == ["morning"]
    assert due_watchdogs(at(9, 53, 30)) == []
    assert due_watchdogs(at(20, 40, 20)) == ["evening"]


def test_loop_survives_restart_mid_window():
    """The loop is stateless: any moment inside a window dispatches — a
    restart never loses the schedule (there are no timers to lose)."""
    for t in (at(9, 46, 1), at(9, 46, 31), at(9, 47, 59)):
        assert "morning" in due_triggers(t)
