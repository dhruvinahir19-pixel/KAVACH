"""
jobs.py — idempotent job runner. THE execution spine.

Guarantees:
  - exactly-once per (job, dkey): concurrent/duplicate triggers yield one run
  - ack-then-run friendly: callers (app.py) launch this in a thread and return 202
    immediately; cron-job.org's 30s timeout can never kill the work
  - heartbeats every 10s: a killed run becomes reclaimable after stale_min
  - every failure terminates in state='failed' + alert attempt — never silence
  - fn receives a fresh-connection FACTORY (never a long-held connection), plus
    ctx['alert'] for operator messages
"""
import threading
import traceback

from . import clock, telegram

HB_EVERY_S = 10


def run_job(name, fn, *, store, url=None, tg=None,
            max_attempts=3, stale_min=15, hb_every=HB_EVERY_S):
    """Run fn(ctx) as job `name` for today's date key. Returns final status string.
    store: a module exposing connect/claim_job/heartbeat/finish_job/log_alert
    (neon_store, or the in-memory FakeStore used by tests). tg: {'token','chat_id'}
    or None (alerts then only land in alert_log)."""
    dkey = clock.today_key()
    conn = store.connect(url)
    if hasattr(store, "ensure"):
        try:
            store.ensure(conn)          # schema exists before first claim (idempotent)
        except Exception:
            traceback.print_exc()       # claim below will fail loudly if truly broken

    status = store.claim_job(conn, name, dkey,
                             max_attempts=max_attempts, stale_min=stale_min)
    if status != "claimed":
        conn.close()
        return status

    stop = threading.Event()

    def _hb():
        while not stop.wait(hb_every):
            try:
                store.heartbeat(conn, name, dkey)
            except Exception as e:            # heartbeat must never kill the job
                traceback.print_exc()

    hb = threading.Thread(target=_hb, daemon=True)
    hb.start()

    def alert(msg, severity="ALERT"):
        delivered = False
        if tg:
            delivered = telegram.alert(msg, tg["token"], tg["chat_id"],
                                       severity=severity)
        try:
            store.log_alert(conn, dkey, severity, msg, delivered)
        except Exception:
            traceback.print_exc()
        return delivered

    def send(msg):
        if tg:
            return telegram.send(msg, tg["token"], tg["chat_id"])
        return False

    ctx = {
        "job": name,
        "dkey": dkey,
        "url": url,
        "connect": lambda: store.connect(url),   # fresh short-lived connections
        "alert": alert,
        "send": send,
    }
    try:
        res = fn(ctx)
        if isinstance(res, str) and res.startswith("skipped"):
            store.finish_job(conn, name, dkey, "skipped", res)
            status = "skipped"
        else:
            store.finish_job(conn, name, dkey, "done", res if isinstance(res, str) else None)
            status = "done"
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"
        store.finish_job(conn, name, dkey, "failed", detail)
        alert(f"job {name} FAILED — {detail}", severity="ERROR")
        traceback.print_exc()
        status = "failed"
    finally:
        stop.set()
        hb.join(timeout=hb_every + 1)
        conn.close()
    return status


def watchdog_status(kind, state, detail):
    """Map a jobs_log row to (ok, verdict). Pure function (unit-tested).
    A 'too-early'/'not-posted-yet' skip that was never followed up by the
    real run is exactly what the watchdog must catch."""
    if state == "done":
        return True, f"ok: {kind} done ({detail})"
    if state == "failed":
        return False, f"FAILED: {kind} ({detail})"
    if state == "running":
        return True, f"ok: {kind} still running"
    if state is None:
        return False, f"MISSING: no {kind} job ran today at all"
    d = (detail or "")
    if "weekend" in d or "holiday" in d:
        return True, f"ok: {kind} skipped ({d})"
    return False, (f"STUCK-SKIPPED: {kind} last state '{d}' — the real run "
                   f"never happened")
