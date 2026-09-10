# STAGE 6 — Robustness Battery: Entry Latency, Exit Time, Small-Account Compounding, Monte Carlo

**The ask:** *"What if we enter 5 minutes late? What if I close at 15:10 (broker auto square-off charges)? What is my final capital starting with 20k at 4× leverage risking 1-2-3% per trade? And run Monte Carlo so it's all verified — no bug, no error, no logic mismatch, no look-ahead bias."*

**One-line answers:** 5-min late entry costs ~4% of the edge (tolerable; 10 min costs ~19% — act at 9:45). Closing at 15:10 gives up essentially nothing vs holding to the close and avoids auto square-off charges — the exit minute is not where the edge lives. On 20k at 4×: risk-1% compounds to ≈ ₹5.5L (backtest assumptions), risk-2% ≈ ₹30L, risk-3% ≈ ₹39L but is leverage-fiction. Monte Carlo (10,000 day-block paths) confirms the geometry — and the random-watchlist control (11.4σ separation) confirms the edge is real selection, not an accounting bug.

All tests re-simulated from raw 5-min bars (2025 re-fetched to /tmp, 2026 from store), same 986-trade v2 book, stops walked bar-by-bar with conservative fills.

---

## 1. Entry latency (fill 5 / 10 minutes after the 9:45 signal)

| config | avg/trade | total | worst trade | worst day | drift paid |
|---|---|---|---|---|---|
| **9:45 entry (protocol)** | +0.42% | +417pp | −1.20% | −4.80% | — |
| **9:50 entry (5 min late)** | +0.41% | +401pp | −1.20% | −4.80% | +0.04% |
| **9:55 entry (10 min late)** | +0.34% | +339pp | −1.20% | −4.80% | +0.07% |

- Waiting costs you the momentum drift (+0.04% in 5 min, +0.07% in 10): 5 minutes late = −3.8% of total edge; 10 minutes = −18.7%.
- **Verdict:** 5-min latency is survivable (execution lag, slow fingers, order queue), but the protocol should place orders AT 9:45 — the confirmation and the order belong in the same minute.
- The disaster SL geometry is latency-proof: worst trade −1.20% and worst day −4.80% under every latency.

## 2. Exit time (incl. the auto square-off question)

| exit | avg/trade | total | stops |
|---|---|---|---|
| 14:45 | +0.40% | +397pp | 447 |
| 15:00 | +0.42% | +417pp | 451 |
| **15:10 (protocol)** | **+0.42%** | **+417pp** | 453 |
| 15:20 | +0.44% | +437pp | 454 |
| 15:25 (near close) | +0.44% | +435pp | 456 |

- **Yes — close at 15:10 yourself.** The protocol already does. Holding to 15:20/15:25 would add only ~+20pp over 18 months (~0.02%/trade — noise), so you give up nothing material by exiting before the broker's auto square-off window and its charges (typically ₹50–100+ per forced square-off).
- The edge is flat across a 40-minute exit band (14:45→15:25: +397 to +437pp). **Exit timing is not a fragile parameter** — the money is made by selection + the 9:45 confirmation, not by the exit minute.

## 3. The 20k account at 4× leverage, risking 1–2–3% per trade

Sizing: true risk per trade (a stopped trade loses exactly R of capital, stop+costs = 1.2% of notional) → notional = R×C/0.012, proportional within a morning, capped by 4× buying power. Full reinvestment, costs as modeled (0.15% + stop slippage).

| risk | notional/trade | **final capital** | max DD (realized) | days leverage-capped | avg exposure |
|---|---|---|---|---|---|
| **1%** | 0.83× C | **₹548,675** (27.4×) | −9.8% | 24/346 | 2.36× |
| **2%** | 1.67× C | **₹2,976,185** (148.8×) | −16.9% | 201/346 | 3.60× |
| **3%** | 2.50× C | **₹3,910,550** (195.5×) | −20.5% | 320/346 | 3.89× |

Read this correctly:
- **3% risk is leverage-fiction:** on 320 of 346 mornings the 4× cap forces downsizing (avg exposure 3.89× leaves zero buffer — and brokers cut intraday leverage on volatile names precisely on days like ours). "3%" only fully applies on single-trade days; that's why it beats 2% by far less than 1.5×.
- **Flat-fee broker penalty is huge at 20k:** with ₹20/order-style brokerage (~+0.10%/trade extra on ~₹17–50k notionals): 1% → ₹244k, 2% → ₹871k, 3% → ₹1.04M. **At a 20k account, choose %–based (or zero) intraday brokerage, not flat-fee — it roughly halves the outcome.**
- Taxes not modeled (intraday = business income at slab); no withdrawals; full compounding.
- These are backtest-edge extrapolations, not promises — see §5.

## 4. Monte Carlo — 10,000 day-block bootstrap paths

Day-level resampling (preserves same-day correlation — the all-stops-hit disaster mornings stay together; trade-level iid bootstrap would understate drawdowns).

| risk | realized | MC median | p5 | p25 | p75 | p95 | P(final < 20k) | median DD | p95 DD |
|---|---|---|---|---|---|---|---|---|---|
| 1% | ₹548,675 | ₹544,787 | ₹231,495 | ₹383,682 | ₹789,187 | ₹1,319,795 | 0.0% | −12.7% | −19.5% |
| 2% | ₹2,976,185 | ₹2,876,524 | ₹789,007 | ₹1,693,138 | ₹5,040,037 | ₹11,712,191 | 0.0% | −19.3% | −29.3% |
| 3% | ₹3,910,550 | ₹3,837,718 | ₹868,341 | ₹2,065,568 | ₹7,142,634 | ₹17,176,034 | 0.0% | −21.5% | −32.6% |

- **Sanity:** realized ≈ MC median at every risk level (the actual path is a typical path — no cherry-picked sequence).
- **Half-edge stress** (every trade's net × 0.5, i.e., assume the live edge decays 50%): 1% risk → median ₹108k; 2% → ₹268k; 3% → ₹312k; P(<20k) still 0%. The geometry survives edge decay; it does not survive edge *death*.
- **What MC cannot do — stated plainly:** bootstrap resamples *observed history*. It proves the strategy's geometry (sizing, stops, correlation, sequence risk) is sound and bug-free arithmetic. It **cannot** prove the edge persists forward — regime change, crowding, live slippage and impact are outside its vocabulary. That is what forward paper-trading is for. P(loss)=0% in MC is a statement about 18 months of resampled history, not about the future.

## 5. Verification battery (the "no bug / no error / no logic mismatch / no look-ahead" demand)

| check | result |
|---|---|
| Look-ahead leakage | Scored 2025-08-29 with full panel vs panel physically truncated at that date: **max probability difference 0.00e+00** across 209 stocks (Stage-4 audit, re-confirmed) |
| Selection integrity | **Random-watchlist control:** 60 random 10-stock watchlists/day through the identical pipeline (OR rule, >3→top-2 range20, costs): **−0.142% ± 0.046%/trade vs real book +0.376% → 11.4σ separation.** No sign error, cost omission, or accounting bug could produce this gap — a bug lifts the control too; only real selection-edge separates them |
| Same-day control | All OR-up stocks 46.4% / −0.10% vs picks 61.9% / +0.60% (Stage 4, 18 months) |
| Rig self-verification | The timing rig recomputes all 986 archived nets from raw bars: max diff 3.8e-06 (1 trade, sub-paise) |
| Rig catches its own bugs | First pass flagged 3.3e-02 divergence → root cause: my stop-walk was running past 15:10 into 15:25 bars. Fixed (stop-walk bounded by exit time), re-verified. The check worked as designed |
| SL geometry invariance | Worst trade −1.20%, worst day −4.80% identical across ALL 7 timing configurations — the stop does its job under every entry/exit variant |
| MC sanity | Realized path ≈ MC median at all risk levels; no anomalous outliers |
| Costs | 0.15% round trip + 0.05% stop slippage on every trade, every table; flat-fee broker scenario quantified separately |

## 6. Verdict & recommendation for the 20k account

1. **Execute at 9:45, not 9:50** — latency is a small but real leak (−4% at 5 min, −19% at 10 min).
2. **Exit at 15:10 manually** — nothing given up vs holding to the close, auto square-off charges avoided, and the edge is indifferent across the 14:45–15:25 band anyway.
3. **Risk 1% per trade** (₹200 initial risk, notional ≈ ₹16.7k ≈ 0.83× capital): expected max drawdowns of −10 to −13% (p95 −19.5%), leverage-capped only 24 days, and the widest safety margin to the 4× ceiling. 2% is the aggressive ceiling; 3% is not actually achievable under 4× leverage.
4. **Broker matters at this size:** %-based intraday brokerage, not ₹20/order flat fee.
5. The Monte Carlo verifies the machine. Only forward paper-trading verifies the engine — the logger proposal stands as the next gate.
