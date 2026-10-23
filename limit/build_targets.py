"""Targets for the limit models (proposal 5.3), attached to snapshot rows (customer x biweekly date).
  demand_30    max daily INTENDED outflow over the next 30 days (uncensored need; realized outflow is capped by the limit)
  declined_30  1 if any transfer was declined by the limit in the next 30 days (business label)
Usage: python limit/build_targets.py --data data/dev --snap data/dev/snapshots.parquet --out data/dev/limit_snapshots.parquet"""
import argparse, glob, os
from concurrent.futures import ProcessPoolExecutor
import numpy as np, pandas as pd
WIN_START = pd.Timestamp("2025-07-01"); D = (pd.Timestamp("2026-09-30") - WIN_START).days + 1; H = 30

def one(args):
    part, snaps = args
    x = pd.read_parquet(part, columns=["customer_id", "date", "intended_amount", "n_declined_limit"])
    ids, ci = np.unique(x.customer_id.to_numpy(), return_inverse=True); di = (x.date - WIN_START).dt.days.to_numpy()
    INT = np.full((len(ids), D), np.nan); INT[ci, di] = x.intended_amount.to_numpy(float)
    DEC = np.full((len(ids), D), np.nan); DEC[ci, di] = x.n_declined_limit.to_numpy(float)
    s = snaps[snaps.customer_id.isin(ids)].copy()
    row_ci = pd.Index(ids).get_indexer(s.customer_id); t = ((s.snap_date - WIN_START).dt.days).to_numpy()
    d30 = np.full(len(s), np.nan); dec = np.full(len(s), np.nan)
    for tt in np.unique(t):
        m = t == tt
        if tt + H >= D: continue
        d30[m] = np.nanmax(INT[row_ci[m], tt + 1:tt + 1 + H], axis=1); dec[m] = (np.nansum(DEC[row_ci[m], tt + 1:tt + 1 + H], axis=1) > 0)
    s["demand_30"], s["declined_30"] = d30, dec
    return s

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--data", default="data/dev"); ap.add_argument("--snap", default="data/dev/snapshots.parquet")
    ap.add_argument("--out", default="data/dev/limit_snapshots.parquet"); ap.add_argument("--workers", type=int, default=4); a = ap.parse_args()
    snaps = pd.read_parquet(a.snap); parts = sorted(glob.glob(os.path.join(a.data, "customer_daily", "part-*.parquet")))
    with ProcessPoolExecutor(a.workers) as ex: out = list(ex.map(one, [(p, snaps) for p in parts]))
    r = pd.concat(out, ignore_index=True).dropna(subset=["demand_30"]); r.to_parquet(a.out, index=False)
    print(f"rows={len(r):,} | declined_30 rate {r.declined_30.mean():.3%} | demand_30 median {r.demand_30.median():,.0f} p95 {r.demand_30.quantile(.95):,.0f}")
