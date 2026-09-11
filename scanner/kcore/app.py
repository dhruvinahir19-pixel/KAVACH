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
from .jobs import watchdog_status
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

    @app.get("/schedstatus")
    def schedstatus():
        from . import scheduler as _s
        st = _s.SchedState
        if st.started_at is None:
            return jsonify(scheduler="not-started", hint="KAVACH_SCHEDULER != 1"), 200
        return jsonify(scheduler="running", started=st.started_at,
                       last_tick=st.last_tick, ticks=st.ticks,
                       last_dispatch=st.last_dispatch), 200

    @app.get("/version")
    def version():
        return jsonify(version="2026-09-11.5-strict2"), 200

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

    # P4-07: in-process scheduler (KAVACH_SCHEDULER=1 in the Docker image).
    # Fires the daily schedule itself; external triggers stay as backups.
    if os.environ.get("KAVACH_SCHEDULER") == "1":
        from . import scheduler

        def _dispatch(name):
            fn = JOBS.get(name)
            if fn is None:
                return
            threading.Thread(
                target=jobs.run_job, args=(name, fn),
                kwargs={"store": store, "url": cfg["NEON_DATABASE_URL"],
                        "tg": {"token": cfg["TELEGRAM_BOT_TOKEN"],
                               "chat_id": cfg["TELEGRAM_CHAT_ID"]}},
                daemon=True).start()

        scheduler.start(cfg, _dispatch)
        app.logger.info("kavach scheduler started (internal, IST)")

    return app


if __name__ == "__main__":        # local dev only; Render serves via gunicorn
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
