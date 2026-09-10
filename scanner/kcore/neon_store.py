"""
neon_store.py — Neon Postgres access layer (pooler-compatible).

Driver rules VERIFIED LIVE against the production pooler endpoint (P1 probe,
2026-09-10 — see FAILURE_MODES.md P1-01..P1-04):
  - connect with autocommit=True; atomic multi-writes via `with conn.transaction():`
    (psycopg3 default autocommit=False + any execute leaves you INTRANS and unable
    to flip the flag — classic trap)
  - prepare_threshold=None: the pooler (PgBouncer, transaction mode) breaks named
    prepared statements
  - NO startup `options=` (pooler rejects them: "unsupported startup parameter")
  - statement timeouts only via SET LOCAL inside a transaction
  - short-lived connections; never hold one for a whole multi-minute job
  - cold-start retry ladder (Neon suspends after 5 min idle; ~1s wake)

All writes are tiny and bounded (egress discipline: pingers never touch this DB,
no endpoint ever returns table contents).
"""
import os
import time

import psycopg
from psycopg import OperationalError

RETRY_DELAYS = (0.4, 0.8, 1.6, 3.2)   # cold-start ladder

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  k        TEXT PRIMARY KEY,
  v        TEXT,
  updated  TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS holidays (
  dkey     TEXT PRIMARY KEY,
  note     TEXT
);
CREATE TABLE IF NOT EXISTS jobs_log (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job         TEXT NOT NULL,
  dkey        TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|failed|skipped
  attempts    INT  NOT NULL DEFAULT 0,
  detail      TEXT,
  started_at  TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  heartbeat   TIMESTAMPTZ,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (job, dkey)
);
CREATE TABLE IF NOT EXISTS alert_log (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  dkey       TEXT,
  severity   TEXT,
  message    TEXT,
  delivered  BOOLEAN,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS universe (
  symbol     TEXT PRIMARY KEY,
  isin       TEXT NOT NULL,
  active     BOOLEAN DEFAULT TRUE,
  scoreable  BOOLEAN DEFAULT FALSE,      -- ~21 EOD sessions -> rolling windows formed
  first_seen DATE,
  last_seen  DATE
);
CREATE TABLE IF NOT EXISTS watchlists (
  dkey       TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  rank       INT,
  prob       REAL,
  basis_z    REAL,
  prior      INT,
  range20    REAL,
  prior20h   REAL,                        -- P3: cap tiebreak range20 = (h-l)/c945
  prior20l   REAL,
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (dkey, symbol)
);
CREATE TABLE IF NOT EXISTS signals (
  dkey       TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  side       INT NOT NULL,               -- +1 long, -1 short
  entry      REAL,
  stop       REAL,
  or15_h     REAL,
  or15_l     REAL,
  c945       REAL,
  status     TEXT DEFAULT 'sent',
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (dkey, symbol)
);
CREATE TABLE IF NOT EXISTS model_blob (
  version         TEXT PRIMARY KEY,
  trained_through TEXT,
  payload         BYTEA,
  sha256          TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS eod_daily (
  date       DATE NOT NULL,
  symbol     TEXT NOT NULL,
  prev_close DOUBLE PRECISION, open DOUBLE PRECISION, high DOUBLE PRECISION,
  low DOUBLE PRECISION, close DOUBLE PRECISION, volume DOUBLE PRECISION,
  turnover_l DOUBLE PRECISION, trades DOUBLE PRECISION,
  deliv_qty  DOUBLE PRECISION, deliv_per DOUBLE PRECISION,
  nm_expiry  TEXT, nm_close DOUBLE PRECISION, nm_oi DOUBLE PRECISION,
  fut_oi DOUBLE PRECISION, fut_oi_chg DOUBLE PRECISION, fut_vol DOUBLE PRECISION,
  fut_val DOUBLE PRECISION, fut_txns DOUBLE PRECISION,
  ce_oi DOUBLE PRECISION, pe_oi DOUBLE PRECISION, ce_oi_chg DOUBLE PRECISION,
  pe_oi_chg DOUBLE PRECISION, ce_vol DOUBLE PRECISION, pe_vol DOUBLE PRECISION,
  ce_val DOUBLE PRECISION, pe_val DOUBLE PRECISION, top3_conc DOUBLE PRECISION,
  call_build DOUBLE PRECISION,
  PRIMARY KEY (date, symbol)
);
CREATE TABLE IF NOT EXISTS eod_mkt (
  date       DATE PRIMARY KEY,
  nifty_close DOUBLE PRECISION, banknifty_close DOUBLE PRECISION,
  nifty_pcr  DOUBLE PRECISION, next_expiry TEXT, vix DOUBLE PRECISION,
  client_stf_net DOUBLE PRECISION, client_idf_net DOUBLE PRECISION,
  fii_stf_net DOUBLE PRECISION, fii_idf_net DOUBLE PRECISION,
  dii_stf_net DOUBLE PRECISION, dii_idf_net DOUBLE PRECISION,
  pro_stf_net DOUBLE PRECISION, pro_idf_net DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs_log (state);
CREATE INDEX IF NOT EXISTS idx_alert_dkey ON alert_log (dkey);
"""


# ---------------------------------------------------------------- connection
def connect(url: str | None = None, *, connect_timeout: int = 5) -> psycopg.Connection:
    """Connect with the pooler-safe flags, retrying through Neon cold starts."""
    url = url or os.environ.get("NEON_DATABASE_URL")
    if not url:
        raise RuntimeError("NEON_DATABASE_URL not set (config.load() must run first)")
    last: Exception = RuntimeError("no attempts")
    for i in range(len(RETRY_DELAYS) + 1):
        try:
            return psycopg.connect(
                url,
                connect_timeout=connect_timeout,
                prepare_threshold=None,
                autocommit=True,
            )
        except OperationalError as e:          # cold start / transient refusal
            last = e
            if i < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[i])
    raise last


def init_schema(conn: psycopg.Connection) -> None:
    """Idempotent DDL. Safe to run at every boot."""
    conn.execute(SCHEMA)
    # P3 migration: cap tiebreak needs prior20h/l saved by the evening job.
    conn.execute("ALTER TABLE watchlists ADD COLUMN IF NOT EXISTS prior20h REAL")
    conn.execute("ALTER TABLE watchlists ADD COLUMN IF NOT EXISTS prior20l REAL")


def ensure(conn: psycopg.Connection) -> None:
    """Public boot/job-start hook: guarantees the schema exists before first use.
    Called at app boot (best effort) and at every job claim (cheap, idempotent)."""
    init_schema(conn)


# ---------------------------------------------------------------- kv
def kv_set(conn: psycopg.Connection, k: str, v: str) -> None:
    conn.execute(
        "INSERT INTO kv (k, v, updated) VALUES (%s, %s, now()) "
        "ON CONFLICT (k) DO UPDATE SET v = excluded.v, updated = now()",
        (k, v),
    )


def kv_get(conn: psycopg.Connection, k: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT v FROM kv WHERE k = %s", (k,)).fetchone()
    return row[0] if row else default


# ---------------------------------------------------------------- alerts
def log_alert(conn: psycopg.Connection, dkey: str, severity: str,
              message: str, delivered: bool) -> None:
    conn.execute(
        "INSERT INTO alert_log (dkey, severity, message, delivered) "
        "VALUES (%s, %s, %s, %s)",
        (dkey, severity, message[:1000], delivered),
    )


# ---------------------------------------------------------------- jobs
def claim_job(conn: psycopg.Connection, job: str, dkey: str, *,
              max_attempts: int = 3, stale_min: int = 15) -> str:
    """Atomically claim (job, dkey). Returns one of:
       'claimed'          — this caller owns the run (state -> running, attempts+1)
       'skip-done'        — already finished successfully (or skipped) today
       'skip-running'     — a live run is in progress (fresh heartbeat)
       'skip-max-attempts'— failed too many times today; needs human eyes
    Concurrency-safe: the INSERT .. ON CONFLICT DO NOTHING and the conditional
    UPDATE .. RETURNING are the only two arbiters; losers observe the winner's
    post-state and skip. (Verified live: concurrent claims yield exactly one winner.)
    """
    row = conn.execute(
        "INSERT INTO jobs_log (job, dkey, state, attempts, started_at, heartbeat) "
        "VALUES (%s, %s, 'running', 1, now(), now()) "
        "ON CONFLICT (job, dkey) DO NOTHING RETURNING job",
        (job, dkey),
    ).fetchone()
    if row:
        return "claimed"

    cur = conn.execute(
        "SELECT state, attempts, heartbeat FROM jobs_log WHERE job = %s AND dkey = %s",
        (job, dkey),
    ).fetchone()
    if not cur:
        return "skip-running"                    # vanished mid-race; safest answer
    state, attempts, _hb = cur

    if state in ("done", "skipped"):
        return "skip-done"
    if state == "running":
        fresh = conn.execute(
            "SELECT 1 FROM jobs_log WHERE job = %s AND dkey = %s "
            "AND heartbeat >= now() - (%s || ' minutes')::interval",
            (job, dkey, str(stale_min)),
        ).fetchone()
        if fresh:
            return "skip-running"
        # stale 'running' (killed process) -> reclaimable below

    if attempts >= max_attempts:
        return "skip-max-attempts"

    row = conn.execute(
        "UPDATE jobs_log SET state = 'running', attempts = attempts + 1, "
        "started_at = now(), heartbeat = now() "
        "WHERE job = %s AND dkey = %s AND ("
        "      state IN ('pending', 'failed') "
        "   OR (state = 'running' AND heartbeat < now() - (%s || ' minutes')::interval)"
        ") RETURNING job",
        (job, dkey, str(stale_min)),
    ).fetchone()
    if row:
        return "claimed"
    return "skip-running"                        # lost the reclaim race


def heartbeat(conn: psycopg.Connection, job: str, dkey: str) -> None:
    conn.execute(
        "UPDATE jobs_log SET heartbeat = now() "
        "WHERE job = %s AND dkey = %s AND state = 'running'",
        (job, dkey),
    )


def finish_job(conn: psycopg.Connection, job: str, dkey: str,
               state: str, detail: str | None = None) -> None:
    if state not in ("done", "failed", "skipped"):
        raise ValueError(f"invalid terminal state: {state}")
    conn.execute(
        "UPDATE jobs_log SET state = %s, detail = %s, finished_at = now(), "
        "heartbeat = now() WHERE job = %s AND dkey = %s",
        (state, (detail or "")[:1000], job, dkey),
    )


def job_state(conn: psycopg.Connection, job: str, dkey: str):
    """Returns (state, attempts, detail) or None."""
    row = conn.execute(
        "SELECT state, attempts, detail FROM jobs_log WHERE job = %s AND dkey = %s",
        (job, dkey),
    ).fetchone()
    return tuple(row) if row else None
