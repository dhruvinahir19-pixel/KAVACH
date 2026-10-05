"""
harvest.py — download & parse ONE trading day from NSE archives.
Raw bytes land in /tmp only; compact per-stock aggregates are returned.

Sources per day:
  1. sec_bhavcopy_full (cash, incl. delivery)      -> per-stock OHLCV+delivery
  2. FO UDiFF bhavcopy (all futures+options)       -> futures agg, option-chain
                                                     agg, index/market row
  3. fao_participant_oi (Client/DII/FII/Pro OI)    -> market-level positioning
  4. India VIX (Yahoo, fetched separately)         -> see fetch_vix()
"""
import io, time, zipfile, datetime as dt
import requests, pandas as pd
from config import UA, url_cash, url_fo, url_participant, TMP


def fetch(url, tries=3, sleep=1.0):
    """GET with retry; returns bytes or None (404/holiday/soft-error).
    P3-12 (live 2026-10-05): NSE intermittently 403-blocks datacenter IPs
    (Render + sandbox both blocked today; headers/warming don't help — full
    IP-range block). Strategy:
      - 404 -> None instantly (file not posted / holiday)
      - 403 -> PATIENT retries (~40s apart — evening WAF storms are often
        transient), then a r.jina.ai reader-proxy fallback (free, no key;
        their servers aren't blocked). The proxy only returns TEXT, so it
        works for the CSVs (cash bhavcopy, participants) and never for the
        FO zip — validated by requiring a CSV-looking header after stripping
        the wrapper.
      - persistent 403 with no working fallback -> raise LOUD (a block is
        NOT 'file absent'; P3-11b)."""
    import time as _t
    last_403 = None
    for i in range(tries):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200:
                b = r.content.lstrip()
                if b[:1] in (b"<",):       # NSE soft-error page
                    return None
                return r.content
            if r.status_code == 404:
                return None
            if r.status_code == 403:
                last_403 = r
                if i < tries - 1:
                    _t.sleep(40)           # patient: WAF storms clear
                    continue
        except requests.RequestException:
            pass
        _t.sleep(sleep * (i + 1))
    # 403 persisted (or network-dead): try the text-proxy fallback
    if last_403 is not None:
        proxied = _proxy_fetch(url)
        if proxied is not None:
            return proxied
        raise RuntimeError(f"NSE 403 Access Denied for {url} — IP blocked? "
                           f"(a block is NOT 'file absent')")
    return None


CSV_HEADS = ("SYMBOL", "TradDt", "Instrument", "Date", "Participant",
             "Client Type", "Record Type", "FUTVAL", "OPTVAL")


def _proxy_fetch(url):
    """r.jina.ai reader fallback for TEXT files. Returns verified CSV bytes
    or None (zip/binary/unreachable -> None -> caller raises)."""
    import requests as _rq
    try:
        r = _rq.get(f"https://r.jina.ai/{url}", timeout=120,
                    headers={"User-Agent": UA})
        if r.status_code != 200:
            return None
        lines = r.text.splitlines()
        start = next((i for i, l in enumerate(lines)
                      if l.startswith(CSV_HEADS)), None)
        if start is None:
            return None                    # not a CSV (e.g. zip) or error page
        return ("\n".join(lines[start:]) + "\n").encode()
    except Exception:                      # noqa: BLE001
        return None


# ------------------------------------------------------------------ parsers
def parse_cash(b):
    """Cash bhavcopy -> (EQ rows, file_date). NOTE: on holidays NSE re-serves
    the last trading day's file, so callers must verify file_date matches."""
    df = pd.read_csv(io.BytesIO(b))
    df.columns = [c.strip() for c in df.columns]
    # NSE pads values with spaces: ' EQ', ' 20MICRONS' — strip both
    df["SERIES"] = df["SERIES"].astype(str).str.strip()
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    file_date = pd.to_datetime(df["DATE1"].astype(str).str.strip().iloc[0],
                               format="%d-%b-%Y").date()
    df = df[df["SERIES"] == "EQ"].copy()
    return df, file_date
    for c in ["PREV_CLOSE","OPEN_PRICE","HIGH_PRICE","LOW_PRICE","CLOSE_PRICE",
              "TTL_TRD_QNTY","TURNOVER_LACS","NO_OF_TRADES","DELIV_QTY","DELIV_PER"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _strip_cols(df):
    df.columns = [c.strip() for c in df.columns]
    return df


def parse_fo(b):
    """FO UDiFF zip -> (fut_agg, opt_agg, mkt_row) for the F&O stock universe.

    fut_agg/fut rows: nearest-month contract metrics + all-contract totals
    opt_agg rows:     reconstructed option-chain summary per stock
    mkt_row:          NIFTY/BANKNIFTY spot (from UndrlygPric), NIFTY PCR,
                      next NIFTY weekly expiry date
    """
    zf = zipfile.ZipFile(io.BytesIO(b))
    name = [n for n in zf.namelist() if n.lower().endswith(".csv")][0]
    df = pd.read_csv(io.BytesIO(zf.read(name)), low_memory=False)

    # ---------------- stock futures (STF) -> universe + futures aggregates
    stf = df[df["FinInstrmTp"] == "STF"].copy()
    stf["XpryDt"] = pd.to_datetime(stf["XpryDt"])
    fut_rows = []
    for sym, g in stf.groupby("TckrSymb"):
        nm = g.loc[g["XpryDt"].idxmin()]           # nearest-month contract
        fut_rows.append({
            "symbol": sym,
            "nm_expiry": nm["XpryDt"].date().isoformat(),
            "nm_close": nm["ClsPric"], "nm_oi": nm["OpnIntrst"],
            "fut_oi": g["OpnIntrst"].sum(), "fut_oi_chg": g["ChngInOpnIntrst"].sum(),
            "fut_vol": g["TtlTradgVol"].sum(), "fut_val": g["TtlTrfVal"].sum(),
            "fut_txns": g["TtlNbOfTxsExctd"].sum(),
        })
    fut = pd.DataFrame(fut_rows)
    universe = set(fut["symbol"])

    # ---------------- stock options (STO) -> chain summary per stock
    sto = df[df["FinInstrmTp"] == "STO"].copy()
    sto = sto[sto["TckrSymb"].isin(universe)]
    opt_rows = []
    for sym, g in sto.groupby("TckrSymb"):
        ce, pe = g[g["OptnTp"] == "CE"], g[g["OptnTp"] == "PE"]
        ce_oi, pe_oi = ce["OpnIntrst"].sum(), pe["OpnIntrst"].sum()
        tot = ce_oi + pe_oi
        # top-3 strikes by combined OI -> pinning / explosion potential
        by_strike = g.groupby("StrkPric")["OpnIntrst"].sum().sort_values(ascending=False)
        top3 = by_strike.head(3).sum() / tot if tot > 0 else 0.0
        opt_rows.append({
            "symbol": sym,
            "ce_oi": ce_oi, "pe_oi": pe_oi,
            "ce_oi_chg": ce["ChngInOpnIntrst"].sum(), "pe_oi_chg": pe["ChngInOpnIntrst"].sum(),
            "ce_vol": ce["TtlTradgVol"].sum(), "pe_vol": pe["TtlTradgVol"].sum(),
            "ce_val": ce["TtlTrfVal"].sum(), "pe_val": pe["TtlTrfVal"].sum(),
            "top3_conc": top3, "call_build": ce_oi / tot if tot > 0 else 0.5,
        })
    opt = pd.DataFrame(opt_rows)

    # ---------------- index-level market row (NIFTY spot/PCR from IDO/IDF)
    nifty_idf = df[(df["FinInstrmTp"] == "IDF") & (df["TckrSymb"] == "NIFTY")]
    nifty_ido = df[(df["FinInstrmTp"] == "IDO") & (df["TckrSymb"] == "NIFTY")]
    bn_idf = df[(df["FinInstrmTp"] == "IDF") & (df["TckrSymb"] == "BANKNIFTY")]
    nifty_close = float(nifty_idf["UndrlygPric"].iloc[0]) if len(nifty_idf) else None
    bn_close = float(bn_idf["UndrlygPric"].iloc[0]) if len(bn_idf) else None
    # PCR across ALL NIFTY index options (weekly + monthly)
    n_ce = nifty_ido.loc[nifty_ido["OptnTp"] == "CE", "OpnIntrst"].sum()
    n_pe = nifty_ido.loc[nifty_ido["OptnTp"] == "PE", "OpnIntrst"].sum()
    pcr = float(n_pe / n_ce) if n_ce > 0 else None
    # next weekly expiry = earliest NIFTY index-option expiry
    if len(nifty_ido):
        nxt_exp = pd.to_datetime(nifty_ido["XpryDt"]).min().date().isoformat()
    else:
        nxt_exp = None
    mkt = {"nifty_close": nifty_close, "banknifty_close": bn_close,
           "nifty_pcr": pcr, "next_expiry": nxt_exp}
    return fut, opt, mkt


def parse_participant(b):
    """Participant-wise OI -> net-long ratios by participant (market level)."""
    df = pd.read_csv(io.BytesIO(b), header=1)
    df = _strip_cols(df)
    df["Client Type"] = df["Client Type"].str.strip()
    out = {}
    for who in ["Client", "FII", "DII", "Pro"]:
        r = df[df["Client Type"] == who]
        if not len(r):
            continue
        r = r.iloc[0]
        def net(l, s):
            return (r[l] - r[s]) / (r[l] + r[s]) if (r[l] + r[s]) > 0 else 0.0
        out[who.lower()] = {
            "stf_net": net("Future Stock Long", "Future Stock Short"),
            "idf_net": net("Future Index Long", "Future Index Short"),
        }
    return out


# ------------------------------------------------------------------ day pipe
def harvest_day(date):
    """Returns dict of compact frames for one date, or None if not a trading
    day (cash file missing). Participant file is optional (publish lag)."""
    b_cash = fetch(url_cash(date))
    if b_cash is None:
        return None
    cash, file_date = parse_cash(b_cash)
    if file_date != date:                    # holiday re-serve of old file
        return None
    b_fo = fetch(url_fo(date))
    if b_fo is None:
        raise RuntimeError(f"cash OK but FO missing for {date} — investigate")
    b_part = fetch(url_participant(date))

    fut, opt, mkt = parse_fo(b_fo)
    universe = set(fut["symbol"])
    cash = cash[cash["SYMBOL"].isin(universe)][
        ["SYMBOL","PREV_CLOSE","OPEN_PRICE","HIGH_PRICE","LOW_PRICE","CLOSE_PRICE",
         "TTL_TRD_QNTY","TURNOVER_LACS","NO_OF_TRADES","DELIV_QTY","DELIV_PER"]].copy()
    cash.columns = ["symbol","prev_close","open","high","low","close",
                    "volume","turnover_l","trades","deliv_qty","deliv_per"]

    day = date.isoformat()
    cash.insert(0, "date", day)
    for d in (fut, opt):
        d.insert(0, "date", day)

    part = {"date": day}
    if b_part:
        p = parse_participant(b_part)
        for who in ["client","fii","dii","pro"]:
            if who in p:
                part[f"{who}_stf_net"] = p[who]["stf_net"]
                part[f"{who}_idf_net"] = p[who]["idf_net"]
    mkt["date"] = day
    return {"cash": cash, "fut": fut, "opt": opt, "part": part, "mkt": mkt}


def fetch_vix(start, end):
    """India VIX daily closes from Yahoo (^INDIAVIX)."""
    tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=tz).timestamp())
    p2 = int((dt.datetime(end.year, end.month, end.day, tzinfo=tz) + dt.timedelta(days=1)).timestamp())
    r = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX",
                     params={"period1": p1, "period2": p2, "interval": "1d"},
                     headers={"User-Agent": UA}, timeout=30)
    j = r.json()["chart"]["result"][0]
    rows = []
    for t, c in zip(j["timestamp"], j["indicators"]["quote"][0]["close"]):
        if c is not None:
            d = dt.datetime.fromtimestamp(t, tz).date()
            if start <= d <= end:
                rows.append({"date": d.isoformat(), "vix": round(float(c), 3)})
    return pd.DataFrame(rows)
