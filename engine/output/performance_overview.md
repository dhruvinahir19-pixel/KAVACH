# Complete Performance Overview — Morning Reveal Protocol
## OOS window: 110 mornings (Apr 1 – Sep 8, 2026) · 10 picks/evening · costs 0.15% rt deducted

---

## Q1: The mechanics (confirmation)

```
Evening ~6:30 PM          → scanner Top-10 (all 10 marked)
Next morning 9:15–9:30    → mark OR15 high/low for each of the 10
9:45 AM                   → check each of the 10:
                             price > OR15 high  → LONG  (core signal)
                             price < OR15 low   → SHORT (optional, half size)
                             neither            → no trade in that stock
Stop = opposite side of OR15 · Exit = 15:10
```

Typical day: 1–2 longs + 1–2 shorts qualify out of the 10. You trade ONLY
the qualifying subset. No qualification = no trade (that's a feature, not a
miss — ~19% of mornings have zero long signals).

---

## Q2: LONG vs SHORT — the full 2026 OOS record

### LONG (OR-up breakout) — the core signal

| Month | Trades | Win % | Avg net | Sum (pp) |
|---|---|---|---|---|
| Apr 2026 | 31 | 77.4% | +1.11% | +34.3 |
| May 2026 | 24 | 66.7% | +1.14% | +27.3 |
| Jun 2026 | 35 | 60.0% | +0.49% | +17.1 |
| Jul 2026 | 41 | 78.0% | +1.74% | +71.1 |
| Aug 2026 | 41 | 65.9% | +0.50% | +20.4 |
| Sep 2026 | 14 | 71.4% | +0.46% | +6.5 |
| **TOTAL** | **186** | **69.9%** | **+0.95%** | **+176.7** |

- **Every single month profitable.** Worst trade −5.64%, best +10.41%
- Max losing streak: 5 trades · max cumulative drawdown: −15.3pp (per-trade curve)
- Median trade +1.03% (right tail is fat, left tail controlled)

### SHORT (OR-down breakout) — optional, weaker

| Month | Trades | Win % | Avg net | Sum (pp) |
|---|---|---|---|---|
| Apr 2026 | 29 | 62.1% | +0.04% | +1.2 |
| May 2026 | 35 | 57.1% | +0.47% | +16.4 |
| Jun 2026 | 42 | 45.2% | −0.21% | −8.8 |
| Jul 2026 | 48 | 54.2% | +0.17% | +8.2 |
| Aug 2026 | 39 | 64.1% | +0.41% | +16.1 |
| Sep 2026 | 12 | 75.0% | +0.31% | +3.7 |
| **TOTAL** | **205** | **57.1%** | **+0.18%** | **+36.8** |

- Positive in aggregate but thin; one negative month (June); max losing
  streak 7; worst trade −9.48% (squeeze); drawdown −20.5pp
- **Verdict: half-size optional, or skip entirely for simplicity.** The
  engine's value concentrates on the long side.

### Combined (long full + short half) — per-day experience

- 106 of 110 mornings had at least one trade
- Avg per traded day: **+1.84%** (sum across concurrent trades, 1×long + 0.5×short)
- 68% of days positive · best day +14.1% · worst day −7.8%
- Total: +195.1pp across 391 trades
- ⚠ Concurrent signals correlate (same market tape) — risk must be capped
  per DAY, not per trade

*Note: "pp" = percentage points of notional per trade, summed — not a
compounded portfolio return. Translate to your position sizing.*

*Jan–Mar are not in these tables: the walk-forward OOS file begins April
(first month scored by a model trained only on prior months). Sep 9+ not yet
resolvable (future hasn't happened).*

---

## Q3: What happened to the futures-discount finding (66.4% DOWN)?

**It's still alive — but in its own time window, which turns out to be mostly
OVERNIGHT.** Decomposition on the 145 discount picks in this window:

| Component | Value |
|---|---|
| Next-day close-to-close (the original finding's window) | **62–66% down, avg ≈ −0.8 to −1.1%** |
| — of which overnight gap (close → 9:15 open) | avg **−0.45%** |
| — of which 9:45→15:10 (what an intraday short can capture) | avg ≈ **−0.25%** |

**Interpretation:** roughly half the discount edge is paid out in the gap
before you could ever enter at 9:45. The market "knows" the weakness
overnight; by 9:45 the remaining intraday drift is thin. That's why:
- R1 (discount prior + morning confirms down) = only +0.10% avg net
- The 9:45 tape OVERRIDES the prior: a discount pick that breaks UP by 9:45
  still goes up ~59% of the time

**So the discount signal is NOT dead — it changed jobs:**
1. **Positional/overnight lens** — for a hold-overnight style (different risk
   framework, gap risk both ways), the close→close edge stands on its own
2. **Evening ranking input** — marks which picks carry leveraged-short
   pressure into tomorrow (context for the morning decision)
3. **NOT an intraday filter** — skip-discount filtering of OR-up longs does
   nothing (69.4% vs 69.9%)

**One surprise from the filter test:** it's the PREMIUM picks (z > +1.5, the
"obviously bullish" ones) that make the WEAKEST OR-up longs — 53.8% win,
+0.15% avg (n=26). Discount picks breaking up: 75.0% (n=16). Neutral: 72.2%.
Small samples — treat as a watch-item, not a rule: the crowded-bull setup may
be where morning breakouts fail most.

---

## Q4: The complete stack — who does what

| Layer | Edge | Role in production |
|---|---|---|
| Mover engine (evening) | 51.6% top-quintile hits (2.6× base) | Selects the 10 candidates |
| Basis prior (evening) | 66% down close→close, mostly overnight | Context + positional lens; premium = mild caution on longs |
| **Morning OR confirm (9:45)** | **69.9% win, +0.95% net (long)** | **The execution trigger — decides WHICH picks and WHICH direction** |
| Short OR-down (9:45) | 57.1% win, +0.18% net | Optional half-size |

**Recommended production config (simplest robust version):**
> Long-only OR-up breakouts on the evening Top-10, stop at OR low, exit
> 15:10, cap total day-risk (signals cluster on the same tape).

---

## Risk picture (honest)

- 5-trade losing streak happened; expect ~7+ at some point (short side saw 7)
- Worst single long −5.6%; a gap-through-stop will be worse than backtest
- 17% of days: all concurrent long signals lose together — same-tape correlation
- June (weak month) shows the edge is not uniform: +0.49% avg vs July +1.74%
- All of this is still INTERNAL validation (Apr–Sep OOS picks, H1/H2 stable)
  — forward paper-trading remains the final gate before capital

*Research, not investment advice. Past patterns do not guarantee future results.*
