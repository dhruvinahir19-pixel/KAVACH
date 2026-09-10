"""
morning.py — STAGE 3: the Morning Reveal layer.

Question: by 9:45 AM, using only (a) last evening's priors (mover prob,
basis direction) and (b) the first 30 minutes of tape (gap, opening range,
first-hour volume), can we pick WHICH Top-10 candidates to trade and WHICH
direction — measured on the rest of the day (9:45 -> 15:10)?

Timeline per stock-day (all IST):
  9:15-9:30  opening range (OR15)   — bars 555,560,565
  9:30-9:45  confirm window         — bars 570,575,580 ; decision at 9:45 close
  9:45-15:10 outcome window         — bars 585..910 (exit before square-off)

Costs: 0.15% round trip deducted from all rule returns.
"""
import os
import numpy as np
import pandas as pd
from config import DATA, OUTPUT

CANDLE_DIR = os.path.join(DATA, "candles")
MORNING_F = os.path.join(DATA, "morning.csv.gz")
COST = 0.0015                     # round-trip cost estimate (brokerage+STT+slip)
H2_START = "2026-07-01"


# ---------------------------------------------------------------- build table
def build_morning(verbose=True, save=True):
    import glob, datetime as dt
    parts = []
    for fp in sorted(glob.glob(os.path.join(CANDLE_DIR, "*.csv.gz"))):
        sym = os.path.basename(fp)[:-7]
        d = pd.read_csv(fp)
        if not len(d):
            continue
        d = d.sort_values("epoch_min")
        mod = (d["epoch_min"] % 1440 + 330) % 1440          # IST minute-of-day
        ist_day = ((d["epoch_min"] + 330) // 1440).astype(int)
        date = ist_day.map(
            lambda k: (dt.date(1970, 1, 1) + dt.timedelta(days=int(k))).isoformat())
        d = d.assign(date=date, mod=mod)

        orw = d[d["mod"].between(555, 569)]                 # 9:15-9:30
        cnf = d[d["mod"].between(570, 584)]                 # 9:30-9:45
        fst = d[d["mod"].between(555, 584)]                 # first 30 min
        pst = d[d["mod"].between(585, 910)]                 # 9:45-15:10

        g = pd.DataFrame({
            "open": d.groupby("date")["open_paise"].first() / 100,
            "or15_h": orw.groupby("date")["high_paise"].max() / 100,
            "or15_l": orw.groupby("date")["low_paise"].min() / 100,
            "c945": cnf.groupby("date")["close_paise"].last() / 100,
            "v45": fst.groupby("date")["volume"].sum(),
            "morn_h": fst.groupby("date")["high_paise"].max() / 100,
            "morn_l": fst.groupby("date")["low_paise"].min() / 100,
            "rod_h": pst.groupby("date")["high_paise"].max() / 100,
            "rod_l": pst.groupby("date")["low_paise"].min() / 100,
            "rod_c": pst.groupby("date")["close_paise"].last() / 100,
        }).reset_index()
        g.insert(0, "symbol", sym)
        parts.append(g)
    m = pd.concat(parts, ignore_index=True)
    m["date"] = pd.to_datetime(m["date"])

    # first-30-min volume vs prior-20-day norm (no look-ahead)
    m = m.sort_values(["symbol", "date"]).reset_index(drop=True)
    vmed = (m.groupby("symbol")["v45"].transform(
        lambda s: s.rolling(20, min_periods=10).median().shift(1)))
    m["v45_ratio"] = m["v45"] / vmed

    if verbose:
        print(f"morning table: {len(m)} stock-days | {m['symbol'].nunique()} symbols | "
              f"{m['date'].min().date()} -> {m['date'].max().date()} | "
              f"c945 missing: {m['c945'].isna().sum()}")
    if save:
        m.to_csv(MORNING_F, index=False, compression="gzip", float_format="%.6g")
    return m


# ---------------------------------------------------------------- join priors
def load_joined():
    from direction import build_direction
    m = (pd.read_csv(MORNING_F, parse_dates=["date"])
         if os.path.exists(MORNING_F) else build_morning())
    ev = build_direction()                                 # evening-frame
    ev = ev[["symbol", "date", "close", "high", "low", "prior20H", "prior20L",
             "basis_z", "y_move", "next_tr_pct", "trap_v1"]]

    # map evening date -> next trading session's date
    cash_dates = sorted(ev["date"].unique())
    nxt = dict(zip(cash_dates[:-1], cash_dates[1:]))
    ev["next_date"] = ev["date"].map(nxt)

    j = m.merge(ev, left_on=["symbol", "date"], right_on=["symbol", "next_date"],
                how="inner", suffixes=("", "_T"))
    j["gap"] = j["open"] / j["close"] - 1                   # vs prior close
    j["or_mid"] = (j["or15_h"] + j["or15_l"]) / 2
    j["morn_dir"] = np.sign(j["c945"] - j["or_mid"])
    j["orb_up"] = j["c945"] > j["or15_h"]
    j["orb_dn"] = j["c945"] < j["or15_l"]
    j["prior"] = np.select([j["basis_z"] <= -1.5, j["basis_z"] > 1.5],
                           [-1, 1], default=0)
    # outcome: rest-of-day from the 9:45 entry
    j["rod_ret"] = j["rod_c"] / j["c945"] - 1
    j["rod_up"] = j["rod_ret"] > 0

    # OOS picks (evening prob) — pick date = T
    oos_f = os.path.join(OUTPUT, "oos_predictions.csv.gz")
    if os.path.exists(oos_f):
        oos = pd.read_csv(oos_f, parse_dates=["date"])[["date", "symbol", "prob"]]
        # top-10 by prob per date = the actual watchlist picks
        oos = (oos.sort_values("prob", ascending=False)
                  .groupby("date").head(10))
        j = j.merge(oos, left_on=["symbol", "next_date"], right_on=["symbol", "date"],
                    how="left", suffixes=("", "_pick")).drop(columns=["date_pick"])
        j["is_pick"] = j["prob"].notna()
    else:
        j["is_pick"] = False
    return j


# ---------------------------------------------------------------- rules
def rule_stats(j, mask, side, tag=""):
    """Trade: enter at c945, exit rod_c. side: 'long' | 'short' | 'prior'."""
    sub = j[mask & j["c945"].notna() & j["rod_ret"].notna()].copy()
    if side == "prior":
        lng = (sub["prior"] > 0).values
        sub["gross"] = np.where(lng, sub["rod_ret"], -sub["rod_ret"])
        r = np.where(lng, sub["c945"] - sub["or15_l"],
                     sub["or15_h"] - sub["c945"])
        sub["mfe_r"] = np.where(lng, sub["rod_h"] - sub["c945"],
                                sub["c945"] - sub["rod_l"]) / r
        sub["mae_r"] = np.where(lng, sub["rod_l"] - sub["c945"],
                                sub["c945"] - sub["rod_h"]) / r
    elif side == "long":
        sub["gross"] = sub["rod_ret"]
        r = (sub["c945"] - sub["or15_l"]).values
        sub["mfe_r"] = (sub["rod_h"] - sub["c945"]) / r
        sub["mae_r"] = (sub["rod_l"] - sub["c945"]) / r
    else:
        sub["gross"] = -sub["rod_ret"]
        r = (sub["or15_h"] - sub["c945"]).values
        sub["mfe_r"] = (sub["c945"] - sub["rod_l"]) / r
        sub["mae_r"] = (sub["c945"] - sub["rod_h"]) / r
    sub = sub[np.asarray(r) > 0]
    sub["net"] = sub["gross"] - COST
    if not len(sub):
        print(f"{tag:<34} n=0")
        return
    print(f"{tag:<34} n={len(sub):>4}  win={100*(sub['net']>0).mean():5.1f}%  "
          f"avg net {sub['net'].mean()*100:+.2f}%  "
          f"med {sub['net'].median()*100:+.2f}%  "
          f"MFE {sub['mfe_r'].mean():+.1f}R MAE {sub['mae_r'].mean():+.1f}R")


def analyze():
    j = load_joined()
    print(f"joined stock-days: {len(j)} | with picks: {int(j['is_pick'].sum())}")
    yr = j[j["date"] >= "2026-01-05"].copy()                # skip 1st week (v45 norm)
    picks = yr[yr["is_pick"]]
    movers = yr[yr["y_move"] == 1]

    # ---------------- Phase 1: morning diagnostics on picks
    print(f"\n=== PHASE 1a: does the 9:45 tape predict the rest of the day? "
          f"(OOS picks, n={len(picks)}) ===")
    print(f"base: rod up-rate {100*picks['rod_up'].mean():.1f}% | "
          f"mean |rod| {100*picks['rod_ret'].abs().mean():.2f}%")
    for name, mask, side in [
            ("gap > +0.75% (gap-up)", picks["gap"] > 0.0075, +1),
            ("gap < -0.75% (gap-down)", picks["gap"] < -0.0075, -1),
            ("morning above OR mid", picks["morn_dir"] > 0, +1),
            ("morning below OR mid", picks["morn_dir"] < 0, -1),
            ("broke OR UP by 9:45", picks["orb_up"], +1),
            ("broke OR DOWN by 9:45", picks["orb_dn"], -1)]:
        sub = picks[mask & picks["rod_ret"].notna()]
        if not len(sub):
            continue
        hit = (side * sub["rod_ret"] > 0).mean()
        avg = side * sub["rod_ret"].mean()
        print(f"  {name:<28} n={len(sub):>4}  continuation hit {hit*100:5.1f}%  "
              f"avg in-dir {avg*100:+.2f}%")

    print(f"\n=== PHASE 1b: same, split H1/H2 (stability check) ===")
    for half, hd in [("H1(Apr-Jun)", picks[picks["date"] < H2_START]),
                     ("H2(Jul-Sep)", picks[picks["date"] >= H2_START])]:
        d = hd[hd["rod_ret"].notna()]
        up = d[d["morn_dir"] > 0]; dn = d[d["morn_dir"] < 0]
        bu = d[d["orb_up"]]; bd = d[d["orb_dn"]]
        print(f"  {half}: base up {100*d['rod_up'].mean():.1f}% | "
              f"morn>mid->up {100*up['rod_up'].mean() if len(up) else float('nan'):.1f}% (n={len(up)}) | "
              f"morn<mid->down {100*(1-dn['rod_up'].mean()) if len(dn) else float('nan'):.1f}% (n={len(dn)}) | "
              f"ORup->up {100*bu['rod_up'].mean() if len(bu) else float('nan'):.1f}% (n={len(bu)}) | "
              f"ORdn->down {100*(1-bd['rod_up'].mean()) if len(bd) else float('nan'):.1f}% (n={len(bd)})")

    print(f"\n=== PHASE 1c: 2x2 — evening prior x morning direction (picks) ===")
    for pv, plabel in [(-1, "SHORT prior"), (1, "LONG prior"), (0, "neutral")]:
        for mv, mlabel in [(1, "morn UP"), (-1, "morn DOWN")]:
            sub = picks[(picks["prior"] == pv) & (picks["morn_dir"] == mv)
                        & picks["rod_ret"].notna()]
            if not len(sub):
                continue
            print(f"  {plabel:<12} x {mlabel:<9} n={len(sub):>4}  "
                  f"rod up {100*sub['rod_up'].mean():5.1f}%  "
                  f"avg rod {sub['rod_ret'].mean()*100:+.2f}%")

    # ---------------- Phase 2: rule backtests (with costs), H1/H2
    print(f"\n=== PHASE 2: rule backtests on OOS picks (cost 0.15% rt) ===")
    def run_rules(d, label):
        print(f"--- {label} ---")
        rule_stats(d, d["prior"] != 0, "prior", tag="B0 evening prior only (no morning)")
        rule_stats(d, (d["prior"] == -1) & (d["morn_dir"] < 0), "short", tag="R1 SHORT prior + morn confirms")
        rule_stats(d, (d["prior"] == 1) & (d["morn_dir"] > 0), "long", tag="R1 LONG prior + morn confirms")
        rule_stats(d, (d["prior"] == -1) & d["orb_dn"], "short", tag="R2 SHORT prior + broke OR down")
        rule_stats(d, (d["prior"] == 1) & d["orb_up"], "long", tag="R2 LONG prior + broke OR up")
        rule_stats(d, d["orb_up"], "long", tag="R3 morning-only: follow OR-up")
        rule_stats(d, d["orb_dn"], "short", tag="R3 morning-only: follow OR-down")
        rule_stats(d, d["orb_up"], "short", tag="R4 morning-only: FADE OR-up")
        rule_stats(d, d["orb_dn"], "long", tag="R4 morning-only: FADE OR-down")
        rule_stats(d, (d["gap"] <= -0.0075) & (d["morn_dir"] > 0), "long",
                   tag="S1 gap-fade: gap-down + reclaim")
        rule_stats(d, (d["gap"] >= 0.0075) & (d["morn_dir"] < 0), "short",
                   tag="S1 gap-fade: gap-up + fade")
    pk = picks[picks["rod_ret"].notna()]
    run_rules(pk, "ALL (Apr-Sep)")
    run_rules(pk[pk["date"] < H2_START], "H1 (Apr-Jun)")
    run_rules(pk[pk["date"] >= H2_START], "H2 (Jul-Sep)")

    # ---------------- movers test bed (bigger n, all 2026)
    print(f"\n=== PHASE 2b: key rules on ALL next-day movers (n={len(movers)}) ===")
    rule_stats(movers, movers["orb_up"], "long", tag="R3 follow OR-up (movers)")
    rule_stats(movers, movers["orb_dn"], "short", tag="R3 follow OR-down (movers)")
    rule_stats(movers, movers["orb_up"], "short", tag="R4 fade OR-up (movers)")
    rule_stats(movers, movers["orb_dn"], "long", tag="R4 fade OR-down (movers)")
    rule_stats(movers, (movers["prior"] == -1) & (movers["morn_dir"] < 0), "short",
               tag="R1 SHORT prior + morn confirms (movers)")

    # volume trigger diagnostic
    print(f"\n=== PHASE 1d: first-30-min volume surge -> afternoon movement? ===")
    for lab, msk in [("v45_ratio >= 2", movers["v45_ratio"] >= 2),
                     ("v45_ratio < 0.7", movers["v45_ratio"] < 0.7)]:
        sub = movers[msk & movers["rod_ret"].notna()]
        print(f"  {lab:<16} n={len(sub):>4}  mean |rod| {100*sub['rod_ret'].abs().mean():.2f}% "
              f"vs all movers {100*movers['rod_ret'].abs().mean():.2f}%")
    return j


if __name__ == "__main__":
    analyze()
