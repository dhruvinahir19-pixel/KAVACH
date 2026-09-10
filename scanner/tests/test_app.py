"""App end-to-end: HTTP layer + real Neon + Telegram mocked. Live DB required."""
import os
import threading
import time

import pytest

from kcore import app as appmod
from kcore import neon_store
from kcore.clock import today_key

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"), reason="live NEON_DATABASE_URL not set")

TODAY = today_key()


@pytest.fixture()
def client(monkeypatch):
    # full env: real NEON URL (from env), dummies for the rest
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
              "UPSTOX_ACCESS_TOKEN", "UPSTOX_TOKEN_GENERATED", "TRIGGER_SECRET"):
        monkeypatch.setenv(k, "dummy-" + k)
    sent = []

    def fake_send(text, token, chat_id):
        sent.append(text)
        return True

    import kcore.telegram as tg
    monkeypatch.setattr(tg, "send", fake_send)
    # clean any earlier selftest rows so the run is deterministic
    conn = neon_store.connect()
    neon_store.init_schema(conn)
    conn.execute("DELETE FROM jobs_log WHERE job = 'selftest' AND dkey = %s", (TODAY,))
    conn.execute("DELETE FROM kv WHERE k = 'selftest_last_run'")
    conn.close()

    app = appmod.create_app()
    app.config["TEST_SENT"] = sent
    return app.test_client()


def _wait_done(timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        conn = neon_store.connect()
        st = neon_store.job_state(conn, "selftest", TODAY)
        conn.close()
        if st and st[0] in ("done", "failed"):
            return st
        time.sleep(0.25)
    return None


def test_health_static_no_db(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.data == b"OK"


def test_trigger_requires_secret(client):
    r = client.post("/trigger/selftest")
    assert r.status_code == 403
    r = client.post("/trigger/selftest", headers={"X-Trigger-Secret": "wrong"})
    assert r.status_code == 403


def test_trigger_unknown_job(client):
    r = client.post("/trigger/zzz", headers={"X-Trigger-Secret": "dummy-TRIGGER_SECRET"})
    assert r.status_code == 404


def test_trigger_unimplemented_job(client):
    r = client.post("/trigger/evening", headers={"X-Trigger-Secret": "dummy-TRIGGER_SECRET"})
    assert r.status_code == 501


def test_selftest_end_to_end(client):
    r = client.post("/trigger/selftest",
                    headers={"X-Trigger-Secret": "dummy-TRIGGER_SECRET"})
    assert r.status_code == 202
    assert r.get_json()["status"] == "accepted"
    # ack-then-run: the HTTP response must arrive BEFORE the job finishes
    st = _wait_done()
    assert st is not None, "selftest job never finished"
    assert st[0] == "done", f"selftest failed: {st}"
    conn = neon_store.connect()
    assert neon_store.kv_get(conn, "selftest_last_run") == TODAY
    conn.close()
    # duplicate trigger same day -> skipped, still exactly one done row
    r2 = client.post("/trigger/selftest",
                     headers={"X-Trigger-Secret": "dummy-TRIGGER_SECRET"})
    assert r2.status_code == 202
    time.sleep(1.0)
    conn = neon_store.connect()
    rows = conn.execute(
        "SELECT count(*) FROM jobs_log WHERE job='selftest' AND dkey=%s", (TODAY,)
    ).fetchone()[0]
    assert rows == 1
    conn.close()
