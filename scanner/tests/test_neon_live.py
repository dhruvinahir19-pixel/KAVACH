"""LIVE Neon tests — run with NEON_DATABASE_URL set (the real pooler endpoint).
Skipped automatically when the env var is absent (e.g., CI without secrets)."""
import os
import threading
import time

import pytest

from kcore import jobs, neon_store

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"), reason="live NEON_DATABASE_URL not set")

JOB = "p1test_job"
DK = "2099-01-01"          # far-future key: never collides with real days


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        conn = neon_store.connect()
        neon_store.init_schema(conn)
        conn.execute("DELETE FROM jobs_log WHERE job LIKE 'p1test%'")
        conn.execute("DELETE FROM kv WHERE k LIKE 'p1test%'")
        conn.execute("DELETE FROM alert_log WHERE dkey = %s", (DK,))
        conn.close()
    wipe()
    yield
    wipe()


def test_connect_and_schema_idempotent():
    conn = neon_store.connect()
    neon_store.init_schema(conn)
    neon_store.init_schema(conn)                 # second run must be a no-op
    assert conn.execute("SELECT 1").fetchone() == (1,)
    conn.close()


def test_kv_roundtrip_and_overwrite():
    conn = neon_store.connect()
    neon_store.kv_set(conn, "p1test_k", "v1")
    neon_store.kv_set(conn, "p1test_k", "v2")
    assert neon_store.kv_get(conn, "p1test_k") == "v2"
    assert neon_store.kv_get(conn, "p1test_missing", "dft") == "dft"
    conn.close()


def test_claim_is_idempotent():
    conn = neon_store.connect()
    assert neon_store.claim_job(conn, JOB, DK) == "claimed"
    assert neon_store.claim_job(conn, JOB, DK) == "skip-running"   # fresh heartbeat
    conn.close()


def test_concurrent_claims_single_winner():
    results = []
    barrier = threading.Barrier(6)

    def worker():
        conn = neon_store.connect()
        barrier.wait(timeout=10)
        results.append(neon_store.claim_job(conn, JOB, DK))
        conn.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert results.count("claimed") == 1
    assert all(r in ("skip-running", "claimed") for r in results)


def test_done_blocks_and_failed_retries_to_max():
    conn = neon_store.connect()
    neon_store.claim_job(conn, JOB, DK)
    neon_store.finish_job(conn, JOB, DK, "done")
    assert neon_store.claim_job(conn, JOB, DK) == "skip-done"

    conn.execute("UPDATE jobs_log SET state='failed', attempts=0 WHERE job=%s AND dkey=%s", (JOB, DK))
    for i in range(3):                           # max_attempts=3
        assert neon_store.claim_job(conn, JOB, DK) == "claimed", f"retry {i}"
        neon_store.finish_job(conn, JOB, DK, "failed")
    assert neon_store.claim_job(conn, JOB, DK) == "skip-max-attempts"
    conn.close()


def test_stale_running_is_reclaimable():
    conn = neon_store.connect()
    neon_store.claim_job(conn, JOB, DK)
    conn.execute(
        "UPDATE jobs_log SET heartbeat = now() - interval '30 minutes' "
        "WHERE job=%s AND dkey=%s", (JOB, DK))
    assert neon_store.claim_job(conn, JOB, DK) == "claimed"       # reclaimed
    conn.close()


def test_transaction_atomicity():
    conn = neon_store.connect()
    conn.execute("CREATE TABLE IF NOT EXISTS p1test_tx (id INT)")
    conn.execute("DELETE FROM p1test_tx")
    try:
        with conn.transaction():
            conn.execute("INSERT INTO p1test_tx VALUES (1)")
            conn.execute("INSERT INTO p1test_tx VALUES (2)")
            raise RuntimeError("crash mid-transaction")
    except RuntimeError:
        pass
    n = conn.execute("SELECT count(*) FROM p1test_tx").fetchone()[0]
    assert n == 0, "partial state after mid-transaction crash"
    with conn.transaction():
        conn.execute("INSERT INTO p1test_tx VALUES (1)")
        conn.execute("INSERT INTO p1test_tx VALUES (2)")
    n = conn.execute("SELECT count(*) FROM p1test_tx").fetchone()[0]
    assert n == 2
    conn.execute("DROP TABLE IF EXISTS p1test_tx")
    conn.close()


def test_run_job_full_lifecycle_live():
    ran = []

    def fn(ctx):
        c = ctx["connect"]()
        try:
            neon_store.kv_set(c, "p1test_ran", ctx["dkey"])
        finally:
            c.close()
        ran.append(ctx["dkey"])

    assert jobs.run_job(JOB, fn, store=neon_store, tg=None) == "done"
    assert len(ran) == 1
    conn = neon_store.connect()
    state, attempts, _ = neon_store.job_state(conn, JOB, ran[0])
    assert state == "done" and attempts == 1
    conn.close()


def test_job_state_missing_returns_none():
    conn = neon_store.connect()
    assert neon_store.job_state(conn, "p1test_nope", DK) is None
    conn.close()
