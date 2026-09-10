# STAGE 5 — Sniper Protocol v2: 1–2 Best Stocks a Day + Disaster Stop-Loss

**The ask:** *"We can't risk our capital — disaster days can wipe out multiple days or whole months of profit. I want to trade only 1–2 best stocks a day with a disaster safety SL. We want quantity, so don't add filters that reduce trades; 1–2 trades a day, no gaps between trade days. We need to go one step deeper in stock selection. Only filter if there are more than 3 stocks picked for breakout or breakdown."*

**One-line answer:** Done, and the data blessed the design: capping each side at the 2 *calmest* confirmed candidates when >3 fire, plus a hard **1.0% disaster stop**, cut the worst day from **−15.25% to −4.80%** and the worst trade from **−11.07% to −1.20%** — while the average per trade actually *improved* (+0.38% → +0.42%) and 97.5% of mornings still trade.

---

## 1. The rule (executable spec: `engine/protocol_v2.py`)

At 9:45, among the evening Top-10 picks:
1. **LONG side:** picks with price > OR15 high. **SHORT side:** picks with price < OR15 low.
2. **Per side:** if ≤3 candidates → trade them all. If >3 → trade the **top 2 by calmest 20-day range** (`range20 = (prior20H − prior20L) / price`, known from EOD data the prior evening).
3. **Disaster SL:** bracket stop at **1.0% adverse** from the 9:45 entry, on every trade, no exceptions.
4. Exit 15:10 if not stopped. **No forced trades** on mornings with zero OR confirmations.

## 2. Why "calmest 20-day range" is the ranker (the deeper selection)

We tested every signal legitimately available at 9:45 — breakout margin, OR width, aligned gap, model probability, basis z-score, first-45-min relative volume, 20-day-range freshness, stop distance, 20-day range. Honest finding first: **within a day, no feature reliably separates "best" from "rest"** — top-ranked candidates *underperformed* the rest in 2025 and overperformed in 2026. The per-trade edge is roughly homogeneous across confirmed candidates; **the cap itself is the protection, so the ranker should be chosen for risk, not alpha.**

On the >3-candidate days where the rule actually applies (101 side-days, 519 candidates), ranked top-2 vs all-candidates baseline:

| ranker | 2025 win / avg / worst | 2026 win / avg / worst |
|---|---|---|
| **range20 (calmest)** | **76.0% / +1.22% / −5.20%** | **55.1% / +0.48% / −5.64%** |
| model prob | 66.3% / +1.08% / −6.89% | 58.2% / +0.70% / −9.71% |
| tightest stop distance | 74.0% / +1.01% / −5.25% | 49.0% / +0.39% / −9.71% |
| all candidates (no rank) | ~57% / +0.87% / −6.89% | ~55% / +0.36% / −9.71% |

`range20` is the only ranker that beats the baseline in **both** periods *and* reduces the worst trade in both. Story: among confirmed breakouts, the quietest name makes the cleanest trade; the wild ones (ADANIGREEN-type ranges) are where −10% days live.

## 3. Quantity check (your explicit constraint)

- **346 of 355 mornings trade (97.5%)**; mean 2.85 trades/day, median 3, max 6.
- Top-2 cap beat top-1 cap (avg +0.38% vs +0.33%; 2025: +0.31% vs +0.20%) → keep 2.
- **The 9 no-signal mornings must stay flat.** We tested forcing the top-prob pick without OR confirmation: **33% win, −2.09% avg** (includes −11.1% and −9.8% trades). The confirmation IS the edge — forcing trades to fill the calendar is precisely how capital gets wiped. 2.5% of mornings sitting out is the cost of the protocol working.

## 4. Disaster SL — the grid (chosen on 2025, validated on 2026)

Simulated on real 5-min paths of all 986 trades, conservative fills (gap-through stops fill at the open), +0.05% slippage on stop-outs:

| stop | 2025 total | 2026 total | worst trade | worst day | stopped |
|---|---|---|---|---|---|
| none | +159pp | +212pp | −11.07% | −15.25% | — |
| −0.50% | +130pp | +99pp | −0.70% | −3.50% | 67% |
| −0.75% | +179pp | +165pp | −0.95% | −3.80% | 55% |
| **−1.00%** | **+193pp** | **+225pp** | **−1.20%** | **−4.80%** | 46% |
| −1.25% | +181pp | +237pp | −1.45% | −5.80% | 40% |
| −1.50% | +172pp | +235pp | −1.70% | −5.62% | 35% |
| OR other side | +201pp | +245pp | −6.94% | −8.04% | 19% |
| OR mid | +183pp | +254pp | −5.60% | −8.66% | 33% |

**−1.00% is the choice:** it raises total P&L in *both* independent periods (+193 vs +159; +225 vs +212) — the excursions it cuts are disproportionately the ones that keep going — while bounding every trade at −1.2%. Structural stops (OR-based) win on total but leave −6.9% trades on the table: they fail the disaster duty because a stock can run far from its OR before 9:45. Tighter stops (−0.5%) bleed to whipsaw.

**Honest cost:** 46% of trades now end as stop-outs and per-trade win rate drops to ~48%. That is the psychological price of an amputated left tail. The average trade is still +0.42% because the winners are untouched.

## 5. Final v2 performance (18 OOS months, net)

- 986 trades (493 long / 493 short) | 346 mornings | 2.85 trades/day
- **avg +0.42%/trade | +1.21%/day | 58% win-days | +417pp total**
- 2025: +0.38%/trade, worst day −4.80% | 2026: +0.47%/trade, worst day −4.80% — stable
- **Worst trade −1.20%. Worst day −4.80%** (= 4 stops × −1.2%; every disaster day is now all-stopped). Best day +17.55%.
- **One losing month in 18** (2025-05, ~breakeven). Monthly long/short table in §6.
- Losing streaks: 7 (long) / 9 (short) — expect them.
- Long +0.53%/trade vs short +0.32%/trade — both sides carry their weight; VIX tilt from Stage 4 remains an observation for forward testing.

**vs the uncapped Stage-4 book:** total/day +1.21% vs +1.50% — you give up ~0.3%/day of expected P&L, and in exchange the worst day drops from −21.39% (all-trades) / −15.25% (capped, no SL) to **−4.80%**. Risk-adjusted (avg day ÷ worst day): 0.070 → **0.252, a 3.6× improvement**. A 2025-12-09-style morning now costs four stops, not a month of profit.

## 6. Monthly detail (net of costs, with SL)

| month | L n | L win | L avg | S n | S win | S avg |
|---|---|---|---|---|---|---|
| 2025-04 | 22 | 72.7% | +1.50% | 19 | 52.6% | +0.80% |
| 2025-05 | 27 | 44.4% | +0.05% | 32 | 40.6% | −0.07% |
| 2025-06 | 29 | 48.3% | +0.60% | 25 | 44.0% | +0.13% |
| 2025-07 | 38 | 39.5% | +0.13% | 36 | 47.2% | +0.14% |
| 2025-08 | 21 | 28.6% | −0.26% | 34 | 38.2% | +0.53% |
| 2025-09 | 37 | 64.9% | +1.04% | 19 | 47.4% | +0.18% |
| 2025-10 | 32 | 46.9% | +0.13% | 30 | 40.0% | −0.01% |
| 2025-11 | 27 | 55.6% | +0.82% | 26 | 69.2% | +0.62% |
| 2025-12 | 28 | 46.4% | +0.35% | 30 | 50.0% | +0.28% |
| 2026-01 | 26 | 46.2% | +0.71% | 32 | 56.2% | +1.49% |
| 2026-02 | 19 | 42.1% | +0.27% | 38 | 52.6% | +0.26% |
| 2026-03 | 22 | 27.3% | +0.01% | 19 | 47.4% | +0.35% |
| 2026-04 | 30 | 50.0% | +0.76% | 19 | 52.6% | +0.23% |
| 2026-05 | 24 | 33.3% | +0.27% | 27 | 40.7% | +0.42% |
| 2026-06 | 32 | 56.2% | +0.72% | 31 | 29.0% | −0.24% |
| 2026-07 | 36 | 61.1% | +1.24% | 31 | 48.4% | +0.35% |
| 2026-08 | 31 | 48.4% | +0.38% | 35 | 45.7% | +0.20% |
| 2026-09 | 12 | 58.3% | +0.18% | 10 | 50.0% | −0.03% |

Win rates look lower than Stage 4 because stop-outs count as losses; the money is the same or better.

## 7. Integrity

- **No same-window fitting:** ranker and SL level chosen on 2025-04→12, validated on 2026-01→09, then reported on the full window.
- **SL simulation:** all 986 trade paths resolved from raw 5-min bars (2026 from workspace store, 2025 re-fetched fresh from Upstox to `/tmp` — outside the 128MB workspace); **0 missing, 0 close mismatches** vs the morning table; the no-stop case reproduces the Stage-4 book exactly (rig self-verification).
- Two rig bugs were caught and fixed during the audit (a fall-through that mislabeled the no-stop baseline; a path window off-by-one vs `morning.py`'s 15:10 convention — the exit is the last bar starting ≤15:10, which on ~30% of days is the 15:10–15:15 bar). Both are documented in METHODOLOGY.md; final numbers are from the fixed rig.
- Caveat that matters: the >3 threshold, top-2 count, range20 ranker and −1% level were all selected on this 18-month sample. The split-period validation protects against overfitting but is not proof. **Forward paper-trading is the gate.**

## 8. Live execution card (for paper trading)

**Evening:** run the stack → Top-10 watchlist with basis tilt; compute `range20` per pick.
**9:45:** list confirmed candidates per side (price vs OR15 high/low).
- ≤3 on a side → take all. >3 → take the 2 smallest `range20`.
**On fill:** bracket SL at 1.0% adverse immediately (part of the order — no discretion).
**15:10:** exit whatever remains. No OR confirmation → no trade, no exceptions.

Trade log: `output/v2_book_trades.csv.gz` (all 986 backtest trades with nets — the baseline the paper log must be compared against).
