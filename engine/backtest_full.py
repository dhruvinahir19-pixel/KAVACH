"""
backtest_full.py — STAGE 4: full 2025->2026 backtest of the Morning Reveal
protocol, with regime segmentation and integrity audits.

Requires (built in order):
  1. features.py  -> panel over Oct 2024 -> Sep 2026 (TRAIN_FROM 2025-01-01)
  2. validate.py  -> oos_predictions.csv.gz (walk-forward, first OOS Apr 2025)
  3. morning.py   -> morning table over Jan 2025 -> Sep 2026

Rules tested (identical to Stage 3):
  LONG : evening Top-10 pick, 9:45 price > OR15 high  -> long, exit 15:10
  SHORT: evening Top-10 pick, 9:45 price < OR15 low   -> short, exit 15:10
  Costs 0.15% round trip. Entry = 9:45 bar close. Outcome = 9:45->15:10.
"""
import os
import numpy as np
import pandas as pd
from morning import load_joined, COST
from config import DATA, OUTPUT

MKT = os.path.join(DATA, "mkt.csv.gz")
VIX = os.path.join(DATA, "vix.csv")


def prep():
    j = load_joined()
    j = j[j["rod_ret"].notna()].copy()
    picks = j[j["is_pick"]].copy()
    # regime context per trade date
    mkt = pd.read_csv(MKT, parse_dates=["date"]).sort_values("date")
    mkt["month"] = mkt["date"].dt.to_period("M")
    mret = mkt.groupby("month").agg(nifty_open=("nifty_close", "first"),
                                    nifty_close=("nifty_close", "last"))
    mret["nifty_mret"] = mret["nifty_close"] / mret["nifty_open"] - 1
    vix = pd.read_csv(VIX, parse_dates=["date"])
    vix["month"] = vix["date"].dt.to_period("M")
    vavg = vix.groupby("month")["vix"].mean()
    picks["month"] = picks["date"].dt.to_period("M")
    picks["nifty_mret"] = picks["month"].map(mret["nifty_mret"])
    picks["vix_avg"] = picks["month"].map(vavg)
    return j, picks


def perf(t, side):
    """t: trades df with rod_ret; side: +1 long, -1 short."""
    net = side * t["rod_ret"] - COST
    return dict(n=len(t), win=(net > 0).mean() * 100 if len(t) else np.nan,
                avg=net.mean() * 100 if len(t) else np.nan,
                med=net.median() * 100 if len(t) else np.nan,
                sum=net.sum() if len(t) else 0.0)


def show(tag, t, side):
    p = perf(t, side)
    print(f"{tag:<38} n={p['n']:>4}  win={p['win']:5.1f}%  avg {p['avg']:+.2f}%  "
          f"med {p['med']:+.2f}%  sum {p['sum']*100:+.0f}pp")


def streaks(t, side):
    net = (side * t.sort_values("date")["rod_ret"] - COST) > 0
    cur = mx = 0
    for s in net:
        cur = 0 if s else cur + 1
        mx = max(mx, cur)
    return mx


def main():
    j, picks = prep()
    oos = picks[picks["date"] >= "2025-04-01"].copy()
    print(f"OOS picks joined: {len(oos)} pick-days over "
          f"{oos['date'].nunique()} mornings ({oos['date'].min().date()} -> "
          f"{oos['date'].max().date()})")
    exp = oos.groupby("date")["is_pick"].sum()
    print(f"completeness: median picks/morning = {exp.median():.0f} "
          f"(target 10) | mornings with <10: {(exp < 10).sum()}")

    L = oos[oos["orb_up"]]
    S = oos[oos["orb_dn"]]

    print("\n================ MONTHLY PERFORMANCE ================")
    print(f"{'month':<10}{'LONG n':>7}{'win%':>7}{'avg%':>7}{'sum':>7}   "
          f"{'SHORT n':>8}{'win%':>7}{'avg%':>7}{'sum':>7}")
    for m, g in oos.groupby("month"):
        gl, gs = g[g["orb_up"]], g[g["orb_dn"]]
        pl, ps = perf(gl, 1), perf(gs, -1)
        print(f"{str(m):<10}{pl['n']:>7}{pl['win']:>7.1f}{pl['avg']:>+7.2f}"
              f"{pl['sum']*100:>6.0f}p   {ps['n']:>8}{ps['win']:>7.1f}"
              f"{ps['avg']:>+7.2f}{ps['sum']*100:>6.0f}p")
    print(f"{'TOTAL':<10}{len(L):>7}{perf(L,1)['win']:>7.1f}{perf(L,1)['avg']:>+7.2f}"
          f"{perf(L,1)['sum']*100:>6.0f}p   {len(S):>8}{perf(S,-1)['win']:>7.1f}"
          f"{perf(S,-1)['avg']:>+7.2f}{perf(S,-1)['sum']*100:>6.0f}p")

    print("\n================ REGIME SEGMENTATION ================")
    med_vix = oos["vix_avg"].median()
    segs = [
        ("YEAR: 2025", oos[oos["date"] < "2026-01-01"]),
        ("YEAR: 2026", oos[oos["date"] >= "2026-01-01"]),
        ("Nifty UP month", oos[oos["nifty_mret"] > 0]),
        ("Nifty DOWN month", oos[oos["nifty_mret"] <= 0]),
        (f"VIX avg >= {med_vix:.1f} (high)", oos[oos["vix_avg"] >= med_vix]),
        (f"VIX avg <  {med_vix:.1f} (low)", oos[oos["vix_avg"] < med_vix]),
        ("Nifty down AND VIX high", oos[(oos["nifty_mret"] <= 0) &
                                        (oos["vix_avg"] >= med_vix)]),
    ]
    for tag, g in segs:
        print(f"--- {tag} ({len(g)} pick-days) ---")
        show("  LONG (OR-up)", g[g["orb_up"]], 1)
        show("  SHORT (OR-dn)", g[g["orb_dn"]], -1)

    print("\n================ CONTROLS & RISK ================")
    ou = j[(j["orb_up"]) & (j["date"] >= "2025-04-01")]
    tab = ou.groupby(ou["is_pick"]).agg(n=("rod_ret", "size"),
                                        up=("rod_up", "mean"),
                                        avg=("rod_ret", "mean"))
    print("same-day control (all OR-up stocks vs picks):")
    print(tab.rename(index={True: "PICKS", False: "all stocks"}).round(3).to_string())
    print(f"\nLONG: max losing streak {streaks(L, 1)} | worst {(L['rod_ret']-COST).min()*100:.2f}% | "
          f"best {(L['rod_ret']-COST).max()*100:.2f}%")
    print(f"SHORT: max losing streak {streaks(S, -1)} | worst {(-S['rod_ret']-COST).min()*100:.2f}% | "
          f"best {(-S['rod_ret']-COST).max()*100:.2f}%")
    # per-day clustering
    dly = pd.concat([L.assign(net=L["rod_ret"] - COST),
                     S.assign(net=-S["rod_ret"] - COST)]).groupby("date")["net"].sum()
    print(f"per-day P&L (1x each): days {len(dly)} | avg {dly.mean()*100:+.2f}% | "
          f"win-days {(dly > 0).mean()*100:.0f}% | worst {dly.min()*100:.2f}% | best {dly.max()*100:+.2f}%")
    return oos


if __name__ == "__main__":
    main()
