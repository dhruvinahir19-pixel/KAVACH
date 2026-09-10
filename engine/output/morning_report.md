# Stage 3 Report — The Morning Reveal Layer
## Can we decide by 9:45 AM? (Tested on 1,098 OOS picks + 7,094 movers, 2026)

**Yes. The 9:45 opening-range breakout on an evening-predicted mover is the
strongest intraday signal found in this entire research program.**

## The headline rule (R3-long)

> **Evening:** scanner Top-10. **9:45:** among them, go LONG the stocks whose
> 9:45 price is above their 9:15–9:30 opening-range high. **Stop:** OR low.
> **Exit:** 15:10.

| Sample | n | Win (net) | Avg net | Median | H1 | H2 |
|---|---|---|---|---|---|---|
| OOS picks, Apr–Sep | 186 | **69.9%** | **+0.95%** | +1.03% | 67.8% / +0.87% | 71.9% / +1.02% |
| All next-day movers (control sample) | 1,259 | 64.1% | +0.48% | +0.65% | — | — |

Costs 0.15% round-trip already deducted. MFE/MAE ≈ **+1.1R / −0.5R** (2:1).
Worst trade −5.6%, best +10.4% — fat right tail, controlled left.
Median entry sits just 0.46% above the OR high (the "chase" is small).

## The control that makes it real

Same days, same signal, different universe:

| Universe (same days) | n | rest-of-day up | avg rod |
|---|---|---|---|
| ALL F&O stocks with OR-up breakout by 9:45 | 3,916 | 45.2% | −0.10% |
| **Evening picks with OR-up breakout by 9:45** | 186 | **72.6%** | **+1.10%** |

The morning breakout alone is worth nothing (+0.03% avg on all stock-days).
The evening mover prediction alone is worth little by 9:45 (+0.13% with the
basis prior). **The interaction is the edge**: predicted movement + confirmed
direction = 27 percentage points of separation on identical days.

## Stability & robustness checks

- **H1/H2 split**: 67.8% → 71.9% win rate (both halves strong)
- **Gap filter**: gap ≥ 0 at open → 72.2% win, +1.11% (n=144); gap-down
  breakouts still work (61.9%, +0.42%) — optional quality filter
- **Volume adds nothing**: v45 < 1.5× actually slightly better (75% vs 68%) —
  morning volume spikes burn off (consistent with Phase 1d: high first-30-min
  volume → *less* afternoon movement on movers)
- **Availability**: 81% of days have ≥1 signal; median 2 concurrent; 17% of
  days all concurrent signals lose together → risk caps must be per-DAY
- **Short side**: OR-down short on picks: 57–61% win, +0.18–0.28% — positive
  but thin; optional at reduced size

## What the morning does to the evening priors (2×2)

| Evening prior × morning | n | rod up | avg rod |
|---|---|---|---|
| SHORT ★★ × morning UP | 51 | **58.8%** | +0.35% |
| SHORT ★★ × morning DOWN | 93 | 43.0% | −0.25% |
| LONG-OK × morning UP | 55 | 54.5% | +0.26% |
| neutral × morning UP | 431 | **69.4%** | +0.99% |
| neutral × morning DOWN | 416 | 46.4% | −0.03% |

**The 9:45 tape overrides the evening basis prior.** A SHORT ★★ pick that
breaks UP in the morning goes UP 59% of the time — the morning is the boss.
Practical rule: evening priors rank the *candidates*; the morning *decides*.

## The fade-versus-follow verdict (final)

Every contrarian/fade variant tested in this program lost or was unstable:
- EOD reversal trap score: signs flipped between halves (Stage 2)
- Gap-up fade at 9:45: **−0.50% net** (n=125)
- OR-break fades: OR-up breaks continued 72.6% — fading wins ~27%
- Meanwhile everything that FOLLOWED momentum/positioning held:
  futures-discount continuation (66%), OR-breakout continuation (70%)

The retail crowd loses on these names by *fighting* the move — shorting the
breakout, buying the dip. At 9:45 on a predicted mover, the trap is on the
fader's side, not the chaser's side.

## The production protocol (9:45 AM routine)

1. **Evening (~6:30 PM):** `python3 scanner.py` → Top-10 watchlist
2. **9:15–9:30:** mark each pick's OR15 high/low (any live terminal)
3. **9:45:** LONG picks with price > OR15 high (prefer gap ≥ 0 at open)
4. **Stop:** OR15 low (backtest R stats use this) · **Exit:** 15:10
5. Skip everything else; cap total day-risk (signals cluster)

## Honest limitations

- Discovered on the Apr–Sep OOS pick sample (same sample as Stage 2's
  direction work). H1/H2 stable and movers-universe confirms, but this is
  internal validation — forward paper-trading is mandatory before capital.
- Entry assumed at 9:45 close (median 0.46% above OR high — already in the
  numbers). No partial fills/slippage beyond the 0.15% cost.
- 2026 regime only (VIX 9–16). Options-expiry days not segregated.
- Short side thin (+0.18–0.28%): treat as optional, half-size.

*Research, not investment advice. Backtested with walk-forward picks; past
patterns do not guarantee future results.*
