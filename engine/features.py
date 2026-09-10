"""
features.py — build the research panel: ~60 features per stock-day.

Discipline: every feature at row (t, stock) uses ONLY data up to close of t.
Ratios vs "20-d avg" use the mean of the PRIOR 20 sessions (today excluded),
so a surge isn't diluted by itself. Labels use t+1 data only.

Input : data/{cash,fut,opt,part,mkt}.csv.gz + data/vix.csv
Output: data/panel.csv.gz
"""
import os
import numpy as np
import pandas as pd
from config import CASH_F, FUT_F, OPT_F, PART_F, MKT_F, VIX_F, PANEL_F, TRAIN_FROM


def build_panel(verbose=True):
    cash = pd.read_csv(CASH_F, parse_dates=["date"])
    fut = pd.read_csv(FUT_F, parse_dates=["date"])
    opt = pd.read_csv(OPT_F, parse_dates=["date"])
    part = pd.read_csv(PART_F, parse_dates=["date"])
    mkt = pd.read_csv(MKT_F, parse_dates=["date"])
    vix = pd.read_csv(VIX_F, parse_dates=["date"])

    p = cash.sort_values(["symbol", "date"]).reset_index(drop=True).copy()
    p = p.merge(fut, on=["date", "symbol"], how="left")
    p = p.merge(opt, on=["date", "symbol"], how="left")

    # --- primitives (verified-safe groupby patterns; panel is symbol/date sorted)
    def ROL(col, n, minp=None, agg="mean"):
        s = p.groupby("symbol")[col].rolling(n, min_periods=minp or n)
        return getattr(s, agg)().reset_index(level=0, drop=True).sort_index()
    def SHIFT(col, n=1):
        return p.groupby("symbol")[col].shift(n)
    def PCT(col, n=1):
        return p.groupby("symbol")[col].pct_change(n, fill_method=None)
    def DIFF(col, n=1):
        return p.groupby("symbol")[col].diff(n)
    def tmp(name, series):
        p[name] = series

    c, h, l, o, pc = p["close"], p["high"], p["low"], p["open"], p["prev_close"]

    # ---------------------------------------------------- price action
    tmp("ret1", PCT("close"))
    for n in (5, 10, 20):
        tmp(f"ret{n}", PCT("close", n))
    tmp("range_pct", (h - l) / c)
    tmp("tr", pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1))
    tmp("atr14", ROL("tr", 14, 10)); tmp("atr5", ROL("tr", 5, 3)); tmp("atr20", ROL("tr", 20, 10))
    p["log_atr14p"] = np.log(p["atr14"] / c)
    p["atr5_atr20"] = p["atr5"] / p["atr20"]
    p["clv"] = (c - l) / (h - l).replace(0, np.nan)
    p["gap_pct"] = o / pc - 1
    p["expansion_today"] = p["tr"] / p["atr14"]
    tmp("r_min7", p.groupby("symbol")["range_pct"].rolling(7, min_periods=7).min()
        .reset_index(level=0, drop=True).sort_index())
    p["nr7"] = (p["range_pct"] <= p["r_min7"]).astype(int)
    p["inside_day"] = ((h <= SHIFT("high")) & (l >= SHIFT("low"))).astype(int)
    tmp("r5", ROL("range_pct", 5, 3)); tmp("r60", ROL("range_pct", 60, 20))
    p["squeeze"] = p["r5"] / p["r60"]
    p["prev_squeeze"] = SHIFT("squeeze")
    for n, mp in [(20, 20), (60, 40), (252, 60)]:
        tmp(f"mx{n}", ROL("high", n, mp, agg="max"))
        p[f"dist{n}h"] = c / p[f"mx{n}"] - 1
    tmp("ma20", ROL("close", 20, 10))
    p["ma20_dist"] = c / p["ma20"] - 1
    tmp("_ma20", p["ma20"])
    p["ma20_slope"] = PCT("_ma20", 5)
    sgn = np.sign(p["ret1"]).fillna(0)
    tmp("_sgn", sgn)
    tmp("_streak", sgn.groupby(p["symbol"], group_keys=False).apply(
        lambda s: s.groupby((s != s.shift()).cumsum()).cumcount() + 1).sort_index())
    p["run_dir"] = p["_streak"] * sgn.replace(0, 1)
    p["dow"] = p["date"].dt.dayofweek

    # ---------------------------------------------------- volume & delivery
    def ratio(col, n=20):
        tmp(f"_b{col}", ROL(col, n, 10))
        tmp(f"_b{col}s", SHIFT(f"_b{col}"))
        return p[col] / p[f"_b{col}s"]
    p["vol_ratio"] = ratio("volume")
    tmp("_v5", ROL("volume", 5, 3)); tmp("_v5b", SHIFT("_v5"))
    p["vol_ratio5"] = p["_v5"] / ROL("_v5", 20, 10).groupby(p["symbol"]).shift(1)
    p["turnov_ratio"] = ratio("turnover_l")
    p["trades_ratio"] = ratio("trades")
    p["deliv_pct"] = p["deliv_per"]
    p["deliv_ratio"] = ratio("deliv_qty")
    tmp("_dpb", ROL("deliv_pct", 20, 10))
    p["deliv_spike"] = p["deliv_pct"] - SHIFT("_dpb")
    p["deliv_x_vol"] = p["deliv_ratio"] * p["vol_ratio"]
    tmp("_dp_up", p["deliv_pct"].where(p["ret1"] > 0))
    p["deliv_up_day"] = ROL("_dp_up", 20, 3)

    # ---------------------------------------------------- futures layer
    p["fut_oi"] = p["fut_oi"]
    p["foi_chg1"] = PCT("fut_oi")
    p["foi_chg5"] = PCT("fut_oi", 5)
    p["fut_vol_ratio"] = ratio("fut_vol")
    p["doi_norm"] = p["fut_oi_chg"] / p["fut_oi"]
    p["basis_pct"] = (p["nm_close"] - c) / c
    up_p, up_o = p["ret1"] > 0, p["foi_chg1"] > 0
    p["poi_state"] = np.select(
        [up_p & up_o, up_p & ~up_o, ~up_p & ~up_o, ~up_p & up_o],
        [1, 2, 3, 4], default=0)   # 1 long buildup, 2 short covering,
                                   # 3 long unwinding, 4 short buildup

    # ---------------------------------------------------- option layer
    tmp("_tot_oi", p["ce_oi"] + p["pe_oi"])
    p["pcr_oi"] = p["pe_oi"] / p["ce_oi"]
    p["pcr_vol"] = p["pe_vol"] / p["ce_vol"]
    p["pcr_chg1"] = DIFF("pcr_oi")
    p["pcr_chg5"] = DIFF("pcr_oi", 5)
    p["opt_oi_chg5"] = PCT("_tot_oi", 5)
    tmp("_oval", p["ce_val"] + p["pe_val"])
    tmp("_ovalb", ROL("_oval", 20, 10))
    p["opt_vol_ratio"] = p["_oval"] / SHIFT("_ovalb")
    p["top3_strike_conc"] = p["top3_conc"]
    p["call_build"] = p["call_build"]

    # ---------------------------------------------------- market layer
    p = p.sort_values("date").reset_index(drop=True)
    p = p.merge(vix, on="date", how="left")
    p["vix"] = p["vix"].ffill()
    p["vix_chg1"] = p["vix"].diff()
    p["vix_chg5"] = p["vix"].diff(5)
    p = p.merge(mkt[["date", "nifty_close", "nifty_pcr", "next_expiry"]],
                on="date", how="left")
    p["nifty_ret1"] = p.groupby("date")["nifty_close"].transform("first").pct_change()
    p["nifty_pcr"] = p["nifty_pcr"].ffill()
    p["days_to_exp"] = (pd.to_datetime(p["next_expiry"]) - p["date"]).dt.days
    p["mkt_breadth"] = p.groupby("date")["ma20_dist"].transform(
        lambda s: (s > 0).mean())

    # ---------------------------------------------------- participants (ffill)
    part2 = part.sort_values("date").set_index("date").ffill().reset_index()
    keep = [k for k in ["date", "fii_stf_net", "client_stf_net",
                        "pro_stf_net", "fii_idf_net"] if k in part2.columns]
    p = p.merge(part2[keep], on="date", how="left")
    for col in ["fii_stf_net", "client_stf_net", "pro_stf_net", "fii_idf_net"]:
        if col in p:
            p[col] = p[col].ffill()
    if "fii_stf_net" in p:
        p["fii_stf_net_chg5"] = p.groupby("symbol")["fii_stf_net"].diff(5)

    # ---------------------------------------------------- labels (t+1 only)
    # NB: p was re-sorted by date above; re-bind locals to the NEW row order
    # (stale references would silently mis-align by index — pandas trap).
    c = p["close"]
    nh, nl, nc = SHIFT("high", -1), SHIFT("low", -1), SHIFT("close", -1)
    p["next_tr_pct"] = 100 * (np.maximum(nh, c) - np.minimum(nl, c)) / c
    p["next_abs_ret"] = 100 * (nc / c - 1).abs()
    p["tr_expand"] = (p["next_tr_pct"] / 100 * c) / p["atr14"]
    # y_move: next-day TR% in top quintile of the day's cross-section
    p["q80"] = p.groupby("date")["next_tr_pct"].transform(
        lambda s: s.quantile(0.80) if len(s) > 30 else np.nan)
    p["y_move"] = np.where(p["q80"].notna(),
                           (p["next_tr_pct"] >= p["q80"]).astype(float), np.nan)

    # ---------------------------------------------------- finalize
    drop = [c0 for c0 in p.columns if c0.startswith(("_", "mx", "r_min", "r5", "r60",
                "ma20", "atr5", "atr20", "q80")) and c0 not in
                ("ma20_dist", "ma20_slope", "atr5_atr20")]
    p.drop(columns=[c0 for c0 in drop if c0 in p.columns], inplace=True)

    if verbose:
        yr = p[p["date"] >= TRAIN_FROM]
        print(f"panel: {len(p)} rows | {p['symbol'].nunique()} symbols | "
              f"{p['date'].min().date()} -> {p['date'].max().date()}")
        print(f"2026 rows: {len(yr)} | y_move base rate: {yr['y_move'].mean():.3f} "
              f"| mean next TR%: {yr['next_tr_pct'].mean():.2f}")
    p.to_csv(PANEL_F, index=False, compression="gzip", float_format="%.6g")
    return p


if __name__ == "__main__":
    build_panel()
