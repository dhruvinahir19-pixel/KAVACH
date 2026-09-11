"""
app.py — the scanner web service.

Routes:
  GET  /health          -> "OK" (static; NEVER touches Neon — egress + keep-alive safe)
  POST /trigger/<job>   -> requires X-Trigger-Secret header; ACKS in <100ms and runs
                           the job in a background thread (ack-then-run, F-05)

P2/P3 register the real jobs (evening/morning/watchdog/universe-refresh) in JOBS.
P1 ships 'selftest' (writes a kv marker + sends a Telegram message) so the whole
chain can be exercised end-to-end before any market logic exists.
"""
import os
import threading

from flask import Flask, jsonify, request

from . import config, jobs, neon_store, telegram
from .clock import today_key

JOB_NAMES = ("selftest", "evening", "morning", "watchdog", "universe-refresh")


def _selftest(ctx):
    conn = ctx["connect"]()
    try:
        neon_store.kv_set(conn, "selftest_last_run", ctx["dkey"])
    finally:
        conn.close()
    ctx["alert"](f"selftest OK — dkey={ctx['dkey']}", severity="INFO")


def _evening(ctx):
    from evening import run_evening
    return run_evening(ctx)


def _morning(ctx):
    from morning import run_morning
    return run_morning(ctx)


JOBS = {"selftest": _selftest, "evening": _evening, "morning": _morning}


def watchdog_status(kind, state, detail):
    """Map a jobs_log row to (ok, verdict). Pure function (unit-tested).
    A 'too-early'/'not-posted-yet' skip that was never followed up by the real
    run is exactly what the watchdog must catch."""
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


def create_app(cfg=None, store=neon_store):
    cfg = cfg or config.load(strict=True)

    # boot self-check (F-02): DB reachable + schema present. Best effort — if Neon
    # is transiently down at deploy, jobs still fail loudly via claim/alert paths.
    import traceback
    try:
        conn = store.connect(cfg["NEON_DATABASE_URL"])
        if hasattr(store, "ensure"):
            store.ensure(conn)
        conn.close()
    except Exception:
        traceback.print_exc()

    app = Flask(__name__)

    @app.get("/health")
    def health():
        return "OK", 200

    @app.get("/version")
    def version():
        return jsonify(version="2026-09-11.1-chain"), 200

    @app.post("/trigger/<job>")
    def trigger(job):
        if request.headers.get("X-Trigger-Secret") != cfg["TRIGGER_SECRET"]:
            return jsonify(error="forbidden"), 403
        if job not in JOB_NAMES:
            return jsonify(error=f"unknown job: {job}"), 404
        fn = JOBS.get(job)
        if fn is None:
            return jsonify(error=f"job not yet implemented: {job}"), 501
        threading.Thread(
            target=jobs.run_job,
            args=(job, fn),
            kwargs={
                "store": store,
                "url": cfg["NEON_DATABASE_URL"],
                "tg": {"token": cfg["TELEGRAM_BOT_TOKEN"],
                       "chat_id": cfg["TELEGRAM_CHAT_ID"]},
            },
            daemon=True,
        ).start()
        return jsonify(status="accepted", job=job, dkey=today_key()), 202

    @app.post("/watchdog/<kind>")
    def watchdog(kind):
        """Called AFTER the expected window (09:52 / 20:20 IST) by cron-job.org
        and/or GitHub Actions. Verifies the job actually produced a signal
        today and screams on Telegram if not."""
        if request.headers.get("X-Trigger-Secret") != cfg["TRIGGER_SECRET"]:
            return jsonify(error="forbidden"), 403
        if kind not in ("morning", "evening"):
            return jsonify(error=f"unknown watchdog: {kind}"), 404
        conn = store.connect(cfg["NEON_DATABASE_URL"])
        try:
            row = neon_store.job_state(conn, kind, today_key())
            state, detail = (row[0], row[2]) if row else (None, None)
        finally:
            conn.close()
        ok, verdict = watchdog_status(kind, state, detail)
        if not ok:
            telegram.alert(
                f"WATCHDOG [{kind.upper()}] {verdict} — check the system now; "
                f"today's {'signal' if kind == 'morning' else 'watchlist'} "
                f"may be missing.", cfg["TELEGRAM_BOT_TOKEN"],
                cfg["TELEGRAM_CHAT_ID"], severity="ERROR")
        return jsonify(ok=ok, kind=kind, state=state, verdict=verdict), \
            (200 if ok else 503)

    return app


if __name__ == "__main__":        # local dev only; Render serves via gunicorn
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
