# KAVACH-945 — FAILURE_MODES.md (living document)

Every way this system can fail in live operation, with its prevention and its detection.
**Core rule: no failure may be silent.** Every entry has a detection path that ends in a
Telegram alert or a loud verification failure. This file is extended at every phase's
pre-phase discussion and reviewed at every verification gate.

Legend: **P**=prevention (designed out) · **D**=detection (alerts/tests catch it)

## Cross-cutting (all phases)

| ID | Failure mode | P / D |
|----|--------------|-------|
| F-01 | Render free service sleeps after 15 min → internal scheduler dies silently; 9:45 passes with no signal and no error | **P:** external triggers (cron-job.org) + always-on keep-alive (UptimeRobot 5-min pings); schedule never lives only inside the service. **D:** watchdog job ~09:50 IST checks Neon for the morning job's success row; missing → Telegram alert (independent path) |
| F-02 | Ephemeral filesystem: deploy/restart wipes all local state (model, flags, "already sent" markers) | **P:** 100% of state in Neon; idempotency keys (job, date) in DB. **D:** boot self-check asserts DB reachable + expected tables before serving |
| F-03 | 750 instance-hours/month exhausted → whole workspace suspended till month-end | **P:** exactly ONE always-on free service in the workspace; hours ≈ 744 < 750. **D:** weekly calendar check of Render dashboard; monthly report |
| F-04 | Secrets leak into the public repo | **P:** .gitignore (.env), .env.example contract, secrets only in Render env vars. **D:** pre-push secret scan (grep for token patterns) at every push; GitHub secret scanning on public repos |
| F-05 | cron-job.org free tier: 30s timeout; auto-DISABLES a job after 15 consecutive "failures"; 4–40s jitter | **P:** every endpoint acks in <5s and runs work in background; success judged by Neon rows, never by HTTP response. **D:** cron-job.org failure emails + endpoint's own job log |
| F-06 | Same-day (intraday) candles NOT served by the free no-auth Upstox endpoint — **verified live 2026-09-10: 0 bars at 15:46 IST for that day** | **P:** morning job uses authenticated endpoints with the 1-year Analytics token (T1 test picks the exact endpoint). **D:** morning fallback ladder ends in an explicit "data path failed" Telegram alert — never silence |
| F-07 | Neon free 5 GB/month network transfer exhausted → compute suspended (GARUDA precedent) | **P:** pingers hit Render only, never Neon; /health is static and DB-free; no endpoint returns tables; model blob cached at boot; ~75–140 MB/month budget vs 5 GB. **D:** weekly usage check + alert |
| F-08 | Upstox Analytics token: 1-year expiry; one-per-account (regenerating revokes the old instantly); account-scoped classic-style risk | **P:** store generation date; token only in env vars. **D:** T-30-day expiry alert; on 401 → immediate alert (not retry-forever) |
| F-09 | Telegram: bot cannot message user until user sends /start; 429 rate limits | **P:** /start ritual in setup checklist; ≤5 msgs/day. **D:** every sendMessage response checked; failure logged + retried with backoff |
| F-10 | NSE bhavcopy late/missing on the evening run | **P:** retry ladder 20:15 / 20:45 / 21:15 IST. **D:** alert that distinguishes holiday (known table) vs failure (unexpected) |
| F-11 | Clock/timezone drift; IST has no DST but hosts run UTC | **P:** single IST clock module as the only time source; cron-job.org jobs defined in IST natively. **D:** timestamps logged with every job row; watchdog compares |
| F-12 | Duplicate triggers (pinger + cron + manual) double-send a 9:45 signal | **P:** idempotency key (job, date) — second trigger no-ops. **D:** job log shows skip reason |
| F-13 | NSE WAF/403 from datacenter IP | **P:** UA header discipline (proven from datacenters for months). **D:** fetch failure alerts; workspace fallback for rebuilds |
| F-14 | Logic drift between research engine and live scanner (backtest≠signal) | **P:** ONE shared rules/features module used by both. **D:** parity test — workspace recompute vs server rows diff ≈ 0 |
| F-15 | Universe drift: NSE adds/removes F&O stocks; stale universe scores dead symbols or misses new movers | **P:** daily evening diff of FO bhavcopy symbol set vs Neon universe table (catches true effective dates — removals often at series expiry, not the 1st); monthly refresh job on the 1st: EQUITY_L.csv → isin_map rebuild, history backfill for additions, reconciliation + Telegram report; aligned with monthly model retrain. **D:** diff logged every evening; monthly report message; a pick absent from today's bhavcopy fails loudly at fetch |
| F-16 | Newly added F&O symbol scored with short history → rolling windows (prior20, basis_z 20d, v45 median) not formed → NaN/garbage features | **P:** eligibility rule — scoreable only after ~21 contiguous EOD sessions (windows formed); young symbols excluded-and-noted in nightly diagnostics. **D:** NaN-feature assertion before scoring; exclusion counter in evening job log |

## Phase P0 — repo & foundation

| ID | Failure mode | P / D |
|----|--------------|-------|
| P0-01 | 76 MB of data stores pushed to the repo | **P:** .gitignore engine/data/. **D:** gate: `git ls-files` audit — no data paths |
| P0-02 | Secret committed | **P/D:** pre-push grep scan for ghp_/token patterns |
| P0-03 | Repo code drifts from workspace engine (silent divergence) | **D:** verify_engine.py reproduction gate — fresh clone must reproduce 986 trades / 346 mornings / 453 stopped / worst day −4.80% |
| P0-04 | Transient /tmp dependency mistaken for persistent | **D:** documented — /tmp is wiped between sessions (observed); state lives in workspace/Neon only |
| P0-05 | `import config` creates EMPTY `engine/data/` and `engine/output/` dirs → a subsequent `cp -r <data> engine/data` NESTS the copy as `engine/data/data/` → jobs silently see zero data (**caught live by the P0 gate: "No objects to concatenate"**) | **P:** when `engine/data` already exists, copy CONTENTS: `cp -r <src>/* engine/data/`. **D:** verify_engine.py fails loudly on missing data; job boot self-check asserts non-empty stores |

## Phase P1 — shared core & state layer (COMPLETE — 43/43 gate green)

Built: `scanner/kcore/` (clock, config, neon_store, upstox_client, nse_client, telegram,
jobs, app) + `scanner/tests/` (unit mocks + live-Neon suite). Verified live on the real
pooler endpoint. Failures found and designed out during P1:

| ID | Failure mode | P / D |
|----|--------------|-------|
| P1-01 | Neon pooler rejects libpq startup `options` (statement_timeout) — connect fails outright | **P:** no startup options ever; timeouts via `SET LOCAL` inside transactions only. **D:** caught by live probe before any code was written |
| P1-02 | psycopg3 default autocommit=False + any execute leaves INTRANS; flipping autocommit then raises | **P:** connect(autocommit=True) everywhere; atomic writes via `with conn.transaction():`. **D:** probe + live atomicity test (mid-tx crash leaves zero partial rows) |
| P1-03 | Pooler (PgBouncer txn mode) breaks named prepared statements — works in dev, dies in prod | **P:** `prepare_threshold=None` on every connect. **D:** documented + enforced in neon_store.connect (single connection factory) |
| P1-04 | IPv6 AAAA attempts fail (unreachable) in some datacenters before IPv4 fallback — noisy but harmless | **D:** observed in sandbox; recognized as noise, not an outage signal |
| P1-05 | Fresh database has no schema → first job's claim hits UndefinedTable | **P:** `ensure()` (idempotent DDL) at app boot (best-effort) AND at every job start. **D:** caught by live test suite on the virgin DB |
| P1-06 | Permanent 4xx (400/404) retried pointlessly, wasting the morning window | **P:** fail-fast `raise_for_status()` for non-transient 4xx; backoff only for 429/5xx/network. **D:** unit test asserts call counts |
| P1-07 | SQL precedence trap in job-reclaim WHERE (AND binds tighter than OR — reclaim could touch OTHER jobs' rows) | **P:** parenthesized compound predicates. **D:** caught in self-review before ship; live tests assert reclaim scope |
| P1-08 | Job killed mid-run stays 'running' forever, blocking all future runs | **P:** heartbeats every 10s; stale-running (>15 min) is reclaimable via conditional UPDATE..RETURNING (atomic arbiter). **D:** live test simulates stale + reclaim |
| P1-09 | Duplicate/concurrent triggers double-execute | **P:** INSERT..ON CONFLICT DO NOTHING + conditional UPDATE..RETURNING as the only arbiters. **D:** live 6-thread concurrent-claim test — exactly one winner |

Gate (2026-09-10): 43/43 passed — unit (clock, config, telegram, upstox, jobs, static
hygiene) + live-Neon (schema idempotency, kv, claims, concurrency, retries-to-max, stale
reclaim, tx atomicity, full run_job lifecycle, app end-to-end incl. duplicate-trigger
skip). Production schema now live in the Neon project (9 tables).

## Phase P2 — evening pipeline (COMPLETE — 56/56 gate green)
All preview items resolved plus the following observed/designed failure modes:

- **P2-01 bhavcopy posting order** (measured 2026-09-10): cash posts before FO UDiFF
  (cash 200 at 17:18, FO still 404). During the retry ladder "cash OK but FO missing"
  is a `skipped:not-posted-yet`, NOT a failure. Only after the ladder ends (21:55)
  does a missing bhavcopy fail loud (alert + no watchlist). First trigger 20:00,
  retries 20:30/21:00/21:30/22:00 — measured margin to cash posting is ~3h.
- **P2-02 VIX fail-loud (D2)**: no VIX row → alert + RuntimeError BEFORE any Neon
  write (upsert ordering is sanity → VIX → upsert). Never carry-forward — a stale
  VIX would silently poison vix features.
- **P2-03 model freshness**: blob asserts `trained_through < scoring date`; a model
  trained through T cannot score T (would be in-sample leakage).
- **P2-04 NaN features are NOT failures**: HistGradientBoosting handles NaN natively
  and the research panel contained NaN rows (young symbols) — adding an exclusion
  here would diverge from backtest logic. Top picks with thin history get a note in
  the message instead ("partial-window picks").
- **P2-05 universe maintenance baseline**: must compare against RECENT activity
  (sessions since last_seen in eod_daily), never the all-time known set — the daily
  FO universe is ~210 symbols vs 268 cumulative; all-time comparison falsely
  deactivates ~58 healthy symbols within 5 days. 5 missed sessions → deactivate;
  return → re-activate; ≥21 sessions → scoreable (F-16).
- **P2-06 parity contract**: scanner path (Neon → 480-session cache → features_core
  → model) vs workspace path (stores → full history → model) must match to 1e-6 on
  probs with IDENTICAL top-10 (frozen fixture `tests/fixtures/parity_20260909.json`,
  207 candidates, max observed drift 5e-8). Candidate set = DB `active ∧ scoreable`
  — the DB is authoritative (matches the research daily cross-section).
- **P2-07 NSE revises bhavcopy files post-publication** (observed: TURNOVER_LACS
  83740.9 → 83740.87 for 2026-09-09 between two fetches 24h apart). Raw inputs can
  drift ~1e-7 relative after a revision; the fixture tolerance (1e-6) prices this
  in. Parity compares probs, not raw cells, as the primary gate.
- **P2-08 inherited dead features (documented, NOT fixed)**: after the (date,symbol)
  sort in features_core's market layer, vix_chg1/vix_chg5/nifty_ret1 are ~0.0 for
  all but one symbol per day. Dead in research training AND validation AND
  production scoring — validated metrics already price this in. Fixing = retrain +
  revalidation = explicit user decision for a later phase. vix LEVEL is correct.
- **P2-09 test-data pollution vector**: a test that upserts real dates can overwrite
  production rows (observed: mock vix 13.5 overwrote the real 11.92 for 09-09
  during a broken test run). Full-path tests must relabel to a far-future date
  (2099-01-05) and clean up; parity fixtures must pin the candidate set.
- **P2-10 clock discipline**: triggers outside 20:00–22:00 IST (weekdays, non-holiday)
  raise off-schedule; weekend/holiday/not-posted return skipped states recorded by
  the job runner. Test seam: `clock._now`.
- **P2-11 process-global EodCache**: boot-once-append-daily is correct in production
  (Render restarts daily on wake), but tests MUST reset `kcore.eod_cache._cache_singleton`
  between cases or a stale cache masks per-test state.
- **P2-12 store hygiene**: rewriting stores from harvest frames mixes date formats
  ("2026-09-09 00:00:00" vs "2026-09-09") — pandas leaves the column object-dtype
  and merges silently degrade. Normalize with `pd.to_datetime(col, format='mixed')`
  and rewrite with `date_format='%Y-%m-%d'`; part/mkt must stay date-sorted.

## Phase P3 — morning engine (placeholder)

## P3-07b / P3-08 — rule change + timing study (2026-09-11, user-directed)
- **P3-08 STRICT MAX 2 TRADES/DAY (production rule, user directive)**:
  previously cap = 2 per SIDE only when >3 confirmed on a side (research
  Stage-5 wording) -> up to 6/day. User wants hard max 2/day total; when more
  confirm, keep the 2 CALMEST by range20 regardless of side (a whole side can
  be dropped). 2026 evidence (167 mornings): 312 vs 466 trades, win 51.6% vs
  46.8%, +0.69% vs +0.45%/trade, +216 vs +208pp, worst day -2.40% vs -4.80%.
  August 2026: 40 trades, 52.5% win, +0.43%/trade, worst day -1.20%, Rs 20k
  -> Rs 22,083 (+10.4%). FULL 18-month revalidation (needs 2025 candles
  refetch) scheduled before Monday.
- **P3-09 entry-timing study (why 09:45, not the first breakout candle)**:
  early breaks that FADE back inside by 09:45 (whipsaws): 135 cases in 2026,
  21.5% win, avg -0.38% — the 09:45 wait exists to filter exactly these.
  Entering earlier on the trades that DO hold shows +0.96%/trade, but that
  number is hindsight (unknowable at entry). The implementable comparison:
  enter-at-first-break (all early breaks incl. whipsaws) = 45.6% win,
  +0.56%/trade vs the 09:45 rule = 51.0% win, +0.70%/trade on identical
  costs. Verdict: the 09:45 decision point stays (also the 18-month-validated
  fill convention).

## Phase P3.5 — live-data source map (measured 2026-09-10 night, all live probes)
- **P4-06 Upstox source behaviors (measured)**:
  - public v3 intraday 5m: works after close (75 bars, == NSE closes) BUT goes
    EMPTY for all symbols late night (~23:20 IST) — nightly reset; in-market
    behavior UNKNOWN until first live morning.
  - auth (Bearer) v3 intraday: 200-EMPTY — the Analytics token BREAKS this
    endpoint. Never use auth on v3 intraday.
  - v2 intraday (auth): also 200-EMPTY post-close (kept in chain as in-market
    possibility only).
  - v3 historical to=today: serves SAME-DAY bars post-close, BOTH public and
    authenticated (verified 10/10 watchlist symbols; data identical to intraday
    where both exist).
  - Chain order in morning.py: public-intraday -> public-historical-today ->
    auth-historical-today (token) -> auth-v2-intraday-1min. First source with
    TODAY-dated non-empty bars wins; stale/empty falls through; all-fail =
    loud abort (P3-06). Live-verified fall-through during the intraday
    endpoint's dark window (10/10 via public-historical-today).
  - After midnight the whole chain correctly reports no data for the new day
    (market not open) — expected pre-market state, not an outage.
- **Token lifecycle**: Analytics token issued 2026-05-28, expires 2027-05-29;
  stored at ~/.upstox_token mode 600 (sandbox) / UPSTOX_ACCESS_TOKEN (Render);
  regenerated token invalidates the old one instantly.

## Phase P4 — deployment & hardening (LIVE — kavach-scanner.onrender.com)
- **P4-01 skipped-is-terminal (CRITICAL, found pre-live)**: the job runner
  treated state='skipped' as final for the day. The 09:44 wake ping's
  'skipped:too-early' would have PERMANENTLY blocked the real 09:46 morning
  run — silently, no signal, no alert. Same for the evening retry ladder after
  'not-posted-yet'. Fix: 'skipped' is re-claimable; finish_job resets attempts
  on skip (only real failures consume the max_attempts budget). Live-verified
  on Neon: skip(09:44) -> claimed(09:46) -> skip-done(third).
- **P4-02 watchdogs**: POST /watchdog/{morning,evening} after the windows
  (09:52 / 20:40 IST). Verdicts: done/weekend/holiday = ok; running = ok;
  MISSING / FAILED / STUCK-SKIPPED = Telegram ERROR. Absence of a signal is
  itself an alarmed event.
- **P4-03 independent second clock**: GitHub Actions backup-triggers.yml
  (09:44/09:48 trigger, 09:54 watchdog, 20:03 trigger, 20:22 watchdog IST).
  GHA cron is jittery by design -> the workflow maps its own start time to an
  action window, so lateness is safe; idempotent re-triggers absorb doubles.
  NOTE: pushing the .yml requires the PAT to have `workflow` scope (classic
  tokens refuse workflow-file creation without it — hit during first push).
- **P4-04 keep-alive**: UptimeRobot pings /health every 5 min (static route,
  never touches Neon — no egress burn). Render cold start (~45-60s) is
  absorbed by the 09:44 wake + 09:46 run pair; if 09:44 lands post-09:45:40
  anyway, the run is still valid (idempotent, in-window).
- **P4-05 in-chat schedulers are unreliable**: long sleeps in the sandbox
  drift when the box idles (evening watcher woke ~2 min late; morning watcher
  never got to fire). Production timers MUST live in the cloud; in-chat runs
  are manual/interactive only.

## Phase P5 — paper-trading shakedown (placeholder)
