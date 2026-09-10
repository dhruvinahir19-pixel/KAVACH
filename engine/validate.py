"""
validate.py — walk-forward validation of the momentum fingerprint model.

Protocol (mirrors the original research):
  - Features are percentile-ranked per day (kills regime/scale drift)
  - Model retrains monthly on ALL prior 2026 data, scores next month OOS
  - Metrics: monthly AUC, Top-10 hit rate (picks in next-day top-quintile TR),
    average next-day TR% of picks vs universe, capture ratio
  - Baselines: volume-surge top-10, today's-range top-10
Output: output/validation_report.md + metrics.csv + ic_table.csv
"""
import os
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from config import PANEL_F, OUTPUT, TRAIN_FROM, TOP_N, SEED

FEATURES = [
    # price action
    "ret1","ret5","ret10","ret20","range_pct","log_atr14p","atr5_atr20","clv",
    "gap_pct","expansion_today","nr7","inside_day","squeeze","prev_squeeze",
    "dist20h","dist60h","dist252h","ma20_dist","ma20_slope","run_dir","dow",
    # volume & delivery
    "vol_ratio","vol_ratio5","turnov_ratio","trades_ratio","deliv_pct",
    "deliv_ratio","deliv_spike","deliv_x_vol","deliv_up_day",
    # futures
    "fut_oi","foi_chg1","foi_chg5","fut_vol_ratio","doi_norm","basis_pct","poi_state",
    # options
    "pcr_oi","pcr_vol","pcr_chg1","pcr_chg5","opt_oi_chg5","opt_vol_ratio",
    "top3_strike_conc","call_build",
    # market
    "vix","vix_chg1","vix_chg5","nifty_ret1","nifty_pcr","mkt_breadth","days_to_exp",
    # participants
    "fii_stf_net","fii_stf_net_chg5","fii_idf_net","client_stf_net",
]


def load_panel():
    p = pd.read_csv(PANEL_F, parse_dates=["date"])
    p = p[p["date"] >= TRAIN_FROM].copy()          # 2026 research window only
    p = p.dropna(subset=["y_move", "next_tr_pct"])
    return p


def rank_features(p):
    """Percentile-rank each feature within the day's cross-section."""
    r = p.copy()
    for f in FEATURES:
        if f in r.columns:
            r[f] = r.groupby("date")[f].rank(pct=True)
    return r


def top10_hits(sub):
    """Of the day's 10 highest-prob picks, how many were next-day movers?"""
    picks = sub.nlargest(TOP_N, "prob")["y_move"]
    return picks.sum(), len(picks), sub["y_move"].mean()


def main():
    p = load_panel()
    r = rank_features(p)
    r["month"] = r["date"].dt.to_period("M")
    months = sorted(r["month"].unique())
    print(f"{len(r)} rows, {r['date'].min().date()} -> {r['date'].max().date()}, "
          f"{len(months)} months")

    # ------------------------------------------------------------ IC scan
    print("\n== Univariate ICs (Spearman vs tr_expand) ==")
    ics = {}
    for f in FEATURES:
        if f not in r.columns:
            continue
        ic = r.groupby("date").apply(
            lambda d: d[f].corr(d["tr_expand"], method="spearman")
            if d[f].nunique() > 2 else np.nan, include_groups=False).mean()
        ic_bin = r.groupby("date").apply(
            lambda d: d[f].corr(d["y_move"], method="spearman")
            if d[f].nunique() > 2 else np.nan, include_groups=False).mean()
        ics[f] = (ic, ic_bin)
    ic_df = pd.DataFrame(ics, index=["ic_tr_expand", "ic_binary"]).T
    ic_df = ic_df.sort_values("ic_tr_expand", key=abs, ascending=False)
    ic_df.round(3).to_csv(os.path.join(OUTPUT, "ic_table.csv"))
    print(ic_df.head(18).round(3).to_string())

    # ------------------------------------------------------------ walk-forward
    # first 3 months = initial train, then rolling OOS by month
    oos_months = [m for m in months if list(months).index(m) >= 3]
    rows, all_oos = [], []
    for m in oos_months:
        tr = r[r["month"] < m]
        te = r[r["month"] == m].copy()
        if len(tr) < 2000 or te["date"].nunique() < 5:
            continue
        Xtr, ytr = tr[FEATURES], tr["y_move"]
        model = HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.06, max_depth=4,
            min_samples_leaf=60, l2_regularization=1.0, random_state=SEED)
        model.fit(Xtr, ytr)
        te["prob"] = model.predict_proba(te[FEATURES])[:, 1]

        # per-day metrics
        hits, n_picks, base = 0, [], []
        daily = te.groupby("date").apply(
            lambda d: pd.Series({
                "hits": d.nlargest(TOP_N, "prob")["y_move"].sum(),
                "picks": min(TOP_N, len(d)),
                "pick_tr": d.nlargest(TOP_N, "prob")["next_tr_pct"].mean(),
                "univ_tr": d["next_tr_pct"].mean(),
                "vol_surge_hits": d.nlargest(TOP_N, "vol_ratio")["y_move"].sum(),
                "range_hits": d.nlargest(TOP_N, "range_pct")["y_move"].sum(),
            }), include_groups=False)
        auc = roc_auc_score(te["y_move"], te["prob"])
        rows.append({
            "month": str(m), "auc": round(auc, 3),
            "hit_rate": round(daily["hits"].sum() / daily["picks"].sum(), 3),
            "vol_surge_hit": round(daily["vol_surge_hits"].sum() / daily["picks"].sum(), 3),
            "range_hit": round(daily["range_hits"].sum() / daily["picks"].sum(), 3),
            "pick_tr": round(daily["pick_tr"].mean(), 2),
            "univ_tr": round(daily["univ_tr"].mean(), 2),
            "capture": round(daily["pick_tr"].mean() / daily["univ_tr"].mean(), 2),
            "days": len(daily),
        })
        all_oos.append(te)
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUTPUT, "metrics.csv"), index=False)
    print("\n== Walk-forward OOS (monthly retrain, 2026 only) ==")
    print(res.to_string(index=False))
    if len(res):
        agg_picks = res["hit_rate"].mean()
        print(f"\nMEAN OOS hit rate: {agg_picks:.1%} (base 20%) | "
              f"mean AUC {res['auc'].mean():.3f} | "
              f"capture {res['capture'].mean():.2f}x")

    # ------------------------------------------------------------ report
    with open(os.path.join(OUTPUT, "validation_report.md"), "w") as f:
        f.write("# Walk-Forward Validation — 2026 Rebuild\n\n```\n"
                + res.to_string(index=False)
                + f"\n\nMean OOS top-10 hit rate: {agg_picks:.1%} | base 20%"
                + "\n```\n\n## Top-18 feature ICs\n```\n"
                + ic_df.head(18).round(3).to_string() + "\n```\n")
    # stash OOS predictions for later analysis
    if all_oos:
        pd.concat(all_oos)[["date", "symbol", "prob", "y_move", "next_tr_pct"]
                           ].to_csv(os.path.join(OUTPUT, "oos_predictions.csv.gz"),
                                    index=False, compression="gzip")
    print("saved: output/metrics.csv, ic_table.csv, validation_report.md, "
          "oos_predictions.csv.gz")


if __name__ == "__main__":
    main()
