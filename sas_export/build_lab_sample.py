"""Stratified ~20 MB CSV sample of the TRAINING table (data/dev/daydetect.parquet) to upload to the Viya lab as evidence.
Real rows only (nothing is altered or injected); values/nulls are exactly as in the training table. data/test and data/test2 are never touched.
Usage: python sas_export/build_lab_sample.py [--mb 20] [--seed 7]      -> sas_export/lab_evidence/"""
import argparse, hashlib, json, os
import numpy as np, pandas as pd

ap = argparse.ArgumentParser(); ap.add_argument("--mb", type=float, default=20.0); ap.add_argument("--seed", type=int, default=7)
args = ap.parse_args()
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); OUT = os.path.join(HERE, "lab_evidence"); os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(args.seed)

d = pd.read_parquet(os.path.join(ROOT, "data", "dev", "daydetect.parquet"))
d = d.rename(columns={"label_fraud_today": "fraud_today", "_fraud_type": "fraud_type", "_stealth": "stealth"})
for c in d.select_dtypes("category").columns: d[c] = d[c].astype(str)
d["snap_date"] = pd.to_datetime(d.snap_date).dt.strftime("%Y-%m-%d")
d["month"] = d.snap_date.str[:7]
FEATS = [v["name"] for v in json.load(open(os.path.join(HERE, "fraudXgbMaskV11", "inputVar.json")))]


def draw(n):
    """~28% fraud-day rows (all fraud types/stealth kinds), ~17% borderline-looking genuine rows, the rest genuine rows spread over month x segment x occupation."""
    fr = d[d.fraud_today == 1]; ng = d[d.fraud_today == 0]
    n_fr = min(len(fr), int(n * 0.28)); n_hard = int(n * 0.17); n_base = n - n_fr - n_hard
    part_fr = fr.groupby(["fraud_type", "stealth"], group_keys=False).apply(lambda g: g.sample(min(len(g), max(1, round(n_fr * len(g) / len(fr)))), random_state=int(rng.integers(1e9))))
    # borderline genuine: high outflow vs income, many new payees or night activity (what a model could confuse with fraud)
    score = ng.out_to_income_30.rank(pct=True) + ng.new_payee_rate_30.rank(pct=True) + ng.night_share_30.rank(pct=True)
    hard = ng[score >= score.quantile(0.9)].sample(n_hard, random_state=int(rng.integers(1e9)))
    rest = ng.drop(hard.index)
    base = rest.groupby(["month", "segment", "occupation"], group_keys=False).apply(lambda g: g.sample(min(len(g), max(1, round(n_base * len(g) / len(rest)))), random_state=int(rng.integers(1e9))))
    s = pd.concat([part_fr, hard, base]).drop_duplicates(["customer_id", "snap_date"])
    return s.sample(frac=1, random_state=args.seed).drop(columns="month").reset_index(drop=True)


def size_mb(df):
    return len(df.to_csv(index=False, float_format="%.6g").encode()) / 1e6


n = 30000
for _ in range(6):                                    # converge on the target size
    s = draw(n); mb = size_mb(s)
    if abs(mb - args.mb) / args.mb < 0.04: break
    n = int(n * args.mb / mb)
path = os.path.join(OUT, "training_sample_20mb.csv")
s.to_csv(path, index=False, float_format="%.6g")
sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
man = {"file": "training_sample_20mb.csv", "source": "data/dev/daydetect.parquet (training table, v1.1)", "rows": len(s), "size_mb": round(os.path.getsize(path) / 1e6, 2), "seed": args.seed,
       "fraud_share_in_sample": round(float(s.fraud_today.mean()), 4), "fraud_share_in_full_table": round(float(d.fraud_today.mean()), 5),
       "customers": int(s.customer_id.nunique()), "date_range": [s.snap_date.min(), s.snap_date.max()], "model_input_columns": len(FEATS), "sha256": sha,
       "note": "Stratified, NOT representative of the base rate (fraud and borderline rows are oversampled). Synthetic data. Gender/age_ord/region are audit columns, not model inputs."}
json.dump(man, open(os.path.join(OUT, "training_sample_manifest.json"), "w"), indent=1, ensure_ascii=False)
print(json.dumps(man, indent=1, ensure_ascii=False))
