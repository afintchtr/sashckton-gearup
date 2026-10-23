"""End-to-end checks of the serving stack.  Usage: python serving/test_service.py
 1. event ingestion semantics (hand-built scenario)
 2. replay of real synthetic days as event streams -> API score  vs  reference score computed straight from the daily records
Writes serving/test_report.json"""
import json, os, shutil, sys, tempfile
import numpy as np, pandas as pd
from scipy.stats import spearmanr
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, HERE)
tmp = tempfile.mkdtemp()
shutil.copy(os.path.join(HERE, "demo.db"), os.path.join(tmp, "t.db"))
os.environ["STORE_PATH"] = os.path.join(tmp, "t.db"); os.environ["LOG_PATH"] = os.path.join(tmp, "log.jsonl")
from fastapi.testclient import TestClient
import app as A, rolling as R
from replay import day_to_events
client = TestClient(A.app)
rep_all = pd.read_parquet(os.path.join(HERE, "demo_replay.parquet"))
report = {}

# ---- 1. ingestion semantics ---------------------------------------------------------------------------------------------------------------
cid = rep_all.customer_id.iloc[0]; cap = A.store.profile(cid)["cap"]; d0 = rep_all.date.min() - pd.Timedelta(days=1)
day = (d0 + pd.Timedelta(days=30)).normalize()                      # a quiet future day for this customer
A.store.ensure_days(cid, day - pd.Timedelta(days=1)); bal = A.store._row(cid, (day - pd.Timedelta(days=1)).strftime("%Y-%m-%d"))["end_balance"]
evs = [dict(customer_id=cid, ts=(day + pd.Timedelta(hours=23, minutes=30)).isoformat(), type="transfer", amount=1_000_000, payee_id="NEW1", category="ewallet_topup", geo_mismatch=True),
       dict(customer_id=cid, ts=(day + pd.Timedelta(hours=10)).isoformat(), type="transfer", amount=3_000_000, payee_id="K1", category="bills"),
       dict(customer_id=cid, ts=(day + pd.Timedelta(hours=11)).isoformat(), type="transfer", amount=1_000_000, payee_id="K1", category="bills"),
       dict(customer_id=cid, ts=(day + pd.Timedelta(hours=12)).isoformat(), type="transfer", amount=0.6 * cap, payee_id="NEW2", declined_by_limit=True),
       dict(customer_id=cid, ts=(day + pd.Timedelta(hours=13)).isoformat(), type="device_new")]
assert client.post("/events", json=evs).status_code == 200
r = A.store._row(cid, day.strftime("%Y-%m-%d"))
exp = dict(outflow_amt=5e6, n_outflow=3, night_outflow_amt=1e6, n_new_payee=1, n_distinct_payee=2, n_geo_mismatch=1, new_device=1, n_declined_limit=1, out_bills=4e6, out_ewallet_topup=1e6)
for k, v in exp.items(): assert abs(r[k] - v) < 1e-6, (k, r[k], v)
assert abs(r["top_payee_share"] - 0.8) < 1e-9 and r["ftp_large"] == 1 and abs(r["end_balance"] - (bal - 5e6)) < 1e-6
report["ingestion_unit_test"] = "passed"; print("1. ingestion semantics: PASSED")

# ---- 2. replay vs reference -----------------------------------------------------------------------------------------------------------------
fresh = A.FeatureStore(os.path.join(HERE, "demo.db"))                                    # untouched seed = reference history
ids = list(dict.fromkeys(rep_all[rep_all._fraud_day == 1].customer_id.tolist()))[:19] + list(rep_all[~rep_all.customer_id.isin(rep_all[rep_all._fraud_day == 1].customer_id)].customer_id.unique()[:25])
cut = rep_all.date.min() - pd.Timedelta(days=1); N_DAYS = 10
hist0 = fresh.history(ids, cut + pd.Timedelta(days=0))
cols = ["customer_id", "date"] + R.DAILY_COLS
ref_hist = pd.concat([hist0[cols], rep_all[rep_all.customer_id.isin(ids)][cols]], ignore_index=True)
prof = pd.read_csv(os.path.join(ROOT, "data/dev", "customers.csv")).set_index("customer_id")
rows = []
for k in range(1, N_DAYS + 1):
    d = cut + pd.Timedelta(days=k)
    todays = rep_all[(rep_all.date == d) & rep_all.customer_id.isin(ids)]
    for _, row in todays.iterrows():
        evs = day_to_events(row, A.store.profile(row.customer_id)["cap"], seed=k)
        if evs: assert client.post("/events", json=evs).status_code == 200
    ref_feats = R.compute_features(R.to_arrays(ref_hist, todays.customer_id.tolist(), [d] * len(todays)), prof.loc[todays.customer_id].reset_index(), [d] * len(todays))
    ref = A.scorer.score_df(ref_feats, reasons=False)
    for i, (_, row) in enumerate(todays.iterrows()):
        res = client.post("/score", json=dict(customer_id=row.customer_id, as_of=(d + pd.Timedelta(hours=23, minutes=59)).isoformat(), reasons=False)).json()
        rows.append(dict(customer_id=row.customer_id, date=d, fraud_day=int(row._fraud_day), api_score=res["score"], ref_score=float(ref.score.iloc[i]), api_band=res["band"], ref_band=ref.band.iloc[i],
                         verdict=res["verdict"], gate=",".join(res["gate_hits"])))
df = pd.DataFrame(rows)
rho = spearmanr(df.api_score, df.ref_score).statistic
agree = (df.api_band == df.ref_band).mean()
fd = df[df.fraud_day == 1]
report["replay"] = dict(customer_days=len(df), customers=len(ids), fraud_days=int(len(fd)), spearman_api_vs_reference=round(float(rho), 4), band_agreement=round(float(agree), 4),
                        fraud_days_flagged_api=round(float((fd.api_band != "genuine").mean()), 3) if len(fd) else None,
                        fraud_days_flagged_reference=round(float((fd.ref_band != "genuine").mean()), 3) if len(fd) else None,
                        normal_days_flagged_api=round(float((df[df.fraud_day == 0].api_band != "genuine").mean()), 4),
                        normal_days_flagged_reference=round(float((df[df.fraud_day == 0].ref_band != "genuine").mean()), 4),
                        verdicts=df.verdict.value_counts().to_dict())
print("2. replay vs reference:", json.dumps(report["replay"], indent=1))
json.dump(report, open(os.path.join(HERE, "test_report.json"), "w"), indent=1, default=str)
df.to_csv(os.path.join(HERE, "test_replay_scores.csv"), index=False)
assert rho >= 0.9 and agree >= 0.9, "replay deviates too much from the reference path"
print("ALL SERVICE CHECKS PASSED")
