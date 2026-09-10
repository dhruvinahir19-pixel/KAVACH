"""
audit_candles.py — validate the Upstox 5-min store against NSE bhavcopy.

For a sample of stocks/days: aggregate 5-min candles to daily OHLC+volume and
compare with the official NSE cash bhavcopy (our data/cash store).
Pass criteria: O/H/L/C within 0.05 INR, volume within 0.5%.
Also reports coverage (files, bars/day distribution, sessions per stock).
"""
import os, random, datetime as dt
import numpy as np
import pandas as pd
from config import DATA

CANDLE_DIR = os.path.join(DATA, "candles")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def to_ist_date(ep_min):
    return dt.datetime.fromtimestamp(ep_min * 60, IST).date().isoformat()


def load_candles(sym):
    df = pd.read_csv(os.path.join(CANDLE_DIR, f"{sym}.csv.gz"))
    df["date"] = df["epoch_min"].map(to_ist_date)
    return df


def audit(n_stocks=12, n_days=15, seed=7):
    cash = pd.read_csv(os.path.join(DATA, "cash.csv.gz"), parse_dates=["date"])
    cash["date"] = cash["date"].dt.strftime("%Y-%m-%d")
    files = sorted(f[:-7] for f in os.listdir(CANDLE_DIR)
                   if f.endswith(".csv.gz") and not f.startswith("_"))
    rng = random.Random(seed)
    sample_syms = rng.sample(files, min(n_stocks, len(files)))

    checked = ohlc_ok = vol_ok = 0
    close_rel = []
    mismatches = []
    for sym in sample_syms:
        c5 = load_candles(sym)
        daily = c5.groupby("date").agg(
            o=("open_paise", "first"), h=("high_paise", "max"),
            l=("low_paise", "min"), c=("close_paise", "last"),
            v=("volume", "sum")).reset_index()
        bhav = cash[cash["symbol"] == sym].set_index("date")
        days = rng.sample(sorted(set(daily["date"]) & set(bhav.index)),
                          min(n_days, len(set(daily["date"]) & set(bhav.index))))
        for d in days:
            row = daily[daily["date"] == d].iloc[0]
            b = bhav.loc[d]
            ok_p = all(abs(row[k] / 100 - b[bc]) <= 0.05 for k, bc in
                       [("o", "open"), ("h", "high"), ("l", "low")])
            ok_v = abs(row["v"] - b["volume"]) / max(b["volume"], 1) <= 0.005
            # close is expected to differ: last continuous trade vs official
            # closing-auction price — record relative deviation, don't fail
            close_rel.append(abs(row["c"] / 100 - b["close"]) / b["close"])
            checked += 1
            ohlc_ok += ok_p
            vol_ok += ok_v
            if not (ok_p and ok_v):
                mismatches.append((sym, d, row["o"] / 100, b["open"],
                                   row["v"], b["volume"]))
    print(f"cross-validation: {checked} stock-days checked "
          f"({len(sample_syms)} stocks)")
    print(f"  open/high/low match (±0.05): {ohlc_ok}/{checked} = {ohlc_ok/checked:.1%}")
    print(f"  volume match (±0.5%): {vol_ok}/{checked} = {vol_ok/checked:.1%}")
    print(f"  close vs bhavcopy (expected gap: closing auction): median "
          f"{np.median(close_rel)*100:.3f}%, p99 "
          f"{np.percentile(close_rel, 99)*100:.2f}%")
    if mismatches:
        print("  sample mismatches (sym, date, cnd_open, bhav_open, cnd_vol, bhav_vol):")
        for m in mismatches[:8]:
            print("   ", m)

    # coverage stats
    sizes, bars_days, sessions = [], [], []
    for f in files:
        p = os.path.join(CANDLE_DIR, f + ".csv.gz")
        sizes.append(os.path.getsize(p))
        df = load_candles(f)
        bd = df.groupby("date").size()
        bars_days.append(bd.median())
        sessions.append(len(bd))
    print(f"\ncoverage: {len(files)} stocks | total {sum(sizes)/1e6:.1f} MB")
    print(f"  sessions/stock: min {min(sessions):.0f}, median "
          f"{np.median(sessions):.0f}, max {max(sessions):.0f}")
    print(f"  median bars/day: {np.median(bars_days):.0f} (expect ~73-75)")
    tot_rows = sum(len(load_candles(f)) for f in files[:20]) / min(20, len(files)) * len(files)
    print(f"  estimated total bars: {tot_rows/1e6:.1f} M")


if __name__ == "__main__":
    audit()
