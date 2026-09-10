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

## Phase P0 — repo & foundation

| ID | Failure mode | P / D |
|----|--------------|-------|
| P0-01 | 76 MB of data stores pushed to the repo | **P:** .gitignore engine/data/. **D:** gate: `git ls-files` audit — no data paths |
| P0-02 | Secret committed | **P/D:** pre-push grep scan for ghp_/token patterns |
| P0-03 | Repo code drifts from workspace engine (silent divergence) | **D:** verify_engine.py reproduction gate — fresh clone must reproduce 986 trades / 346 mornings / 453 stopped / worst day −4.80% |
| P0-04 | Transient /tmp dependency mistaken for persistent | **D:** documented — /tmp is wiped between sessions (observed); state lives in workspace/Neon only |
| P0-05 | `import config` creates EMPTY `engine/data/` and `engine/output/` dirs → a subsequent `cp -r <data> engine/data` NESTS the copy as `engine/data/data/` → jobs silently see zero data (**caught live by the P0 gate: "No objects to concatenate"**) | **P:** when `engine/data` already exists, copy CONTENTS: `cp -r <src>/* engine/data/`. **D:** verify_engine.py fails loudly on missing data; job boot self-check asserts non-empty stores |

## Phase P1 — shared core & state layer (to be extended at pre-phase discussion)

Placeholder — filled during P1 error-hunt: Neon cold start ~1s, connection pooling, partial
writes (transactions), kill-mid-job recovery, Telegram edge cases, mock-based unit tests.

## Phase P2 — evening pipeline (placeholder)

## Phase P3 — morning engine (placeholder)

## Phase P4 — deployment & hardening (placeholder)

## Phase P5 — paper-trading shakedown (placeholder)
