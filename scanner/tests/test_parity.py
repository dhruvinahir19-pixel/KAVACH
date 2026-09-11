"""P2-06 PARITY: scanner path (Neon -> cache -> features_core -> model) must
reproduce the workspace-research fixture EXACTLY (tol 1e-6 on probs; the
Neon DOUBLE PRECISION roundtrip introduces ~5e-8 noise). Live Neon required."""
import json
import os
import pickle
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import neon_store                     # noqa: E402
from kcore.eod_cache import EodCache             # noqa: E402
from features_core import build_features         # noqa: E402
from model_core import FEATURES, rank_features   # noqa: E402
from evening import load_model                   # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEON_DATABASE_URL"), reason="live NEON_DATABASE_URL not set")

FIXTURE = json.load(open(Path(__file__).parent / "fixtures" / "parity_20260909.json"))
TOL = 1e-6


def test_parity_fixture():
    conn = neon_store.connect()
    version, trained_through, model = load_model(conn)
    conn.close()
    assert version == FIXTURE["model_version"], f"model changed: {version}"

    # FULL history, then hard truncate at the fixture date. A trailing-N window
    # (sessions=480) anchors at Neon's max date and slides forward every time a
    # new session lands — the fixture froze the research window ending
    # 2026-09-09, so any later date in eod_daily evicts the oldest session and
    # silently drifts rank features (observed 2026-09-11: 09-10 + a stray
    # 2099-01-05 test row slid the window and failed parity with 7e-3 drift).
    cache = EodCache(os.environ["NEON_DATABASE_URL"], sessions=None)
    cache.boot()
    f = cache.frames
    f = {k: v[v["date"] <= FIXTURE["date"]].copy() for k, v in f.items()}
    panel = build_features(f["cash"], f["fut"], f["opt"], f["part"], f["mkt"], f["vix"])
    today = panel[panel["date"] == FIXTURE["date"]]
    cand = set(FIXTURE["candidate_set"])
    today = today[today["symbol"].isin(cand)].copy()
    assert len(today) == FIXTURE["n_scored"], (
        f"candidate count drifted: {len(today)} vs {FIXTURE['n_scored']}")

    r = rank_features(today)
    today["prob"] = model.predict_proba(r[FEATURES])[:, 1]

    worst_sym, worst = None, 0.0
    for row in today.itertuples():
        want = FIXTURE["all_probs"].get(row.symbol)
        assert want is not None, f"unexpected symbol in cross-section: {row.symbol}"
        d = abs(row.prob - want)
        if d > worst:
            worst_sym, worst = row.symbol, d
    assert worst <= TOL, f"max prob drift {worst:.2e} on {worst_sym} (> {TOL})"

    got_top = list(today.nlargest(10, "prob")["symbol"])
    want_top = [t["symbol"] for t in FIXTURE["top10"]]
    assert got_top == want_top, f"top-10 order changed:\n{got_top}\nvs\n{want_top}"
    print(f"PARITY OK: {len(today)} candidates, max drift {worst:.2e}, top-10 identical")


def test_feature_sample_values():
    """Granular diagnosis anchor: 5 symbols x 56 features vs fixture."""
    conn = neon_store.connect()
    _, _, model = load_model(conn)
    conn.close()
    cache = EodCache(os.environ["NEON_DATABASE_URL"], sessions=None)
    cache.boot()
    f = {k: v[v["date"] <= FIXTURE["date"]].copy() for k, v in cache.frames.items()}
    panel = build_features(f["cash"], f["fut"], f["opt"], f["part"], f["mkt"], f["vix"])
    today = panel[panel["date"] == FIXTURE["date"]]
    for sym, feats in FIXTURE["feature_sample"].items():
        row = today[today["symbol"] == sym]
        assert len(row) == 1, f"{sym} missing from cross-section"
        for k, want in feats.items():
            got = row[k].iloc[0]
            if want is None:
                assert pd_isna(got), f"{sym}.{k}: expected NaN, got {got}"
            else:
                # 1e-6 (not 1e-9): NSE revises bhavcopy files post-publication
                # (observed: TURNOVER_LACS 83740.9 -> 83740.87 for 2026-09-09),
                # so raw inputs can drift at ~1e-7 relative after a revision.
                assert abs(float(got) - want) < 1e-6, f"{sym}.{k}: {got} vs {want}"


def pd_isna(v):
    import math
    return v is None or (isinstance(v, float) and math.isnan(v))
