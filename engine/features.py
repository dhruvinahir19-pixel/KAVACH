"""
features.py — research wrapper: load EOD stores, build the panel via
features_core.build_features (the shared single source of truth), save.

Input : data/{cash,fut,opt,part,mkt}.csv.gz + data/vix.csv
Output: data/panel.csv.gz
"""
import os
import pandas as pd
from config import CASH_F, FUT_F, OPT_F, PART_F, MKT_F, VIX_F, PANEL_F
from features_core import build_features


def build_panel(verbose=True, save=True):
    cash = pd.read_csv(CASH_F, parse_dates=["date"])
    fut = pd.read_csv(FUT_F, parse_dates=["date"])
    opt = pd.read_csv(OPT_F, parse_dates=["date"])
    part = pd.read_csv(PART_F, parse_dates=["date"])
    mkt = pd.read_csv(MKT_F, parse_dates=["date"])
    vix = pd.read_csv(VIX_F, parse_dates=["date"])

    p = build_features(cash, fut, opt, part, mkt, vix)

    if verbose:
        print(f"panel: {len(p)} rows | {p['symbol'].nunique()} symbols | "
              f"{p['date'].min().date()} -> {p['date'].max().date()}")
    if save:
        p.to_csv(PANEL_F, index=False, compression="gzip", float_format="%.6g")
    return p


if __name__ == "__main__":
    build_panel()
