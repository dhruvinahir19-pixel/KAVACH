# Next-Day Momentum Fingerprint Engine v2 — 2026 Rebuild

Predicts WHICH NSE F&O stocks are most likely to move violently tomorrow
(**movement, not direction**). Rebuilt from the original Sep'24–Sep'26
research with 2026-only data, under a strict 128MB workspace budget.

## Validated results (walk-forward, out-of-sample, 2026 only)

| Metric | v2 (2026-only) | Original engine |
|---|---|---|
| OOS AUC | **0.702** | 0.714 |
| Top-10 hit rate | **51.6%** (base 20%) | 54.2% |
| Range capture | **1.41×** | 1.46× |

Same fingerprint family confirmed on fresh 2026 data: ATR compression
(IC −0.184), today's expansion (+0.166), turnover/volume/trade surges
(+0.116 to +0.126), delivery×volume (+0.105), futures-volume surge (+0.121),
option-activity surge (+0.123). NR7 still negative (myth still busted).

Monthly OOS: Apr 48% · May 52% · Jun 44% · Jul 54% · Aug 52% · Sep 60%

## Space discipline (128MB budget)

- Raw NSE files are downloaded to `/tmp` (outside the workspace) and parsed
  in memory — **never** written to the workspace.
- Only compact per-stock aggregates are stored: `data/*.csv.gz`.
- Current footprint: **~26MB total** (panel + aggregates + outputs for
  202 sessions, 227 symbols). A full year adds ~10MB.

## Stage 2 — Direction layer (validated April–September 2026)

**The a priori trap framework was tested and rejected.** Swing-failure reversals,
wick rejections, delivery absorption: signs flip between halves (wick_lo lift
+12.7pp in H1 → −6.0pp in H2). Reversal signals at EOD horizon are regime-fragile.

**What survived (stable in both halves, on actual OOS picks):**

| Signal | n | Next-day outcome | Stability |
|---|---|---|---|
| Futures **discount** (basis_z ≤ −1.5) on a pick | 137 | **66.4% DOWN**, avg −0.76%, short MFE/MAE +2.45%/−1.26% | H1: 66.7% · H2: 66.2% |
| Futures premium (basis_z > +1.5) on a pick | 110 | 55.5% up, avg +0.31% | H1: 54.7% · H2: 56.1% |
| Neutral basis | 822 | 54.5% up (base rate) | — |

Not a momentum proxy: among picks that ROSE today, discount still → 32.7% up
vs 56.6% for non-discount. Daily IC of basis_z vs next-day return: +0.05 (H1),
+0.15 (H2). Worst adverse move for a discount-short: +10% (squeeze risk —
stops non-negotiable).

**Interpretation:** at the EOD→next-day horizon on momentum candidates, FOLLOW
visible leveraged positioning, don't fade it. Aggressive futures shorting
(discount) continues down; aggressive longs (premium) mildly continue up.
`direction.py annotate_latest()` appends this to every watchlist
(SHORT ★★ / LONG-OK / NEUTRAL).

## Intraday candle store (5-minute bars, 2026)

- Source: **Upstox v3 public historical API** (no auth):
  `api.upstox.com/v3/historical-candle/NSE_EQ|{ISIN}/minutes/5/{to}/{from}`
- API rules (verified + official docs): max **1 month per request** for
  intervals ≤15 min (1 quarter for >15 min); rate limits 50/sec, 500/min,
  **2,000 per 30 min**. Our governor: 6 workers, 5/sec, 280/min, 1,800/30min,
  700s pause on any 429. Full backfill of 226 stocks × 9 months ≈ 4 min.
- Store: `data/candles/{SYMBOL}.csv.gz` — epoch_min + paise-int OHLC + volume
  (zero-volume filler bars dropped). 226 stocks × 171 sessions × ~75 bars/day
  ≈ 2.9M bars, 36.7MB.
- **Audited vs NSE bhavcopy**: open/high/low 100% match (±₹0.05), volume
  97.6% (±0.5%; rare NMDC-type days differ ~2-13% — closing-auction/block
  prints), candle-close vs official close median +0.11% (closing auction —
  expected; use candle close for intraday, bhavcopy close for EOD).
- Modes: `python3 candles.py backfill` (resume-safe) · `daily` (refresh
  current month after close) · `status`.
- Symbol→ISIN from NSE EQUITY_L.csv; **LTIM→LTM rename (Feb 2026) stitched**
  in cash/fut/opt stores before panel rebuild.

## Stage 3 — Morning Reveal layer (validated Apr–Sep 2026 OOS picks)

**The 9:45 protocol:** evening Top-10 → at 9:45, LONG the picks trading above
their 9:15–9:30 opening-range high; stop at OR low; exit 15:10.

- **69.9% win, +0.95% avg net (median +1.03%)** over 186 trades, costs
  deducted; H1 67.8% / H2 71.9% (stable); MFE/MAE 2:1
- Same-day control: ALL stocks with the same OR-up breakout → only 45.2% up,
  −0.10% avg. The edge is the *interaction* (predicted mover × confirmed
  direction), not market beta
- Morning direction **overrides** evening basis prior (a SHORT ★★ that breaks
  UP goes up 58.8% — don't short it)
- Gap ≥ 0 filter improves to 72.2% / +1.11%; first-30-min volume adds nothing
- 81% of days have ≥1 signal (median 2); cap risk per day, not per trade
- All fade variants lost (gap-up fade −0.50%); follow > fade at every horizon
- Details: `output/morning_report.md` · builder: `morning.py`

## Daily operation (after ~6:30 PM IST on a trading day)

```bash
cd engine
python3 scanner.py            # refresh EOD data -> rebuild -> score -> watchlist
python3 candles.py daily       # refresh 5-min candles for current month
```

Outputs: `output/watchlist_YYYY-MM-DD.{csv,md}` — Top-10 with model
probability, activity multiples, and fired fingerprints.

## One-off / maintenance

```bash
python3 backfill.py           # fill any missing days (resume-safe)
python3 features.py           # rebuild feature panel only
python3 validate.py           # re-run walk-forward validation + IC scan
python3 scanner.py --no-refresh  # re-score without downloading
```

## Architecture

```
harvest.py   one day: cash bhavcopy+delivery, FO UDiFF (full option chain),
             participant OI -> compact frames (holiday & soft-error safe)
backfill.py  date loop, checkpoints, VIX fetch (Yahoo ^INDIAVIX)
features.py  ~60 features per stock-day, strict no-lookahead
             (ratios vs PRIOR-20d means; labels from t+1 only)
validate.py  daily percentile ranks -> HistGradientBoosting, monthly
             walk-forward retrain, IC scan, naive baselines
scanner.py   production: refresh -> retrain on all 2026 -> score -> Top-10
```

Data quirks handled: NSE re-serves stale bhavcopies on holidays (date
verified inside file); padded values (` EQ`); participant file 1–2 day
publish lag (forward-filled); FO zip parsed in-memory.

## Label definition

`y_move = 1` if next-day gap-adjusted true range % lands in the top quintile
of that day's F&O universe (base rate 20.2%). `tr_expand` = next-day TR ÷
own ATR14. Universe = stocks with active stock-futures that session (~210).

## Honest limits (unchanged from original research)

- Movement ≠ direction; ~48% of picks still fizzle.
- 2026-only training (3-month initial window) is thinner than the original
  500-session panel — expect hit rate to firm up as sessions accumulate.
- No costs/slippage modeled. Mild survivorship (F&O universe as-of-today).
- Warmup note: harvest starts 2025-11-17 purely to seed rolling indicators;
  no training/validation rows exist before 2026-01-01. Set
  `HARVEST_FROM = "2026-01-01"` in config.py for a zero-2025-bytes build
  (cost: first ~25 sessions of January lose their rolling features).

*Research tool. Not investment advice.*

## Stage 4 (2025→2026 full backtest) — DONE
`backtest_full.py` + `output/backtest_2025_2026.md`: 18 OOS months (2025-04→2026-09).
LONG 601 trades 59.1% win +0.49% avg; SHORT 702 trades 58.5% win +0.32% avg — both sides
profitable; shorts better in 2025, longs better in 2026 → Apr–Sep 2026 window was regime bias.
VIX tilt (high-VIX→longs, low-VIX→shorts) is the only tradeable regime split; candidate for
forward test. Worst equal-weight day −21.39% (2025-12-09, 6 correlated longs). Audits: leakage
0.00e+00, manual verification 3/3, walk-forward isolation re-verified.
NOTE: 2025 raw 5-min bars pruned post-analysis for the 128MB cap; re-fetch with
`python3 candles.py window 2025-01-01 2025-12-31` if needed.

## Stage 5 (Sniper Protocol v2) — DONE
`protocol_v2.py` + `output/protocol_v2.md`: per side, >3 confirmed OR candidates -> top 2 by
calmest 20-day range (range20), else take all; hard disaster SL 1.0% from 9:45 entry (5-min
path sim, conservative fills). 986 trades / 346 mornings / 2.85 per day; +0.42%/trade,
+1.21%/day, +417pp; worst trade -1.20%, worst day -4.80% (was -11.07/-15.25); one losing
month in 18. Ranker + SL chosen on 2025, validated 2026. Trade log:
`output/v2_book_trades.csv.gz`. 2025 bars for SL sims live in /tmp (re-fetch:
`python3 candles.py window 2025-01-01 2025-12-31`).

## Stage 6 (Robustness battery) — DONE
`output/robustness_v2.md`: entry latency (5m late -4% of edge, 10m -19%), exit band flat
14:45-15:25 (close 15:10, avoid auto square-off), 20k/4x/1-2-3% risk compounding (1% ->
~5.5L, DD -10%; 3% leverage-fiction), 10k-path day-block MC (realized=median, P(loss)=0,
half-edge stress), random-watchlist control -0.14%/trade vs +0.38% real (11.4 sigma).
Rig self-check caught stop-walk-past-exit bug (fixed, 3.8e-06 reproduction).

## MASTER REPORT — KAVACH-945 (system name, adopted 2026-09-10)
`output/KAVACH-945_report.html`: the single self-contained rebuild document — data layer
(sources/URLs/schemas/API rules), all 6 layers (selection -> basis prior -> 9:45 reveal ->
regime test -> KAVACH armor -> robustness), every audit, bug log, rebuild guide with
verification checkpoints, execution playbook, paper-trading gate spec, full monthly tables,
glossary, constants card. If everything else is lost, rebuild from this file + §11.
