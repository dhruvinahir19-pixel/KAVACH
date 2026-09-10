# Methodology & Data Audit — exactly what was done, and what was NOT

*Written to be audited. If you find something here that wasn't done properly,
that's a finding — bring it and we'll fix it.*

## 1. Answer to "did you see next-day prices of picked stocks?"

**Three separate contexts, three honest answers:**

| Context | Did future data touch it? |
|---|---|
| **Training the model** | YES — by design. The model learns from historical days whose next-day outcome is known. That's what training is. Every feature at day T uses ONLY data up to T's close (see §3 mechanics). |
| **Scoring tonight's watchlist** | NO — impossible. The scored session (e.g., Sep 9) has no label row in the panel; the model retrains on labelled sessions only. Sep 10 data did not exist when the list was made. |
| **Reported hit rates (51.6% etc.)** | NO — walk-forward: each month scored by a model trained only on prior months. |

**Machine-verified (not just claimed):** scoring 2026-06-30 with the full panel
vs. a panel with all future rows deleted produces **identical probabilities to
0.00e+00** across all 211 stocks. Run it yourself — the audit snippet is in
this file's git history / chat log.

**One disclosure you should know (direction layer):** the *discovery* of the
futures-discount signal happened on the pooled Apr–Sep picks (in-sample
selection). The H1/H2 split check then confirmed it out-of-sample (H2-only:
n=65, 66.2% down; H1: n=72, 66.7%). The ±1.5 z-threshold was set a priori,
not tuned. Treat the pooled 66.4% as "internally validated"; the true test is
forward paper-trading (n is still small).

## 2. Data actually used (4 sources, all EOD, all public)

| # | File (per trading day) | Fields used | What it IS |
|---|---|---|---|
| 1 | NSE `sec_bhavdata_full` (cash bhavcopy w/ delivery) | OHLC, prev close, volume, turnover, trade count, delivery qty/% per stock | **This IS the candle data** — official exchange daily candles, straight from NSE, plus delivery (which vendors don't give) |
| 2 | NSE FO UDiFF bhavcopy (zip) | Every futures contract (OI, ΔOI, volume, near-month close → basis) + every option contract (OI, ΔOI per strike → PCR, top-3 concentration, call build) + Nifty/BankNifty spot | Full reconstructed option chain, EOD |
| 3 | NSE participant-wise OI | Client/FII/DII/Pro net long-short in index & stock futures — **MARKET-LEVEL TOTALS ONLY** (see §4) | Positioning by participant class |
| 4 | Yahoo Finance `^INDIAVIX` | India VIX daily close | Fear gauge |

- No vendor data, no paid feeds. 202 sessions harvested (2025-11-17 → 2026-09-09), ~5MB compressed.
- The Nov–Dec 2025 rows exist ONLY to seed rolling windows (ATR, 60d averages); training starts 2026-01-01.

## 3. Process, step by step

1. **Harvest** (harvest.py): per day → parse in memory → compact per-stock
   aggregates. Holiday guard (NSE re-serves stale files — date verified inside
   the file). Raw files never stored.
2. **Features** (features.py): ~60 variables per stock-day.
   No-lookahead mechanics:
   - every rolling stat uses windows ENDING at T (e.g., `prior20H` = max of
     highs T−20..T−1, i.e., `shift(1)` before rolling)
   - every "vs 20d avg" ratio divides today by the mean of the PRIOR 20
     sessions (today excluded, so a surge can't dilute itself)
   - market features merged by date (same-day), participant/VIX gaps
     forward-filled (last KNOWN value, never backward)
3. **Labels** (the only place T+1 enters, for training only):
   - `y_move` = next-day gap-adjusted true range in top quintile of that day's
     universe (base 20.2%)
   - direction labels: next-day close/close sign, open/close body, MFE/MAE
4. **Validation** (validate.py): features percentile-ranked per day →
   HistGradientBoosting → monthly retrain, score next month OOS → AUC, top-10
   hit rate, capture. Baselines (vol-surge, range) for comparison.
5. **Production** (scanner.py): refresh → retrain on ALL labelled sessions →
   score the newest (unlabelled) session → Top-10 + fingerprints.
6. **Direction** (direction.py): a priori trap score tested → rejected →
   basis-tilt signal validated H1/H2 (see §1 disclosure).

## 4. What was NOT used / known limitations (the improvement backlog)

1. **NO intraday data.** No 1-min/5-min candles, no opening range, no
   intraday trap confirmation. The "Morning Reveal Protocol" from the
   framework doc is manual, not coded. Free historical intraday for NSE is
   genuinely hard (broker APIs need auth; Yahoo intraday covers only ~60 days).
   → Candidate: start capturing forward intraday data daily from now.
2. **Participant OI is market-level.** NSE does not publish per-stock
   participant splits. So "who is trapped in THIS stock" is inferred via
   per-stock futures basis/OI — not direct. (Original engine had the same
   constraint.)
3. **y_move includes the overnight gap.** An intraday trader can't capture
   gap movement. `next_body` (open→close) exists in the panel but the primary
   label counts gap. → Candidate: re-validate with open-to-close label.
4. **No event calendar** — results/board meetings/ASM-GSM/ex-dates not
   filtered. → Candidate: NSE corporate-actions feed + ASM/GSM list scrape.
5. **No costs/slippage in any reported number.** Round trip ≈ 0.13–0.18%.
   The SHORT edge (−0.76% avg) survives; NEUTRAL (+0.45%) is marginal.
6. **MFE/MAE come from daily high/low** — not a real execution backtest (no
   entry/stop/target simulation).
7. **Single regime.** 2026 only, VIX ~9–16. A 20+ VIX crash is untested.
8. **Universe = stocks with futures that day** (from that day's FO file) —
   no survivorship bias from universe selection; stocks never in F&O excluded
   by design.
9. **Feature-family selection inherited from the original research**, which
   itself was validated on data overlapping 2026. The 2026 walk-forward is
   clean for model WEIGHTS, but the family of signals isn't discovery-blind.
   Forward paper-trading is the only cure.
10. **Yahoo VIX = single point of failure** (no fallback source coded).

## 5. Verification log (what was tested when)

| Check | Result |
|---|---|
| Label distribution sanity | base rate 20.2%, mean next TR 2.91% ✓ |
| Bug hunt: label mis-alignment | found (index trap after re-sort), fixed, re-verified by hand ✓ |
| Walk-forward OOS | 51.6% mean hit rate, 6 months ✓ |
| Leakage audit (future-deleted panel) | 0.00e+00 prediction difference ✓ |
| Direction H1/H2 split | discount signal stable both halves ✓ |
| Trap framework a priori score | REJECTED (sign flips) — documented, not hidden ✗→✓ |

*Next verification due: forward paper-trading log (40–60 sessions) for both
layers — this is the only true out-of-sample test that remains.*

## Stage-4 audit notes (2026-09-10)
- pandas trap: `-bool_series` is LOGICAL NOT (~), not arithmetic negation. An ad-hoc query using `.where(orb_up, -orb_dn)` produced fake longs; backtest_full.py uses boolean masks and was unaffected. Never negate a bool column arithmetically.
- Leakage test protocol: score date T with full panel vs panel truncated at T; require 0.00e+00 max diff (passed 2025-08-29, 209 stocks).
- Workspace cap: 2025 raw candles pruned after analysis (re-fetchable, command in README); morning.csv.gz is the preserved record.
