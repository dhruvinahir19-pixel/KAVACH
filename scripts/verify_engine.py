#!/usr/bin/env python3
"""
verify_engine.py -- the reproduction gate for KAVACH-945.

A fresh checkout of this repo (plus engine/data/ rebuilt per the master report section 11,
and /tmp/c25 holding the 2025-04..12 5-min candles for stop-path simulation) must
reproduce the locked Stage-5/6 backtest numbers EXACTLY. Any mismatch means the code or
data pipeline drifted. Exits non-zero on failure.

Usage: python3 scripts/verify_engine.py

DATA TRAP (P0-05): importing any engine module creates an EMPTY engine/data/ directory.
If it already exists, copy data CONTENTS, not the folder:  cp -r /path/to/data/* engine/data/
2025 stop-path candles must be at /tmp/c25 (see FAILURE_MODES.md P0-04 for /tmp persistence).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(os.path.dirname(HERE), "engine")
sys.path.insert(0, ENGINE)

from protocol_v2 import run  # noqa: E402

EXPECTED = {
    "trades": 986,
    "mornings": 346,
    "stopped": 453,
    "worst_day_pct": -4.80,
    "worst_trade_pct": -1.20,
}
TOL_PCT = 0.15  # percentage-point tolerance for path-dependent stop fills

def main() -> int:
    book, d = run(verbose=True)
    dd = d[~d["flagged"]]
    dly = dd.groupby("date")["net"].sum()
    checks = [
        ("flagged rows (missing 2025 bars)", float(d["flagged"].sum()), 0.0, 0.0),
        ("trades", float(len(book)), EXPECTED["trades"], 0.0),
        ("mornings", float(book["date"].nunique()), EXPECTED["mornings"], 0.0),
        ("stopped", float(dd["stopped"].sum()), EXPECTED["stopped"], 0.0),
        ("worst day %", round(dly.min() * 100, 2), EXPECTED["worst_day_pct"], TOL_PCT),
        ("worst trade %", round(dd["net"].min() * 100, 2), EXPECTED["worst_trade_pct"], TOL_PCT),
    ]
    failed = False
    print("\n===== REPRODUCTION GATE =====")
    for name, got, want, tol in checks:
        ok = abs(got - want) <= tol
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<34} got {got:>9}  expected {want}")
    print("GATE:", "ALL GREEN" if not failed else "FAILED - DO NOT TRUST THIS CHECKOUT")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
