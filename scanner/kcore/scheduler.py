"""
scheduler.py — in-process IST scheduler (P4-07).

WHY: a UptimeRobot-kept-alive service never sleeps, so the app can fire its
own schedule — no external cron required (user's call, 2026-09-11, after
cron-job.org failed to deliver the first live trigger).

Design rules:
- NO in-memory timers. Every 30s tick computes what is due from the CURRENT
  time; Neon jobs_log is the only arbiter of "already ran today" (idempotent
  claims). A restart (deploy, worker recycle, crash) just resumes next tick.
- A sleeping free-tier service cannot wake itself: UptimeRobot keep-alive is
  the lifeline; external /trigger calls (cron-job.org, GitHub Actions) remain
  welcome as redundant wake-up paths — double-fires are deduped by claims.
- Trigger grace windows are short (2 min): each tick re-dispatches inside the
  window; the claim system makes that free. Watchdogs alert once per day
  (kv-table guard) and only when something is actually wrong.
"""
import datetime as dt
import threading
import time
import traceback

from . import clock, neon_store, telegram
from .jobs import watchdog_status

TRIGGER_SCHEDULES = [
    ("morning", dt.time(9, 46)),      # signal run (job self-guards 09:45:40+)
    ("morning", dt.time(9, 48)),      # retry if the first attempt failed
    ("evening", dt.time(20, 2)),      # nightly watchlist
    ("evening", dt.time(20, 32)),     # retry if bhavcopy was late
]
TRIGGER_GRACE = dt.timedelta(minutes=2)

WATCHDOG_SCHEDULES = [
    ("morning", dt.time(9, 52)),      # did the signal actually go out?
    ("evening", dt.time(20, 40)),     # did the watchlist actually go out?
]
WATCHDOG_GRACE = dt.timedelta(minutes=1)

TICK_S = 30


def due_triggers(now: dt.datetime):
    """Pure: job names that should be dispatched at `now` (IST-aware)."""
    if now.weekday() >= 5:                       # Saturday/Sunday: nothing
        return []
    out = []
    for name, when in TRIGGER_SCHEDULES:
        start = dt.datetime.combine(now.date(), when, tzinfo=now.tzinfo)
        if start <= now <= start + TRIGGER_GRACE:
            out.append(name)
    return out


def due_watchdogs(now: dt.datetime):
    """Pure: watchdog kinds that should be checked at `now`."""
    if now.weekday() >= 5:
        return []
    out = []
    for kind, when in WATCHDOG_SCHEDULES:
        start = dt.datetime.combine(now.date(), when, tzinfo=now.tzinfo)
        if start <= now <= start + WATCHDOG_GRACE:
            out.append(kind)
    return out


def run_watchdog(cfg, kind):
    """Check today's job outcome; alert once per day if something is wrong."""
    url = cfg["NEON_DATABASE_URL"]
    conn = neon_store.connect(url)
    try:
        row = neon_store.job_state(conn, kind, clock.today_key())
        state, detail = (row[0], row[2]) if row else (None, None)
        guard = f"wd_alerted:{kind}:{clock.today_key()}"
        if not watchdog_status(kind, state, detail)[0]:
            if not neon_store.kv_get(conn, guard):
                neon_store.kv_set(conn, guard, "1")
                telegram.alert(
                    f"WATCHDOG [{kind.upper()}] "
                    f"{watchdog_status(kind, state, detail)[1]} — check the "
                    f"system; today's "
                    f"{'signal' if kind == 'morning' else 'watchlist'} "
                    f"may be missing.",
                    cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"],
                    severity="ERROR")
    finally:
        conn.close()


def start(cfg, dispatch):
    """Start the loop thread. dispatch(job_name) must be NON-BLOCKING
    (spawn a thread per job); the loop itself must never be slowed."""
    def loop():
        while True:
            try:
                now = clock.now()
                for name in due_triggers(now):
                    dispatch(name)
                for kind in due_watchdogs(now):
                    run_watchdog(cfg, kind)
            except Exception:                     # the loop must never die
                traceback.print_exc()
            time.sleep(TICK_S)

    t = threading.Thread(target=loop, daemon=True, name="kavach-scheduler")
    t.start()
    return t
