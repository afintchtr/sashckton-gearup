"""Proof that serving features == training features.  Usage: python serving/test_rolling_parity.py [n_rows]"""
import glob, json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "serving"))
import rolling as R

n_rows = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
feats = [f["name"] for f in json.load(open(f"{ROOT}/bundle/fraud_xgb_mask_v1/schema.json"))["features"]]
off = pd.read_parquet(f"{ROOT}/data/dev/daydetect.parquet")
cust = pd.read_csv(f"{ROOT}/data/dev/customers.csv").set_index("customer_id")
parts = sorted(glob.glob(f"{ROOT}/data/dev/customer_daily/part-*.parquet"))[:6]            # first 6000 customers
hist = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
ids = set(hist.customer_id.unique())
off = off[off.customer_id.isin(ids)]
rng = np.random.default_rng(0)
pos = off[off.label_fraud_today == 1]
smp = pd.concat([pos.sample(min(len(pos), n_rows // 3), random_state=0), off[off.label_fraud_today == 0].sample(n_rows - min(len(pos), n_rows // 3), random_state=0)]).reset_index(drop=True)
H = R.to_arrays(hist, smp.customer_id, smp.snap_date)
static = cust.loc[smp.customer_id].reset_index()
new = R.compute_features(H, static, smp.snap_date)
bad = {}
for f in feats:
    a, b = new[f], smp[f]
    if f in ("gender", "region", "occupation", "segment"):
        ok = (a.astype(str).values == b.astype(str).values)
    else:
        ok = np.isclose(a.to_numpy(np.float64), b.to_numpy(np.float64), rtol=2e-4, atol=1e-4, equal_nan=True)
    if not ok.all():
        bad[f] = (int((~ok).sum()), float(np.nanmax(np.abs(a.to_numpy(np.float64)[~ok] - b.to_numpy(np.float64)[~ok]))) if f not in ("gender", "region", "occupation", "segment") else None)
print(f"rows compared: {len(smp):,} (fraud days {int(smp.label_fraud_today.sum()):,}) | features: {len(feats)} | mismatching features: {len(bad)}")
for k, v in bad.items(): print("  MISMATCH", k, v)
print("PARITY OK" if not bad else "PARITY FAILED")
sys.exit(0 if not bad else 1)
