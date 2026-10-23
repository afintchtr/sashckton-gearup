"""Seed the demo feature store from the DEV synthetic data (never the TEST set).
Closed daily records up to --cutoff go into the store; the following days are kept in serving/demo_replay.parquet so they can be replayed as live events.
Usage: python serving/seed_store.py [--n-normal 300] [--cutoff 2026-08-14] [--out serving/demo.db]"""
import argparse, glob, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, HERE)
from store import FeatureStore
import rolling as R

ap = argparse.ArgumentParser()
ap.add_argument("--n-normal", type=int, default=300)
ap.add_argument("--cutoff", default="2026-08-14")
ap.add_argument("--replay-days", type=int, default=21)
ap.add_argument("--out", default=os.path.join(HERE, "demo.db"))
a = ap.parse_args()
cut = pd.Timestamp(a.cutoff); end = cut + pd.Timedelta(days=a.replay_days)

cust = pd.read_csv(f"{ROOT}/data/dev/customers.csv"); ev = pd.read_csv(f"{ROOT}/data/dev/fraud_events.csv", parse_dates=["event_start", "event_end"])
parts = sorted(glob.glob(f"{ROOT}/data/dev/customer_daily/part-*.parquet"))[:12]
daily = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
ids_in = set(daily.customer_id.unique())
fr = ev[(ev.reported == 1) & ev.event_start.between(cut + pd.Timedelta(days=1), end - pd.Timedelta(days=2)) & ev.customer_id.isin(ids_in)]
rng = np.random.default_rng(7)
old_enough = cust[(pd.to_datetime(cust.open_date) <= cut - pd.Timedelta(days=100)) & cust.customer_id.isin(ids_in) & ~cust.customer_id.isin(ev.customer_id)]
normal = old_enough.sample(a.n_normal, random_state=7).customer_id
chosen = pd.Index(fr.customer_id.tolist() + normal.tolist())
d = daily[daily.customer_id.isin(chosen)].copy()
if os.path.exists(a.out): os.remove(a.out)
st = FeatureStore(a.out)
st.upsert_customers(cust[cust.customer_id.isin(chosen)])
st.load_history(d[(d.date <= cut) & (d.date > cut - pd.Timedelta(days=95))])
known = [(c, f"K{i}") for c in chosen for i in range(20)]
st.db.executemany("INSERT OR IGNORE INTO payees_known VALUES (?,?)", known); st.db.commit()
rep = d[(d.date > cut) & (d.date <= end)].merge(ev[["customer_id", "fraud_type", "event_start", "stealth"]], on="customer_id", how="left")
rep.to_parquet(os.path.join(HERE, "demo_replay.parquet"), index=False)
print(f"store: {a.out} | customers {len(chosen)} (fraud {fr.customer_id.nunique()}, normal {len(normal)}) | closed days to {cut.date()} | replay rows {len(rep):,} to {end.date()}")
