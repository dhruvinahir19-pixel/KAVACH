"""
backfill.py — harvest every trading day from HARVEST_FROM to latest.
Resume-safe: skips dates already stored. Raw files never touch the workspace.
Usage:  python3 backfill.py            (from engine/)
"""
import os, sys, datetime as dt, time
import pandas as pd
from config import (HARVEST_FROM, CASH_F, FUT_F, OPT_F, PART_F, MKT_F, VIX_F)
from harvest import harvest_day, fetch_vix


def daterange(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5:          # skip Sat/Sun; holidays 404 naturally
            yield d
        d += dt.timedelta(days=1)


def load_stores():
    st = {}
    for name, path in [("cash", CASH_F), ("fut", FUT_F), ("opt", OPT_F),
                       ("part", PART_F), ("mkt", MKT_F)]:
        st[name] = pd.read_csv(path) if os.path.exists(path) else pd.DataFrame()
    return st


def save_stores(st):
    for name, path in [("cash", CASH_F), ("fut", FUT_F), ("opt", OPT_F),
                       ("part", PART_F), ("mkt", MKT_F)]:
        st[name].to_csv(path, index=False, compression="gzip")


def main():
    start = dt.date.fromisoformat(HARVEST_FROM)
    end = dt.date.today()
    st = load_stores()
    done = (set(st["cash"]["date"]) if len(st["cash"]) else set())
    todo = [d for d in daterange(start, end) if d.isoformat() not in done]
    print(f"store: {len(done)} days already | to harvest: {len(todo)} days "
          f"({todo[0]} -> {todo[-1]})" if todo else "store: nothing to do")

    n_ok, n_miss, checkpoint = 0, 0, 15
    for d in todo:
        try:
            res = harvest_day(d)
        except Exception as e:
            print(f"  !! {d}: ERROR {e}")
            res = None
        if res is None:
            n_miss += 1
        else:
            n_ok += 1
            st["cash"] = pd.concat([st["cash"], res["cash"]], ignore_index=True)
            st["fut"] = pd.concat([st["fut"], res["fut"]], ignore_index=True)
            st["opt"] = pd.concat([st["opt"], res["opt"]], ignore_index=True)
            st["part"] = pd.concat([st["part"], pd.DataFrame([res["part"]])],
                                   ignore_index=True)
            st["mkt"] = pd.concat([st["mkt"], pd.DataFrame([res["mkt"]])],
                                  ignore_index=True)
        if (n_ok + n_miss) % checkpoint == 0:
            save_stores(st)
            print(f"  ... {n_ok} harvested, {n_miss} non-trading | through {d}")
        time.sleep(0.35)                       # be polite to NSE

    save_stores(st)

    # VIX full-range refresh
    vix = fetch_vix(start, end)
    vix.to_csv(VIX_F, index=False)

    print(f"DONE: +{n_ok} trading days ({n_miss} skipped), "
          f"cash rows={len(st['cash'])}, VIX rows={len(vix)}")
    # space report
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(os.path.dirname(CASH_F)) for f in fs)
    print(f"data dir size: {total/1e6:.2f} MB")


if __name__ == "__main__":
    main()
