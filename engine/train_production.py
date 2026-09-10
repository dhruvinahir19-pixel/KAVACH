"""
train_production.py — train the deploy model on ALL labeled data available and
push the versioned blob to Neon (model_blob). Run in the workspace (or any env
with the engine stores + NEON_DATABASE_URL). Blob is pickled with the training
sklearn — the scanner pins scikit-learn==1.6.1 to guarantee loads.
"""
import hashlib
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))            # engine
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scanner"))  # scanner

from config import PANEL_F, TRAIN_FROM                      # noqa: E402
from model_core import rank_features, train_model           # noqa: E402
from kcore import neon_store                                # noqa: E402


def main():
    p = pd.read_csv(PANEL_F, parse_dates=["date"])
    p = p[p["date"] >= TRAIN_FROM]
    lab = p[p["y_move"].notna()].copy()
    trained_through = lab["date"].max()
    r = rank_features(lab)
    model = train_model(r)
    print(f"trained on {len(r)} rows through {trained_through.date()}")

    blob = pickle.dumps(model)
    sha = hashlib.sha256(blob).hexdigest()
    version = f"prod-{trained_through.strftime('%Y%m%d')}"

    conn = neon_store.connect()
    conn.execute(
        """INSERT INTO model_blob (version, trained_through, payload, sha256, created_at)
           VALUES (%s, %s, %s, %s, now())
           ON CONFLICT (version) DO UPDATE SET payload = excluded.payload,
             trained_through = excluded.trained_through, sha256 = excluded.sha256,
             created_at = now()""",
        (version, trained_through.date().isoformat(), pickle.dumps(model), sha),
    )
    # keep only the 3 newest versions (storage discipline)
    conn.execute("""DELETE FROM model_blob WHERE version NOT IN (
                     SELECT version FROM model_blob ORDER BY created_at DESC LIMIT 3)""")
    n = conn.execute("SELECT count(*) FROM model_blob").fetchone()[0]
    conn.close()

    local = Path(__file__).resolve().parent / "data" / "model_prod.pkl"
    local.write_bytes(blob)
    print(f"pushed {version} | sha256 {sha[:16]}... | {len(blob)/1e6:.2f} MB | "
          f"versions in Neon: {n} | local copy: {local}")


if __name__ == "__main__":
    main()
