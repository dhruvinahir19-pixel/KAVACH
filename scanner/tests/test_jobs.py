"""Job-runner logic tests with an in-memory store (SQL semantics covered live)."""
import threading
import time

from kcore import jobs


class FakeConn:
    def close(self):
        pass


class FakeStore:
    def __init__(self):
        self.jobs = {}
        self.alerts = []
        self.heartbeats = 0

    def connect(self, url=None):
        return FakeConn()

    def claim_job(self, conn, job, dkey, max_attempts=3, stale_min=15):
        rec = self.jobs.setdefault((job, dkey), {"state": "pending", "attempts": 0})
        if rec["state"] in ("done", "skipped"):
            return "skip-done"
        if rec["state"] == "running":
            return "skip-running"
        if rec["attempts"] >= max_attempts:
            return "skip-max-attempts"
        rec["state"] = "running"
        rec["attempts"] += 1
        return "claimed"

    def heartbeat(self, conn, job, dkey):
        self.heartbeats += 1

    def finish_job(self, conn, job, dkey, state, detail=None):
        self.jobs[(job, dkey)]["state"] = state
        self.jobs[(job, dkey)]["detail"] = detail

    def log_alert(self, conn, dkey, severity, message, delivered):
        self.alerts.append((dkey, severity, message, delivered))


def test_success_lifecycle():
    fs = FakeStore()
    seen = {}

    def fn(ctx):
        seen["ctx"] = True
        ctx["alert"]("hello", severity="INFO")

    assert jobs.run_job("t1", fn, store=fs, hb_every=999) == "done"
    assert seen["ctx"] and fs.jobs[("t1", fs.alerts[0][0])]["state"] == "done"
    assert fs.alerts[0][2] == "hello"


def test_rerun_same_day_skips():
    fs = FakeStore()
    assert jobs.run_job("t2", lambda ctx: None, store=fs, hb_every=999) == "done"
    assert jobs.run_job("t2", lambda ctx: None, store=fs, hb_every=999) == "skip-done"


def test_failure_marks_failed_and_alerts():
    fs = FakeStore()

    def boom(ctx):
        raise ValueError("kaboom")

    assert jobs.run_job("t3", boom, store=fs, hb_every=999) == "failed"
    assert fs.jobs[("t3", fs.alerts[0][0])]["state"] == "failed"
    assert fs.alerts[0][1] == "ERROR"
    assert "kaboom" in fs.alerts[0][2]
    # failure allows a retry (attempts < max)
    assert jobs.run_job("t3", lambda ctx: None, store=fs, hb_every=999) == "done"


def test_running_job_blocks_second_trigger():
    fs = FakeStore()
    fs.claim_job(None, "t4", "2026-09-10")       # someone already claimed it
    assert jobs.run_job("t4", lambda ctx: None, store=fs, hb_every=999) == "skip-running"


def test_heartbeat_runs_during_long_job():
    fs = FakeStore()

    def slow(ctx):
        time.sleep(0.25)

    t0 = time.time()
    jobs.run_job("t5", slow, store=fs, hb_every=0.05)
    assert fs.heartbeats >= 2                    # beats happened while fn ran
    assert time.time() - t0 < 5                  # and it terminated promptly
