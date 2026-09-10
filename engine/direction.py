"""
direction.py — STAGE 2: the trap/direction layer (v1 + data-informed v2).

v1 score: a priori trap-framework weights (no fitting).
v2 score: same structure, but per-feature SIGNS chosen on H1 (Jan–Jun 2026)
          mover-subset lift, then tested untouched on H2 (Jul–Sep 2026).

Labels: next-day close-to-close sign (primary), next-day open-to-close
        (body), MFE/MAE excursions vs today's close.
"""
import os, glob
import numpy as np
import pandas as pd
from config import PANEL_F, OUTPUT

LONG_T, STRONG_T = 3, 4
H2_START = "2026-07-01"          # H1 = fit, H2 = clean test

APRIORI = {  # feature -> (a priori sign, a priori weight)
    "sfp_bull": (1, 3), "sfp_bear": (-1, 3), "sfp_volcf": (None, 1),
    "clv_acc": (1, 1), "clv_dist": (-1, 1),
    "rented": (-1, 2), "absorb": (1, 2), "realbuy": (1, 1), "pumpinv": (-1, 2),
    "wick_lo": (1, 1), "wick_hi": (-1, 1),
    "longbuild": (1, 1), "shortcov": (-1, 1), "sb_near_lo": (1, 1),
    "basis_disc": (1, 1), "basis_prem": (-1, 1),
    "putwrite": (1, 1), "callwrite": (-1, 1),
}


def build_direction(p=None):
    if p is None:
        p = pd.read_csv(PANEL_F, parse_dates=["date"])
    p = p.sort_values(["symbol", "date"]).reset_index(drop=True).copy()

    def ROL(col, n, minp=None, agg="mean"):
        s = p.groupby("symbol")[col].rolling(n, min_periods=minp or n)
        return getattr(s, agg)().reset_index(level=0, drop=True).sort_index()
    def SHIFT(col, n=1):
        return p.groupby("symbol")[col].shift(n)

    p["_r20h"] = ROL("high", 20, 20, agg="max");  p["prior20H"] = SHIFT("_r20h")
    p["_r20l"] = ROL("low", 20, 20, agg="min");   p["prior20L"] = SHIFT("_r20l")
    p["_dm"] = ROL("deliv_pct", 20, 10);          p["_ds"] = ROL("deliv_pct", 20, 10, agg="std")
    p["deliv_z"] = (p["deliv_pct"] - SHIFT("_dm")) / SHIFT("_ds").replace(0, np.nan)
    p["_vmax5"] = ROL("volume", 5, 5, agg="max")
    p["vol_is_max5"] = p["volume"] >= p["_vmax5"]
    p["_bm"] = ROL("basis_pct", 20, 10);          p["_bs"] = ROL("basis_pct", 20, 10, agg="std")
    p["basis_z"] = (p["basis_pct"] - SHIFT("_bm")) / SHIFT("_bs").replace(0, np.nan)
    rng = (p["high"] - p["low"]).replace(0, np.nan)
    p["wick_hi_frac"] = (p["high"] - p[["open", "close"]].max(axis=1)) / rng
    p["wick_lo_frac"] = (p[["open", "close"]].min(axis=1) - p["low"]) / rng
    p["near_high"] = p["close"] >= p["prior20H"] * 0.97
    p["near_low"] = p["close"] <= p["prior20L"] * 1.03
    tot_oi = (p["ce_oi"] + p["pe_oi"]).replace(0, np.nan)
    p["optflow"] = (p["pe_oi_chg"] - p["ce_oi_chg"]) / tot_oi

    up, dn = p["ret1"] > 0, p["ret1"] < 0
    upoi, dnoi = p["foi_chg1"] > 0, p["foi_chg1"] < 0

    F = pd.DataFrame(index=p.index)
    F["sfp_bull"] = (p["low"] < p["prior20L"]) & (p["close"] > p["prior20L"])
    F["sfp_bear"] = (p["high"] > p["prior20H"]) & (p["close"] < p["prior20H"])
    vol2 = p["vol_ratio"] >= 2.0
    F["sfp_volcf"] = vol2 & (F["sfp_bull"] | F["sfp_bear"])
    # clv is on [0,1] scale in the panel: >0.75 = strong close, <0.25 = weak close
    F["clv_acc"] = p["vol_is_max5"] & (p["clv"] > 0.75)
    F["clv_dist"] = p["vol_is_max5"] & (p["clv"] < 0.25)
    F["rented"] = up & (p["deliv_z"] < -1)
    F["absorb"] = dn & (p["deliv_z"] > 1)
    F["realbuy"] = up & (p["deliv_z"] > 1)
    F["pumpinv"] = (p["ret20"] > 0.25) & (p["deliv_z"] > 2)
    F["wick_lo"] = p["near_low"] & (p["wick_lo_frac"] >= 0.5)
    F["wick_hi"] = p["near_high"] & (p["wick_hi_frac"] >= 0.5)
    F["longbuild"] = up & upoi
    F["shortcov"] = up & dnoi
    F["sb_near_lo"] = dn & upoi & p["near_low"]
    F["basis_disc"] = p["basis_z"] < -1.5
    F["basis_prem"] = p["basis_z"] > 1.5
    F["putwrite"] = p["optflow"] >= 0.02
    F["callwrite"] = p["optflow"] <= -0.02
    F = F.fillna(False)
    for k in F.columns:
        p["f_" + k] = F[k]

    # ---------------- v1 score (a priori)
    s1 = sum(APRIORI[k][0] * APRIORI[k][1] * F[k] for k in APRIORI
             if APRIORI[k][0] is not None)
    s1 = s1 + F["sfp_volcf"] * np.where(F["sfp_bull"], 1, -1)
    p["trap_v1"] = s1

    # ---------------- labels
    for col, sh in [("close", -1), ("open", -1), ("high", -1), ("low", -1), ("close", -2)]:
        p["_" + col + str(sh)] = SHIFT(col, sh)
    p["next_ret"] = p["_close-1"] / p["close"] - 1
    p["next_body"] = p["_close-1"] / p["_open-1"] - 1
    p["next2_ret"] = p["_close-2"] / p["close"] - 1
    p["mfe_long"] = p["_high-1"] / p["close"] - 1
    p["mae_long"] = p["_low-1"] / p["close"] - 1
    p.drop(columns=[c for c in p.columns if c.startswith("_")], inplace=True)
    return p


def fit_v2_signs(p, verbose=True):
    """Choose per-feature sign (or 0) from H1 MOVER-subset lift vs base."""
    h1 = p[(p["date"] < H2_START) & p["next_ret"].notna()]
    mov = h1[h1["y_move"] == 1]
    base = (mov["next_ret"] > 0).mean()
    signs = {}
    if verbose:
        print(f"H1 movers: n={len(mov)} | base UP-rate: {base*100:.1f}%")
        print(f"{'feature':<13}{'n':>6}{'up%':>7}{'lift':>7}  v2sign")
    for k, (a_sign, w) in APRIORI.items():
        if a_sign is None:                      # sfp_volcf: direction = sfp side
            m = mov["f_" + k]
            sub = mov[m]
            if len(sub) < 40:
                signs[k] = 0; continue
            upr = (sub["next_ret"] > 0).mean()
            signs[k] = 1 if upr - base >= 0.02 else (0 if abs(upr - base) < 0.02 else -1)
            # volcf follows its sfp parent's empirical direction (bull side)
            if verbose:
                print(f"{k:<13}{len(sub):>6}{upr*100:>6.1f}%{upr-base:>+6.1%}  "
                      f"{signs[k]:+d}")
            continue
        m = mov["f_" + k]
        sub = mov[m]
        if len(sub) < 40:
            signs[k] = 0
            if verbose: print(f"{k:<13}{len(sub):>6}   --     --   {0:+d}")
            continue
        upr = (sub["next_ret"] > 0).mean()
        lift = upr - base
        # sign = empirical direction if |lift| >= 2pp, else drop the feature
        signs[k] = int(np.sign(lift)) if abs(lift) >= 0.02 else 0
        if verbose:
            print(f"{k:<13}{len(sub):>6}{upr*100:>6.1f}%{lift:>+6.1%}  {signs[k]:+d}")
    return signs


def apply_v2(p, signs):
    s2 = sum(signs[k] * APRIORI[k][1] * p["f_" + k] for k in APRIORI
             if signs.get(k, 0) != 0)
    if signs.get("sfp_volcf", 0) != 0:
        s2 = s2 + p["f_sfp_volcf"] * np.where(p["f_sfp_bull"],
                                              signs["sfp_volcf"], -signs["sfp_volcf"])
    p["trap_v2"] = s2
    return p


def sig_stats(df, mask, side):
    sub = df[mask & df["next_ret"].notna()]
    n = len(sub)
    if n == 0:
        return None
    if side > 0:
        hit = (sub["next_ret"] > 0).mean(); ret = sub["next_ret"].mean()
        mfe, mae = sub["mfe_long"].mean(), sub["mae_long"].mean()
    else:
        hit = (sub["next_ret"] < 0).mean(); ret = -sub["next_ret"].mean()
        mfe, mae = -sub["mae_long"].mean(), -sub["mfe_long"].mean()
    return dict(n=n, hit=hit, ret=ret, mfe=mfe, mae=mae,
                base=(df["next_ret"] > 0).mean())


def show(tag, st):
    if st is None:
        print(f"{tag:<22} n=0"); return
    print(f"{tag:<22} n={st['n']:>4}  hit={st['hit']*100:5.1f}% "
          f"(base {st['base']*100:.1f}%)  avg ret {st['ret']*100:+.2f}%  "
          f"MFE {st['mfe']*100:+.2f}% vs MAE {st['mae']*100:+.2f}%")


def evaluate():
    p = build_direction()
    signs = fit_v2_signs(p)
    p = apply_v2(p, signs)

    h2 = p[(p["date"] >= H2_START) & p["next_ret"].notna()].copy()
    h2m = h2[h2["y_move"] == 1]
    print(f"\n=== H2 CLEAN TEST (Jul–Sep 2026): {h2['date'].nunique()} sessions ===")
    print(f"base UP-rate: universe {(h2['next_ret']>0).mean()*100:.1f}% | "
          f"movers {(h2m['next_ret']>0).mean()*100:.1f}% (n={len(h2m)})")

    print("\n--- v1 (a priori) on H2 movers ---")
    show("LONG >= +3", sig_stats(h2m, h2m["trap_v1"] >= LONG_T, 1))
    show("LONG >= +4", sig_stats(h2m, h2m["trap_v1"] >= STRONG_T, 1))
    show("SHORT <= -3", sig_stats(h2m, h2m["trap_v1"] <= -LONG_T, -1))
    show("SHORT <= -4", sig_stats(h2m, h2m["trap_v1"] <= -STRONG_T, -1))

    print("\n--- v2 (H1-fitted signs) on H2 movers ---")
    show("LONG >= +3", sig_stats(h2m, h2m["trap_v2"] >= LONG_T, 1))
    show("LONG >= +4", sig_stats(h2m, h2m["trap_v2"] >= STRONG_T, 1))
    show("SHORT <= -3", sig_stats(h2m, h2m["trap_v2"] <= -LONG_T, -1))
    show("SHORT <= -4", sig_stats(h2m, h2m["trap_v2"] <= -STRONG_T, -1))

    # ---------------- on the engine's OOS Top-10 picks, H2 only
    oos_f = os.path.join(OUTPUT, "oos_predictions.csv.gz")
    if os.path.exists(oos_f):
        oos = pd.read_csv(oos_f, parse_dates=["date"])
        picks = (oos.sort_values("prob", ascending=False)
                     .groupby("date").head(10))
        pk = picks.merge(h2[["date", "symbol", "trap_v1", "trap_v2", "next_ret",
                             "next_body", "mfe_long", "mae_long"]],
                         on=["date", "symbol"], how="inner")
        print(f"\n=== ENGINE'S OOS TOP-10 PICKS in H2: {len(pk)} picks ===")
        print(f"picks base UP-rate: {(pk['next_ret']>0).mean()*100:.1f}%")
        print("--- v1 ---")
        show("LONG >= +3", sig_stats(pk, pk["trap_v1"] >= LONG_T, 1))
        show("SHORT <= -3", sig_stats(pk, pk["trap_v1"] <= -LONG_T, -1))
        print("--- v2 ---")
        show("LONG >= +3", sig_stats(pk, pk["trap_v2"] >= LONG_T, 1))
        show("LONG >= +4", sig_stats(pk, pk["trap_v2"] >= STRONG_T, 1))
        show("SHORT <= -3", sig_stats(pk, pk["trap_v2"] <= -LONG_T, -1))

    # ---------------- T+2 check on v2
    print("\n--- v2 LONG T+2 (movers) ---")
    sub = h2m[(h2m["trap_v2"] >= LONG_T) & h2m["next2_ret"].notna()]
    if len(sub):
        print(f"n={len(sub)}  T+2 hit={(sub['next2_ret']>0).mean()*100:.1f}%  "
              f"avg {sub['next2_ret'].mean()*100:+.2f}%")
    return p


def annotate_latest():
    """Append the VALIDATED direction layer (basis tilt) to the newest watchlist.

    Findings (Apr–Sep 2026 OOS picks, stable across both halves):
      basis_z <= -1.5 (discount)  -> 66.4% DOWN next day, avg -0.76%,
                                     short MFE/MAE = +2.45%/-1.26%
      basis_z >  1.5 (premium)    -> 55.5% up, mild long tilt
      |basis_z| <= 1.5            -> no directional edge (54.5% up = base)
    The signal is NOT a momentum proxy: it separates even among picks that
    rose today (32.7% vs 56.6% next-day up-rate).
    """
    wls = sorted(glob.glob(os.path.join(OUTPUT, "watchlist_*.csv")))
    wls = [w for w in wls if "_dir" not in w]
    if not wls:
        print("no watchlist found"); return None
    wl = wls[-1]
    tgt = os.path.basename(wl)[10:-4]
    src_date = (pd.Timestamp(tgt) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    p = build_direction()
    day = p[p["date"] == src_date][["symbol", "basis_z", "trap_v1"]]
    w = pd.read_csv(wl).merge(day, on="symbol", how="left")
    w["direction"] = np.select(
        [w["basis_z"] <= -1.5, w["basis_z"] > 1.5],
        ["SHORT ★★", "LONG-OK"], default="NEUTRAL")
    w["basis_z"] = w["basis_z"].round(2)
    w.to_csv(wl.replace(".csv", "_dir.csv"), index=False)
    md = wl.replace(".csv", "_dir.md")
    with open(md, "w") as f:
        f.write(f"# Top-10 + DIRECTION — for session {tgt}\n"
                f"*basis tilt from {src_date} close*\n\n"
                f"**Validated direction rule (OOS picks, Apr–Sep 2026):** futures "
                f"discount (z <= −1.5) → **SHORT ★★** (66.4% down next day, avg "
                f"−0.76%, MFE/MAE 2:1) · premium (z > +1.5) → LONG-OK (55.5%) · "
                f"neutral → no edge.\n\n```\n"
                f"{w[['rank','symbol','close','prob','basis_z','direction','fingerprints']].to_string(index=False)}\n```\n"
                f"\n_Warning: the worst single adverse move for a discount-short was "
                f"+10% (squeeze). Stops are non-negotiable. Research tool, not "
                f"investment advice._\n")
    print(f"\ndirection annotated: {md}")
    return w


if __name__ == "__main__":
    evaluate()
    annotate_latest()
