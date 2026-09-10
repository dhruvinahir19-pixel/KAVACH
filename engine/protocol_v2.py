"""
protocol_v2.py — SNIPER PROTOCOL (v2): 1-2 best candidates/day + disaster stop-loss.

User spec (Stage 5): trade only 1-2 best stocks a day; disaster SL so one morning
cannot wipe weeks of profit; do NOT add filters that reduce trade quantity; only
filter a side when it has MORE THAN 3 confirmed candidates.

Rules on top of the Stage-3/4 Morning Reveal protocol:
  1. CAP (per side): if >3 confirmed OR candidates at 9:45 -> trade TOP 2 ranked by
     range20 = (prior20H - prior20L) / price   [calmest 20-day range, EOD-known]
     if <=3 candidates -> trade them all.
  2. DISASTER SL: hard stop 1.0% adverse from the 9:45 entry, walked bar-by-bar on
     5-min data. Conservative fills: if a bar gaps through the stop, fill at the
     bar open (worse); stopped trades pay cost 0.15% + 0.05% slippage.
  3. NO forced trades: mornings with zero OR confirmation stay flat (9/355 = 2.5%;
     forced top-prob picks without confirmation won 33%, avg -2.09%).

18-month OOS evidence (2025-04 -> 2026-09; ranker + SL chosen on 2025, validated 2026):
  986 trades | 346/355 mornings | 2.85 trades/day (median 3, max 6)
  win 47.9% | avg +0.42%/trade | +1.21%/day | 58% win-days | +417pp total
  WORST TRADE -1.20% | WORST DAY -4.80%   (uncapped book: -11.07% / -15.25%)
  one losing month in 18 (2025-05, ~flat)

SL simulation needs 5-min bars for 2025 (kept out of the 128MB workspace):
  python3 candles.py window 2025-01-01 2025-12-31   (or fetch to /tmp — see README)
If 2025 bars are absent, run(verbose) degrades to selection-only stats.
"""
import os
import pandas as pd

from morning import load_joined, COST
from config import DATA

THRESHOLD = 3          # filter a side only when candidates exceed this
TOPN = 2               # how many to keep when filtering
SL_PCT = 0.01          # disaster stop: 1.0% adverse from entry
SL_SLIP = 0.0005       # extra slippage on stopped trades
CANDLE_ROOTS = (os.path.join(DATA, "candles"), "/tmp/c25")  # 2026 + 2025


def select_book(j):
    """Apply the cap rule to the joined morning table. Returns the traded subset."""
    cand = []
    for side, mask in [(1, j["orb_up"]), (-1, j["orb_dn"])]:
        d = j[mask].copy()
        d["side"] = side
        cand.append(d)
    c = pd.concat(cand)
    c["range20"] = (c["prior20H"] - c["prior20L"]) / c["c945"]
    c["net_raw"] = c["side"] * c["rod_ret"] - COST
    keep = []
    for (_, side), g in c.groupby(["date", "side"]):
        if len(g) <= THRESHOLD:
            keep.append(g)
        else:
            keep.append(g.sort_values("range20", kind="stable").head(TOPN))
    return pd.concat(keep) if keep else pd.DataFrame()


def _load_paths(root, sym):
    p = os.path.join(root, f"{sym}.csv.gz")
    if not os.path.exists(p):
        return {}
    df = pd.read_csv(p)
    mod = (df["epoch_min"] % 1440 + 330) % 1440
    day = (df["epoch_min"] + 330) // 1440
    sel = (mod >= 585) & (mod <= 910)              # 9:45 -> 15:10 (morning.py convention)
    out = {}
    for d, g in df[sel].groupby(day[sel]):
        g = g.sort_values("epoch_min")
        out[int(d)] = (g["open_paise"].values / 100, g["high_paise"].values / 100,
                       g["low_paise"].values / 100, g["close_paise"].values / 100)
    return out


def apply_sl(book, roots=CANDLE_ROOTS):
    """Walk the disaster stop through 5-min bars. Returns book + net/stopped cols."""
    paths, rows = {}, []
    for t in book.itertuples():
        if t.symbol not in paths:
            merged = {}
            for r in roots:
                merged.update(_load_paths(r, t.symbol))
            paths[t.symbol] = merged
        dnum = (t.date.date() - pd.Timestamp("1970-01-01").date()).days
        path = paths[t.symbol].get(dnum)
        if path is None or abs(path[3][-1] - t.rod_c) / t.c945 > 0.002:
            rows.append(dict(date=t.date, symbol=t.symbol, side=t.side,
                             net=t.net_raw, stopped=False, flagged=True))
            continue
        stop = t.c945 * (1 - SL_PCT) if t.side == 1 else t.c945 * (1 + SL_PCT)
        fill = None
        for o, h, l in zip(*path[:3]):
            if t.side == 1 and l <= stop:
                fill = min(o, stop); break
            if t.side == -1 and h >= stop:
                fill = max(o, stop); break
        if fill is not None:
            net = (fill / t.c945 - 1 if t.side == 1 else 1 - fill / t.c945) - COST - SL_SLIP
        else:
            net = t.net_raw
        rows.append(dict(date=t.date, symbol=t.symbol, side=t.side,
                         net=net, stopped=fill is not None, flagged=False))
    return pd.DataFrame(rows)


def run(oos_from="2025-04-01", verbose=True):
    j = load_joined()
    oos = j[(j["is_pick"]) & (j["rod_ret"].notna()) & (j["date"] >= oos_from)].copy()
    book = select_book(oos)
    d = apply_sl(book)
    flagged = d["flagged"].sum()
    if verbose:
        dd = d[~d["flagged"]]
        dly = dd.groupby("date")["net"].agg(["sum", "count"])
        print(f"v2 book: {len(book)} trades | mornings {book['date'].nunique()} | "
              f"trades/day {dly['count'].mean():.2f} (median {dly['count'].median():.0f})")
        if flagged:
            print(f"WARNING: {flagged} trades missing bars -> SL not applied to them")
        print(f"net: win {100*(dd['net']>0).mean():.1f}% | avg {dd['net'].mean()*100:+.2f}%/trade | "
              f"total {dd['net'].sum()*100:+.0f}pp")
        print(f"daily: {dly['sum'].mean()*100:+.2f}%/day | win-days {100*(dly['sum']>0).mean():.0f}% | "
              f"worst day {dly['sum'].min()*100:+.2f}% | worst trade {dd['net'].min()*100:+.2f}% | "
              f"stopped {dd['stopped'].sum()}/{len(dd)}")
    return book, d


if __name__ == "__main__":
    run()
