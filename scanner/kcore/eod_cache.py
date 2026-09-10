"""
eod_cache.py — in-memory EOD frame cache over Neon (the scanner's data layer).

Boot: one bounded pull of eod_daily + eod_mkt (all seeded sessions, ~27 MB) ->
the SIX engine-store-schema frames (cash/fut/opt/part/mkt/vix), dtype-faithful.
Daily: append_day() adds one harvested day (~60 KB) — egress discipline (F-07):
big pull only at boot; evenings are delta-only.

The frames feed features_core.build_features EXACTLY like the engine's stores —
same column names, same dtypes. Parity is enforced by tests/test_parity.py.
"""
import threading

import pandas as pd

from . import neon_store

CASH_COLS = ["prev_close", "open", "high", "low", "close", "volume",
             "turnover_l", "trades", "deliv_qty", "deliv_per"]
FUT_COLS = ["nm_expiry", "nm_close", "nm_oi", "fut_oi", "fut_oi_chg",
            "fut_vol", "fut_val", "fut_txns"]
OPT_COLS = ["ce_oi", "pe_oi", "ce_oi_chg", "pe_oi_chg", "ce_vol", "pe_vol",
            "ce_val", "pe_val", "top3_conc", "call_build"]
PART_COLS = ["client_stf_net", "client_idf_net", "fii_stf_net", "fii_idf_net",
             "dii_stf_net", "dii_idf_net", "pro_stf_net", "pro_idf_net"]
MKT_COLS = ["nifty_close", "banknifty_close", "nifty_pcr", "next_expiry"]


class EodCache:
    def __init__(self, url, sessions=480):
        self.url = url
        self.sessions = sessions
        self._frames = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ boot
    def boot(self):
        conn = neon_store.connect(self.url)
        neon_store.ensure(conn)
        rows = conn.execute(
            "SELECT date, symbol, " + ", ".join(CASH_COLS + FUT_COLS + OPT_COLS)
            + " FROM eod_daily ORDER BY date, symbol").fetchall()
        mrows = conn.execute(
            "SELECT date, " + ", ".join(MKT_COLS + ["vix"] + PART_COLS)
            + " FROM eod_mkt ORDER BY date").fetchall()
        conn.close()

        eod = pd.DataFrame(rows, columns=["date", "symbol"] + CASH_COLS + FUT_COLS + OPT_COLS)
        eod["date"] = pd.to_datetime(eod["date"])
        mkt_all = pd.DataFrame(mrows, columns=["date"] + MKT_COLS + ["vix"] + PART_COLS)
        mkt_all["date"] = pd.to_datetime(mkt_all["date"])

        # trim to the tail window (memory bound on the 512 MB box)
        if self.sessions and eod["date"].nunique() > self.sessions:
            keep_dates = sorted(eod["date"].unique())[-self.sessions:]
            eod = eod[eod["date"].isin(keep_dates)]
            mkt_all = mkt_all[mkt_all["date"].isin(keep_dates)]

        f = {
            "cash": eod[["date", "symbol"] + CASH_COLS].copy(),
            "fut": eod[["date", "symbol"] + FUT_COLS].copy(),
            "opt": eod[["date", "symbol"] + OPT_COLS].copy(),
            "part": mkt_all[["date"] + PART_COLS].copy(),
            "mkt": mkt_all[["date"] + MKT_COLS].copy(),
            "vix": mkt_all[["date", "vix"]].copy(),
        }
        with self._lock:
            self._frames = f
        return f

    @property
    def frames(self):
        with self._lock:
            if self._frames is None:
                raise RuntimeError("EodCache.boot() must run before use")
            return self._frames

    # ------------------------------------------------------------------ delta
    def append_day(self, day, vix_row):
        """day: harvest_day() dict (cash/fut/opt frames + part/mkt dicts);
        vix_row: {'date': iso, 'vix': float} or None (already present)."""
        with self._lock:
            f = self._frames
            if f is None:
                raise RuntimeError("boot() first")
            d = pd.to_datetime(day["date_iso"])

            def app(df, new):
                new = new.copy()
                new["date"] = pd.to_datetime(new["date"])
                both = pd.concat([df, new], ignore_index=True)
                return both.drop_duplicates(subset=["date", "symbol"], keep="last") \
                           .sort_values(["symbol", "date"]).reset_index(drop=True) \
                    if "symbol" in both.columns else \
                    both.drop_duplicates(subset=["date"], keep="last") \
                        .sort_values("date").reset_index(drop=True)

            f["cash"] = app(f["cash"], day["cash"])
            f["fut"] = app(f["fut"], day["fut"])
            f["opt"] = app(f["opt"], day["opt"])
            f["part"] = app(f["part"], pd.DataFrame([day["part"]]))
            f["mkt"] = app(f["mkt"], pd.DataFrame([day["mkt"]]))
            if vix_row:
                f["vix"] = app(f["vix"], pd.DataFrame([vix_row]))
        return self.frames


_cache_singleton = None


def get_cache(url):
    global _cache_singleton
    if _cache_singleton is None:
        _cache_singleton = EodCache(url)
        _cache_singleton.boot()
    return _cache_singleton
