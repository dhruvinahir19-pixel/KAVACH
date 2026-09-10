"""
scanner.py — PRODUCTION: refresh latest data, retrain on all 2026 sessions,
score tonight's close, emit tomorrow's Top-10 watchlist.

Usage (after ~6:30 PM IST on a trading day):
    python3 scanner.py            # refresh + score
    python3 scanner.py --no-refresh   # score on stored data only
"""
import os, sys, datetime as dt
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from config import DATA, OUTPUT, TOP_N, SEED, TRAIN_FROM
from validate import FEATURES, rank_features
import backfill, features


def refresh_latest():
    """Harvest the latest closeable session (walk back from today)."""
    backfill.main()          # resume-safe: fills any missing days incl. today
    features.build_panel(verbose=False)


def fingerprints(row):
    """Human-readable fingerprint flags, as in the original research."""
    g = lambda k: row[k]           # bracket access — avoids Series-method collisions
    fp = []
    if g("vol_ratio") >= 1.3: fp.append(f"VOL-SURGE {g('vol_ratio'):.1f}x")
    if g("trades_ratio") >= 1.3: fp.append("TRADE-SURGE")
    if g("deliv_ratio") >= 1.2 and g("deliv_x_vol") >= 1.44:
        fp.append(f"DELIV-ACCUM {g('deliv_ratio'):.1f}x")
    if g("squeeze") <= 0.8: fp.append("SQUEEZE")
    if g("expansion_today") >= 1.3: fp.append("RANGE-EXP T0")
    if g("foi_chg5") >= 0.04: fp.append(f"FUT-OI+{g('foi_chg5')*100:.0f}%")
    elif g("foi_chg5") <= -0.04: fp.append(f"FUT-OI{g('foi_chg5')*100:.0f}%")
    if g("opt_vol_ratio") >= 1.3: fp.append("OPT-ACTIVITY")
    if g("top3_strike_conc") >= 0.45: fp.append("STRIKE-PIN")
    if g("dist252h") >= -0.02: fp.append("@52W-HIGH")
    pcr5 = g("pcr_chg5")
    if pcr5 == pcr5 and abs(pcr5) >= 0.15: fp.append("PCR-SHIFT")  # NaN-safe
    return " · ".join(fp) if fp else "—"


def main(refresh=True):
    if refresh:
        print("refreshing data ...")
        refresh_latest()

    p = pd.read_csv(os.path.join(DATA, "panel.csv.gz"), parse_dates=["date"])
    p = p[p["date"] >= TRAIN_FROM]

    # retrain on all labelled sessions
    labelled = p.dropna(subset=["y_move", "next_tr_pct"])
    r = rank_features(labelled)
    model = HistGradientBoostingClassifier(
        max_iter=250, learning_rate=0.06, max_depth=4,
        min_samples_leaf=60, l2_regularization=1.0, random_state=SEED)
    model.fit(r[FEATURES], r["y_move"])

    # score the LATEST session (labels not yet known — that's the point)
    latest_date = p["date"].max()
    today = p[p["date"] == latest_date].copy()
    tr_ranked = rank_features(today)
    today["prob"] = model.predict_proba(tr_ranked[FEATURES])[:, 1]
    top = today.nlargest(TOP_N, "prob").copy()

    tgt_date = latest_date + pd.Timedelta(days=1)
    out = pd.DataFrame({
        "rank": range(1, len(top) + 1),
        "symbol": top["symbol"],
        "close": top["close"].round(2),
        "prob": top["prob"].round(3),
        "vol_x": top["vol_ratio"].round(2),
        "deliv_pct": top["deliv_pct"].round(0),
        "deliv_x": top["deliv_ratio"].round(2),
        "squeeze": top["squeeze"].round(2),
        "range_x_atr": top["expansion_today"].round(2),
        "atr_pct": (top["atr14"] / top["close"] * 100).round(2),
        "fut_oi_5d": (top["foi_chg5"] * 100).round(0),
        "pcr": top["pcr_oi"].round(2),
        "fingerprints": [fingerprints(r) for _, r in top.iterrows()],
    })

    csv_path = os.path.join(OUTPUT, f"watchlist_{tgt_date.date()}.csv")
    out.to_csv(csv_path, index=False)

    md = [f"# Top-{TOP_N} Momentum Watchlist — for session {tgt_date.date()}",
          f"*scored on {latest_date.date()} close · model retrained on all sessions "
          f"from {TRAIN_FROM} to {latest_date.date()} ({p['date'].nunique()} sessions)*",
          "", "```", out.to_string(index=False), "```", "",
          "Fingerprints: VOL/TRADE-SURGE = activity vs 20d norm · DELIV-ACCUM = "
          "delivery×volume · SQUEEZE = 5d/60d range compression · RANGE-EXP T0 = "
          "today's TR/ATR · FUT-OI = 5d futures OI change · OPT-ACTIVITY = option "
          "premium turnover surge · STRIKE-PIN = top-3 strike OI concentration · "
          "@52W-HIGH = within 2% of period high · PCR-SHIFT = 5d put-call ratio move",
          "", "_Research tool — predicts movement, NOT direction. Not investment advice._"]
    md_path = os.path.join(OUTPUT, f"watchlist_{tgt_date.date()}.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))

    print(f"\n=== TOP {TOP_N} for {tgt_date.date()} (from {latest_date.date()} close) ===")
    print(out[["rank", "symbol", "close", "prob", "vol_x", "deliv_pct",
               "fingerprints"]].to_string(index=False))
    print(f"\nsaved: {csv_path}\n       {md_path}")

    # append the validated direction layer (basis tilt)
    try:
        import direction as dmod
        dmod.annotate_latest()
    except Exception as e:
        print("direction annotation skipped:", e)
    return out


if __name__ == "__main__":
    main(refresh="--no-refresh" not in sys.argv)
