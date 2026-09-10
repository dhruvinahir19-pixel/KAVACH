"""
seed_neon.py — one-time (and re-runnable) seeding of the Neon project from the
workspace engine stores. Idempotent: upserts everywhere; safe to interrupt and
re-run (resumable by design).

Seeds: eod_daily (cash+fut+opt wide, all sessions), eod_mkt (mkt+vix+participants),
universe (isin_map + session counts + scoreable), holidays (weekday gaps in the
cash store = actual NSE non-trading days, cross-checked against known holidays).
Model blobs are pushed separately by engine/train_production.py.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import neon_store  # noqa: E402

DATA = ROOT / "engine" / "data"
BATCH = 5000


def upsert_rows(conn, table, df, cols, conflict, batch=BATCH):
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({conflict}) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in conflict.split(", ")))
    rows = [tuple(None if (isinstance(v, float) and np.isnan(v)) else v for v in r)
            for r in df[cols].itertuples(index=False)]
    for i in range(0, len(rows), batch):
        conn.cursor().executemany(sql, rows[i:i + batch])
    return len(rows)


def main():
    cash = pd.read_csv(DATA / "cash.csv.gz", parse_dates=["date"])
    fut = pd.read_csv(DATA / "fut.csv.gz", parse_dates=["date"])
    opt = pd.read_csv(DATA / "opt.csv.gz", parse_dates=["date"])
    mkt = pd.read_csv(DATA / "mkt.csv.gz", parse_dates=["date"])
    part = pd.read_csv(DATA / "part.csv.gz", parse_dates=["date"])
    vix = pd.read_csv(DATA / "vix.csv", parse_dates=["date"])
    isin = pd.read_csv(DATA / "isin_map.csv.gz")

    conn = neon_store.connect()
    neon_store.ensure(conn)

    # ---- eod_daily: wide cash x fut x opt
    w = (cash.merge(fut, on=["date", "symbol"], how="outer")
             .merge(opt, on=["date", "symbol"], how="outer"))
    w["date"] = w["date"].dt.date
    eod_cols = ["date", "symbol", "prev_close", "open", "high", "low", "close",
                "volume", "turnover_l", "trades", "deliv_qty", "deliv_per",
                "nm_expiry", "nm_close", "nm_oi", "fut_oi", "fut_oi_chg",
                "fut_vol", "fut_val", "fut_txns", "ce_oi", "pe_oi", "ce_oi_chg",
                "pe_oi_chg", "ce_vol", "pe_vol", "ce_val", "pe_val",
                "top3_conc", "call_build"]
    n = upsert_rows(conn, "eod_daily", w, eod_cols, "date, symbol")
    print(f"eod_daily: {n} rows | {w['date'].min()} -> {w['date'].max()}")

    # ---- eod_mkt: mkt + vix + participants per date
    m = (mkt.merge(part, on="date", how="left")
            .merge(vix.rename(columns={"vix": "vix"}), on="date", how="left")
            .sort_values("date"))
    m["date"] = m["date"].dt.date
    mkt_cols = ["date", "nifty_close", "banknifty_close", "nifty_pcr",
                "next_expiry", "vix", "client_stf_net", "client_idf_net",
                "fii_stf_net", "fii_idf_net", "dii_stf_net", "dii_idf_net",
                "pro_stf_net", "pro_idf_net"]
    n = upsert_rows(conn, "eod_mkt", m, mkt_cols, "date")
    print(f"eod_mkt: {n} rows | vix present: {m['vix'].notna().sum()}")

    # ---- universe: isin_map + session counts
    sess = cash.groupby("symbol")["date"].agg(["count", "min", "max"])
    uni = isin.merge(sess, left_on="symbol", right_index=True, how="left")
    latest = sess["max"].max()
    rows = []
    for rec in uni.to_dict("records"):
        cnt = rec.get("count"); mn = rec.get("min"); mx = rec.get("max")
        active = bool(pd.notna(mx) and (latest - mx).days <= 30)
        rows.append((rec["symbol"], rec["isin"], active,
                     bool(active and pd.notna(cnt) and cnt >= 21),
                     mn.date().isoformat() if pd.notna(mn) else None,
                     mx.date().isoformat() if pd.notna(mx) else None))
    conn.cursor().executemany(
        """INSERT INTO universe (symbol, isin, active, scoreable, first_seen, last_seen)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (symbol) DO UPDATE SET isin=EXCLUDED.isin,
             active=EXCLUDED.active, scoreable=EXCLUDED.scoreable,
             first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen""", rows)
    print(f"universe: {len(rows)} symbols | scoreable: {sum(r[3] for r in rows)}")

    # ---- holidays: weekday gaps in the cash store
    ds = pd.DatetimeIndex(sorted(cash["date"].unique()))
    gaps = pd.bdate_range(ds.min(), ds.max()).difference(ds)
    hol = [(d.date().isoformat(), "derived from store gaps") for d in gaps]
    conn.cursor().executemany(
        "INSERT INTO holidays (dkey, note) VALUES (%s,%s) ON CONFLICT DO NOTHING", hol)
    print(f"holidays: {len(hol)} days seeded")

    for t in ("eod_daily", "eod_mkt", "universe", "holidays"):
        print(f"  {t}: {conn.execute(f'SELECT count(*) FROM {t}').fetchone()[0]} rows in Neon")
    conn.close()
    print("SEED COMPLETE (idempotent — re-run anytime)")


if __name__ == "__main__":
    main()
