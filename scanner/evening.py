"""
evening.py — P2: the evening pipeline job.

Chain (per approved P2 design): gates -> harvest (engine's own fetch+parse) ->
sanity gates -> VIX (fail-loud, D2) -> idempotent Neon upsert -> universe
maintenance (F-15) -> features (features_core, single source of truth) -> model
score (blob from Neon, strictly OOS) -> top-10 watchlist -> Telegram (D4 format)
-> watchlist rows to Neon.

Schedule: cron triggers at 20:00/20:30/21:00/21:30/22:00 IST; each trigger is one
short attempt; 'bhavcopy not posted' attempts finish 'skipped' (retry via next
trigger); after 21:55 an absent bhavcopy is an unexpected FAILURE (P2-11).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))          # engine code (shared truth)
sys.path.insert(0, str(ROOT / "scanner"))

from kcore import clock, neon_store               # noqa: E402
from kcore.eod_cache import get_cache             # noqa: E402
import harvest as engine_harvest                  # noqa: E402
from features_core import build_features          # noqa: E402
from model_core import FEATURES, rank_features    # noqa: E402

TOP_N = 10
SCOREABLE_MIN_SESSIONS = 21
SANITY = {"cash_min": 200, "fut_min": 100, "opt_min": 100}
FEATURE_WINDOW_SESSIONS = 300      # trim before build_features (memory bound)


# ---------------------------------------------------------------- helpers
def _norm_day(day, dstr):
    """harvest_day output -> cache-appendable dict with datetime frames and
    NUMERIC dtypes matching the engine store contract. The engine's csv.gz
    round-trip coerced bhavcopy strings ('-' etc.) to float/NaN implicitly;
    the scanner passes frames directly, so the coercion is explicit here.
    pd.to_numeric(errors='coerce') replicates read_csv semantics exactly."""
    from kcore.eod_cache import CASH_COLS, FUT_COLS, OPT_COLS
    out = {"date_iso": dstr, "part": dict(day["part"]), "mkt": dict(day["mkt"])}
    for k, cols in (("cash", CASH_COLS), ("fut", FUT_COLS[1:]), ("opt", OPT_COLS)):
        df = day[k].copy()
        df["date"] = pd.to_datetime(df["date"])
        for c in cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        out[k] = df
    out["part"]["date"] = dstr
    out["mkt"]["date"] = dstr
    return out


def load_holidays(conn):
    rows = conn.execute("SELECT dkey FROM holidays").fetchall()
    return frozenset(r[0] for r in rows)


def load_model(conn):
    """Latest model blob from Neon with sha256 verification (P2-04)."""
    import hashlib
    import pickle
    row = conn.execute(
        "SELECT version, trained_through, payload, sha256 FROM model_blob "
        "ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        raise RuntimeError("no model blob in Neon — run engine/train_production.py")
    version, trained_through, payload, sha = row
    if hashlib.sha256(payload).hexdigest() != sha:
        raise RuntimeError(f"model blob sha256 mismatch for {version} — corruption")
    return version, trained_through, pickle.loads(payload)


def upsert_day(conn, day, vix_val):
    """Idempotent write of one harvested day (all-or-nothing per table)."""
    dstr = day["cash"]["date"].iloc[0].date().isoformat()
    wide = (day["cash"].merge(day["fut"], on=["date", "symbol"], how="outer")
                        .merge(day["opt"], on=["date", "symbol"], how="outer"))
    wide["date"] = wide["date"].dt.date
    EOD_COLS = ["date", "symbol", "prev_close", "open", "high", "low", "close",
                "volume", "turnover_l", "trades", "deliv_qty", "deliv_per",
                "nm_expiry", "nm_close", "nm_oi", "fut_oi", "fut_oi_chg",
                "fut_vol", "fut_val", "fut_txns", "ce_oi", "pe_oi", "ce_oi_chg",
                "pe_oi_chg", "ce_vol", "pe_vol", "ce_val", "pe_val",
                "top3_conc", "call_build"]
    rows = [tuple(None if (isinstance(v, float) and np.isnan(v)) else v for v in r)
            for r in wide[EOD_COLS].itertuples(index=False)]
    ph = ", ".join(["%s"] * len(EOD_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in EOD_COLS[2:])
    upsert_sql = (f"INSERT INTO eod_daily ({', '.join(EOD_COLS)}) VALUES ({ph}) "
                  f"ON CONFLICT (date, symbol) DO UPDATE SET {updates}")
    with conn.transaction():
        conn.cursor().executemany(upsert_sql, rows)
        m = dict(day["mkt"]); m["date"] = dstr
        p = dict(day["part"]); p["date"] = dstr
        mrow = {"date": dstr,
                **{k: m.get(k) for k in ["nifty_close", "banknifty_close",
                                         "nifty_pcr", "next_expiry"]},
                "vix": vix_val,
                **{k: p.get(k) for k in ["client_stf_net", "client_idf_net",
                                         "fii_stf_net", "fii_idf_net",
                                         "dii_stf_net", "dii_idf_net",
                                         "pro_stf_net", "pro_idf_net"]}}
        conn.execute(
            """INSERT INTO eod_mkt (date, nifty_close, banknifty_close, nifty_pcr,
               next_expiry, vix, client_stf_net, client_idf_net, fii_stf_net,
               fii_idf_net, dii_stf_net, dii_idf_net, pro_stf_net, pro_idf_net)
               VALUES (%(date)s, %(nifty_close)s, %(banknifty_close)s,
                       %(nifty_pcr)s, %(next_expiry)s, %(vix)s, %(client_stf_net)s,
                       %(client_idf_net)s, %(fii_stf_net)s, %(fii_idf_net)s,
                       %(dii_stf_net)s, %(dii_idf_net)s, %(pro_stf_net)s,
                       %(pro_idf_net)s)
               ON CONFLICT (date) DO UPDATE SET nifty_close=EXCLUDED.nifty_close,
                 banknifty_close=EXCLUDED.banknifty_close, nifty_pcr=EXCLUDED.nifty_pcr,
                 next_expiry=EXCLUDED.next_expiry, vix=EXCLUDED.vix,
                 client_stf_net=EXCLUDED.client_stf_net,
                 client_idf_net=EXCLUDED.client_idf_net,
                 fii_stf_net=EXCLUDED.fii_stf_net, fii_idf_net=EXCLUDED.fii_idf_net,
                 dii_stf_net=EXCLUDED.dii_stf_net, dii_idf_net=EXCLUDED.dii_idf_net,
                 pro_stf_net=EXCLUDED.pro_stf_net, pro_idf_net=EXCLUDED.pro_idf_net""",
            mrow)
    return len(rows)


def maintain_universe(conn, fo_symbols, dstr):
    """F-15: daily diff of the FO symbol set vs the universe table.

    Baseline semantics (P2-05, fixed after the 210-symbol finding): a symbol is
    'absent' only relative to RECENT activity — sessions-since-last-seen, counted
    from eod_daily dates (any symbol), not from a fixed list. 5 consecutive
    missed sessions deactivate. New symbols enter scoreable=FALSE and are
    promoted automatically at >= SCOREABLE_MIN_SESSIONS EOD rows (F-16)."""
    notes = []
    fo = set(fo_symbols)
    rows = conn.execute("SELECT symbol, active, last_seen FROM universe").fetchall()
    known = {r[0]: (r[1], r[2]) for r in rows}
    with conn.transaction():
        for sym in sorted(fo - set(known)):
            conn.execute(
                """INSERT INTO universe (symbol, isin, active, scoreable,
                   first_seen, last_seen) VALUES (%s, '', TRUE, FALSE, %s, %s)
                   ON CONFLICT (symbol) DO NOTHING""", (sym, dstr, dstr))
            notes.append(f"+{sym} NEW in F&O")
        for sym in fo & set(known):
            conn.execute("UPDATE universe SET last_seen = %s WHERE symbol = %s",
                         (dstr, sym))
            if not known[sym][0]:
                conn.execute("UPDATE universe SET active = TRUE WHERE symbol = %s",
                             (sym,))
                notes.append(f"+{sym} re-activated")
        for sym in sorted(set(known) - fo):
            active, last_seen = known[sym]
            if not active:
                continue
            n_after = conn.execute(
                "SELECT count(DISTINCT date) FROM eod_daily WHERE date > %s",
                (last_seen or dstr,)).fetchone()[0]
            if n_after >= 5:
                conn.execute("UPDATE universe SET active = FALSE WHERE symbol = %s",
                             (sym,))
                notes.append(f"-{sym} deactivated ({n_after} sessions absent)")
            else:
                notes.append(f"?{sym} absent ({n_after}/5)")
        # F-16: promote young symbols once their windows can form
        conn.execute(
            """UPDATE universe SET scoreable = TRUE
               WHERE scoreable = FALSE AND symbol IN (
                 SELECT symbol FROM eod_daily
                 GROUP BY symbol HAVING count(*) >= %s)""",
            (SCOREABLE_MIN_SESSIONS,))
    return notes


def evening_analytics(conn, cache, model, version, target_date=None):
    """Features -> scores -> top-10 + message context. Pure computation.
    target_date: ISO str for tests/parity (default: latest cached session)."""
    f = cache.frames
    dates = sorted(f["cash"]["date"].unique())
    if len(dates) > FEATURE_WINDOW_SESSIONS:
        keep = set(dates[-FEATURE_WINDOW_SESSIONS:])
        f = {k: v[v["date"].isin(keep)].copy() for k, v in f.items()}
    if target_date is not None:
        f = {k: v[v["date"] <= pd.Timestamp(target_date)].copy()
             for k, v in f.items()}
    panel = build_features(f["cash"], f["fut"], f["opt"], f["part"], f["mkt"], f["vix"])
    T = panel["date"].max()
    today = panel[panel["date"] == T].copy()

    scoreable = {r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE active AND scoreable").fetchall()}
    today = today[today["symbol"].isin(scoreable)].copy()

    today_ranked = rank_features(today)
    today["prob"] = model.predict_proba(today_ranked[FEATURES])[:, 1]

    top = today.nlargest(TOP_N, "prob").copy()
    # NaN features flow through to the model NATIVELY (HistGradientBoosting
    # handles missing values) — exactly as the research panel did. Do NOT
    # add exclusions here: that would diverge from backtest logic (F-14).
    nan_syms = top[top[FEATURES].isna().any(axis=1)]["symbol"].tolist()

    # raw basis_z + prior (direction.py formula, verbatim) + evening range20
    cf = f["cash"].merge(f["fut"][["date", "symbol", "nm_close"]], on=["date", "symbol"])
    g = cf.sort_values(["symbol", "date"]).groupby("symbol")
    basis_pct = (cf["nm_close"] - cf["close"]) / cf["close"]
    bm = basis_pct.groupby(cf["symbol"]).rolling(20, min_periods=10).mean() \
        .reset_index(level=0, drop=True).sort_index()
    bs = basis_pct.groupby(cf["symbol"]).rolling(20, min_periods=10).std() \
        .reset_index(level=0, drop=True).sort_index()
    cf = cf.assign(basis_pct=basis_pct,
                   basis_z=(basis_pct - bm.groupby(cf["symbol"]).shift(1))
                   / bs.groupby(cf["symbol"]).shift(1))
    r20h = g["high"].rolling(20, min_periods=20).max().reset_index(level=0, drop=True)
    r20l = g["low"].rolling(20, min_periods=20).min().reset_index(level=0, drop=True)
    cf["prior20H"] = r20h.groupby(cf["symbol"]).shift(1)
    cf["prior20L"] = r20l.groupby(cf["symbol"]).shift(1)
    cf["range20"] = (cf["prior20H"] - cf["prior20L"]) / cf["close"]
    ctx = cf[cf["date"] == T].set_index("symbol")

    top["basis_z"] = top["symbol"].map(ctx["basis_z"])
    top["prior"] = np.select([top["basis_z"] <= -1.5, top["basis_z"] > 1.5], [-1, 1], 0)
    top["range20"] = top["symbol"].map(ctx["range20"])
    top["prior20h"] = top["symbol"].map(ctx["prior20H"])   # P3 cap tiebreak inputs
    top["prior20l"] = top["symbol"].map(ctx["prior20L"])
    return T, top, nan_syms


def _bold(s: str) -> str:
    """Unicode mathematical bold — Telegram plain text renders it as bold."""
    out = []
    for c in s:
        if "A" <= c <= "Z":
            out.append(chr(ord(c) - 0x41 + 0x1D5D4))
        elif "a" <= c <= "z":
            out.append(chr(ord(c) - 0x61 + 0x1D5EE))
        elif "0" <= c <= "9":
            out.append(chr(ord(c) - 0x30 + 0x1D7EC))
        else:
            out.append(c)
    return "".join(out)


_KEYCAPS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def format_message(T, top, version, notes, nan_syms=()):
    lean = {-1: "▼", 0: "—", 1: "▲"}
    wd = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
          "Saturday", "Sunday"][T.weekday()]
    lines = [
        f"🌙 {_bold('KAVACH-945 · NIGHTLY WATCHLIST')}",
        f"📅 {wd}, {T.strftime('%d %b %Y')}",
        f"🤖 Model {version} · 🎯 {_bold('10 picks for tomorrow')}",
        "",
    ]
    for i, r in enumerate(top.itertuples(), 1):
        key = _KEYCAPS[i - 1] if i <= len(_KEYCAPS) else f"{i}."
        bz = f" {r.basis_z:+.1f}" if r.basis_z == r.basis_z else ""
        band = (f" · 📏 {r.prior20l:.1f}–{r.prior20h:.1f}"
                if (r.prior20l == r.prior20l and r.prior20h == r.prior20h) else "")
        lines.append(f"{key} {r.symbol} {lean[int(r.prior)]}{bz} · "
                     f"{r.prob*100:.1f}%{band}")
    if notes:
        lines += ["", "🌱 Universe: " + " · ".join(notes[:6])
                  + (" …" if len(notes) > 6 else "")]
    if nan_syms:
        lines += [f"⚠️ Thin history: {', '.join(nan_syms)}"]
    lines += [
        "",
        "⏰ Tomorrow 09:46 — final entries arrive here",
        "   🟢 LONG if price breaks above opening-range high",
        "   🔴 SHORT if price breaks below opening-range low",
        "   ✋ No breakout → no trade",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- the job
def run_evening(ctx, harvest_fn=None, vix_fn=None):
    now = clock.now()
    T = clock.today_key()
    if now.weekday() >= 5:
        return "skipped:weekend"
    conn = ctx["connect"]()
    if T in load_holidays(conn):
        conn.close()
        return "skipped:holiday"
    if not (18 <= now.hour < 23):
        conn.close()
        raise RuntimeError(f"evening job triggered off-schedule ({now:%H:%M} IST, P2-10)")

    harvest_fn = harvest_fn or engine_harvest.harvest_day
    vix_fn = vix_fn or engine_harvest.fetch_vix
    import datetime as dt
    ladder_ended = now.hour >= 22 or (now.hour == 21 and now.minute >= 55)
    try:
        day_raw = harvest_fn(dt.date.fromisoformat(T))
    except RuntimeError as e:
        if "FO missing" in str(e) and not ladder_ended:
            conn.close()
            return "skipped:not-posted-yet (FO lags cash)"   # P2-02: partial = not ready
        raise                                                # outside ladder: loud
    if day_raw is None:
        if ladder_ended:
            ctx["alert"](f"bhavcopy MISSING for {T} after retry ladder — "
                         f"not a known holiday. Investigate NSE posting.", severity="ERROR")
            raise RuntimeError(f"bhavcopy missing after ladder: {T}")
        conn.close()
        return "skipped:not-posted-yet"

    day = _norm_day(day_raw, T)
    # sanity gates (P2-02)
    for key, minimum in (("cash", SANITY["cash_min"]), ("fut", SANITY["fut_min"]),
                         ("opt", SANITY["opt_min"])):
        if len(day[key]) < minimum:
            raise RuntimeError(f"sanity gate failed: {key} rows {len(day[key])} < {minimum}")
    if not day["mkt"].get("nifty_close"):
        raise RuntimeError("sanity gate failed: nifty_close missing")

    # VIX (D2: fail loud, never carry-forward)
    vdf = vix_fn(dt.date.fromisoformat(T), dt.date.fromisoformat(T))
    if not len(vdf):
        ctx["alert"](f"VIX unavailable for {T} — watchlist NOT sent (fail-loud policy D2)",
                     severity="ERROR")
        raise RuntimeError(f"VIX unavailable for {T}")
    vix_val = round(float(vdf.iloc[-1]["vix"]), 3)

    n = upsert_day(conn, day, vix_val)
    notes = maintain_universe(conn, set(day["fut"]["symbol"]), T)

    version, trained_through, model = load_model(conn)
    if trained_through >= T:
        raise RuntimeError(f"model {version} trained_through {trained_through} "
                           f">= scoring date {T} (P2-04 freshness violation)")
    cache = get_cache(ctx.get("url"))
    cache.append_day(day, {"date": T, "vix": vix_val})
    Tdt, top, nan_syms = evening_analytics(conn, cache, model, version)
    if Tdt.date().isoformat() != T:
        raise RuntimeError(f"analytics date {Tdt} != harvest date {T}")

    msg = format_message(Tdt, top, version, notes, nan_syms)
    ok = ctx["send"](msg) if ctx.get("send") else False
    if not ok:
        ctx["alert"]("evening watchlist TELEGRAM DELIVERY FAILED (data committed, "
                     "message not delivered)", severity="ERROR")
        raise RuntimeError("telegram delivery failed")

    with conn.transaction():
        conn.execute("DELETE FROM watchlists WHERE dkey = %s", (T,))
        for i, r in enumerate(top.itertuples(), 1):
            conn.execute(
                """INSERT INTO watchlists (dkey, symbol, rank, prob, basis_z, prior,
                                          range20, prior20h, prior20l)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (T, r.symbol, i, float(r.prob),
                 float(r.basis_z) if r.basis_z == r.basis_z else None,
                 int(r.prior), float(r.range20) if r.range20 == r.range20 else None,
                 float(r.prior20h) if r.prior20h == r.prior20h else None,
                 float(r.prior20l) if r.prior20l == r.prior20l else None))
    conn.close()
    return f"done:{n} rows, top-1 {top.iloc[0]['symbol']} @{top.iloc[0]['prob']:.1%}"
