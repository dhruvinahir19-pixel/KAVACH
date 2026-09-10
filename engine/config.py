"""
Next-Day Momentum Fingerprint Engine v2 — 2026 rebuild
Config: paths, URL templates, universe rules.

SPACE BUDGET RULES (128MB hard cap on workspace):
  - Raw NSE files (cash bhavcopy ~0.4MB, FO zip ~1MB, participant ~1KB)
    are downloaded to /tmp ONLY, parsed in memory, and never written
    to the workspace.
  - Only compact per-stock aggregates are persisted (csv.gz).
  - Estimated steady-state size: ~10-15MB for a full year.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
OUTPUT = os.path.join(ROOT, "output")
TMP = "/tmp/mfe_raw"          # transient raw files (outside workspace snapshot)

os.makedirs(DATA, exist_ok=True)
os.makedirs(OUTPUT, exist_ok=True)
os.makedirs(TMP, exist_ok=True)

# ------------------------------------------------------------------ dates
# Warmup harvest starts Oct 2024 ONLY to seed rolling indicators
# (ATR14, 60d averages, 52w-high proxy) so 2025-01-01 features are formed.
# NO training/validation rows are built before TRAIN_FROM.
# STAGE-4 EXTENSION (user-approved): backtest window widened to 2025->2026
# to test regime-dependence of the short side. OOS picks start Apr 2025.
HARVEST_FROM = "2024-10-01"
TRAIN_FROM = "2025-01-01"      # learning window start (OOS from Apr 2025)

# ------------------------------------------------------------------ URLs
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

def url_cash(d):      # cash bhavcopy WITH delivery columns
    return f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"

def url_fo(d):        # full F&O UDiFF bhavcopy (every contract)
    return f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"

def url_participant(d):
    return f"https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{d:%d%m%Y}.csv"

VIX_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/%5EINDIAVIX"

# ------------------------------------------------------------------ storage
CASH_F = os.path.join(DATA, "cash.csv.gz")
FUT_F  = os.path.join(DATA, "fut.csv.gz")
OPT_F  = os.path.join(DATA, "opt.csv.gz")
PART_F = os.path.join(DATA, "part.csv.gz")
MKT_F  = os.path.join(DATA, "mkt.csv.gz")
VIX_F  = os.path.join(DATA, "vix.csv")
PANEL_F = os.path.join(DATA, "panel.csv.gz")

# ------------------------------------------------------------------ misc
TOP_N = 10                # watchlist size
QUINTILE = 0.80           # label: next-day TR% above 80th percentile of universe
SEED = 42
