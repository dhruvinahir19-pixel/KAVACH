# KAVACH-945

**कवच — "the armor".** An evening-selected, morning-confirmed intraday sniper protocol for NSE stock equity, armored with a hard 1% disaster stop.

> ⚠️ **Status: backtest-validated research system — NOT live.** All numbers below are 18-month out-of-sample backtests (2025-04 → 2026-09) with modeled costs. The system has never traded live. The gate before any capital is the forward paper-trading phase (see `FAILURE_MODES.md` / master report §13).

## The stack (six layers)

| # | Layer | What it does |
|---|---|---|
| 1 | **SELECT** | Evening: gradient-boosted model scores the F&O universe for tomorrow's top-quintile movers → Top-10 watchlist |
| 2 | **TILT** | Futures basis z-score → directional lean (discount = bearish context; it's an overnight lens, not an intraday filter) |
| 3 | **REVEAL** | 9:45 next morning: price vs 9:15–9:30 opening range → LONG above OR high / SHORT below OR low / else no trade |
| 4 | **SNUFF** | If a side has >3 confirmed candidates → keep only the 2 calmest by 20-day range (correlation control) |
| 5 | **ARMOR** | Hard bracket stop 1.0% adverse from the 9:45 entry, every trade, no discretion |
| 6 | **EXIT** | 15:10 manual exit (before broker auto square-off); risk ≈1% of capital per trade |

## Headline backtest (18 OOS months, net of 0.15% cost + stop slippage)

| Metric | Value |
|---|---|
| Trades | 986 (493 long / 493 short) over 346/355 mornings |
| Net avg / trade | **+0.42%** |
| Net avg / day (1× book) | +1.21% · 58% win-days · +417pp total |
| **Worst trade** | **−1.20%** (armored; unarmored: −11.07%) |
| **Worst day** | **−4.80%** (armored; unarmored: −15.25%) |
| Losing months | 1 of 18 |
| Stopped trades | 453/986 (46%) — the price of the armor |

## Repository layout

```
KAVACH/
├── engine/          research engine (12 modules) — selection, features, validation,
│                    direction prior, morning table, backtests, KAVACH protocol
│   └── output/      stage reports + small record artifacts (trade log, OOS predictions)
├── docs/            KAVACH-945 master report (self-contained rebuild document)
├── scanner/         live scanner (Telegram signals) — built in phases P1–P5
├── scripts/         verification harness (verify_engine.py — the reproduction gate)
├── FAILURE_MODES.md living catalogue of live-failure modes + preventions + detections
└── .env.example     secrets contract (never commit real .env)
```

`engine/data/` is **not** in the repo (size discipline). Rebuild data with the commands in the master report §11 — all sources are free (NSE bhavcopy archives, Upstox v3 historical API, Yahoo VIX).

## Quickstart (engine)

```bash
# deps: python3 + pandas, numpy, scikit-learn, requests  (nothing else)
cd engine
python3 backfill.py                              # EOD stores 2024-10-01 → present
python3 candles.py backfill                      # 5-min candles, current year
python3 candles.py window 2025-01-01 2025-12-31  # 2025 candles (for SL studies)
python3 features.py && python3 validate.py && python3 morning.py
python3 protocol_v2.py                           # the armored book
```

Verify a rebuild with the reproduction gate:

```bash
python3 scripts/verify_engine.py
# PASS trades: 986 | mornings: 346 | stopped: 453 | worst day −4.80% | worst trade −1.20%
```

## Documentation

- **`docs/KAVACH-945_report.html`** — the master report: full system spec, every formula and constant, stage-by-stage evidence, audits, rebuild guide, execution playbook. If everything else is lost, rebuild from this file.
- `engine/output/*.md` — stage reports (backtest, protocol, robustness).
- `engine/README.md`, `engine/METHODOLOGY.md` — engine usage and research discipline (incl. the bug log).
- `FAILURE_MODES.md` — every way the live system can die silently, and how each is prevented/detected.

## Build phases (scanner)

| Phase | Scope | Status |
|---|---|---|
| P0 | Repo & foundation, verification gate | ✅ |
| P1 | Shared core & state layer (Neon, Telegram, clients, job runner) | ⬜ |
| P2 | Evening pipeline (harvest → features → score → watchlist message) + universe maintenance (daily F&O diff; monthly 1st refresh: isin_map, history backfill, report) | ⬜ |
| P3 | Morning engine (9:45 signal with entries + stops) | ⬜ |
| P4 | Deployment (Render Docker, pingers, watchdogs) | ⬜ |
| P5 | Paper-trading shakedown (live, zero capital) | ⬜ |

Protocol per phase: discuss → research → error-hunt → build → verification gate → sign-off.

## Policies

- **No secrets in the repo, ever.** See `.env.example` for the contract. Pre-push secret scans are part of every phase gate.
- **No data files in the repo.** Data lives in the (rebuildable) stores and, for the scanner, in Neon.
- **One source of truth for strategy logic** — shared rules file used by both research and live scanner; parity-tested (no logic mismatch between backtest and signals).
- India/NSE only · equity intraday only (F&O is a signal source, never a vehicle) · no indicator-based signals · free data only.
