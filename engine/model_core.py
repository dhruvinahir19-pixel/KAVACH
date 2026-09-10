"""
model_core.py — THE model definition (single source of truth).

FEATURES list, per-day percentile ranking, exact hyperparameters, train/predict.
Shared byte-identically by research (validate.py) and the live scanner.
DO NOT fork this logic; parity tests enforce it.
"""
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from config import SEED


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


def rank_features(p):
    """Percentile-rank each feature within the day's cross-section."""
    r = p.copy()
    for f in FEATURES:
        if f in r.columns:
            r[f] = r.groupby("date")[f].rank(pct=True)
    return r

def train_model(r, seed=SEED):
    """Fit the production classifier on a ranked frame (rank_features output)."""
    m = HistGradientBoostingClassifier(max_iter=250, learning_rate=0.06,
                                       max_depth=4, min_samples_leaf=60,
                                       l2_regularization=1.0, random_state=seed)
    m.fit(r[FEATURES], r["y_move"])
    return m


def predict_scores(model, r):
    """Probability of y_move=1 for each row of a ranked frame."""
    return model.predict_proba(r[FEATURES])[:, 1]
