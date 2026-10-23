#!/usr/bin/env python3
"""Sanity + realism checks for data/dev. Usage: python3 validate_v2.py ../data/dev"""
import glob
import sys

import numpy as np
import pandas as pd

d = sys.argv[1] if len(sys.argv) > 1 else "../data/dev"
c = pd.read_csv(f"{d}/customers.csv")
t = pd.read_csv(f"{d}/_truth.csv")
ev = pd.read_csv(f"{d}/fraud_events.csv")
ok = lambda b: "OK  " if b else "FAIL"
fails = 0


def chk(b, msg):
    global fails
    fails += (not b)
    print(ok(b), msg)


print("== structure")
parts = sorted(glob.glob(f"{d}/customer_daily/part-*.parquet"))
x = pd.concat([pd.read_parquet(p) for p in parts[:10]], ignore_index=True)  # first 10k customers is enough
print(f"customers={len(c):,}  parts={len(parts)}  rows in sample={len(x):,}  columns={x.shape[1]}")
chk(c.customer_id.is_unique, "customer_id unique")
chk(x.groupby(["customer_id", "date"]).size().max() == 1, "one row per customer-day")
chk((x.end_balance >= 0).all(), "balance never negative")
chk((x.outflow_amt <= x.cap_applied + 1000).all(), "realized outflow <= cap")
chk((x.n_new_payee <= x.n_outflow).all(), "new payees <= outflow count")
chk((x.n_distinct_payee <= x.n_outflow).all() and (x.n_distinct_payee >= x.n_new_payee).all(), "distinct payees between new payees and outflow count")
chk((x.night_outflow_amt <= x.outflow_amt).all(), "night outflow <= outflow")
chk((x.n_geo_mismatch <= x.n_outflow).all(), "geo mismatch <= outflow count")
chk(((x[[c_ for c_ in x if c_.startswith("out_")]].sum(1) - x.outflow_amt).abs() <= 8000).all(), "merchant-category amounts sum to outflow")

print("\n== fraud labels")
tt = t.merge(c[["customer_id", "age_band", "gender", "region", "tenure_months"]], on="customer_id")
fr = tt.persona.isin(["scam", "mule", "ato"])
print(f"fraud customers {fr.sum():,} of {len(tt):,} ({fr.mean():.2%});  events {len(ev):,};  reported {ev.reported.mean():.0%};  stealth {ev.stealth.mean():.0%}")
print(ev.groupby("fraud_type").agg(n=("customer_id", "size"), stealth=("stealth", "mean"), reported=("reported", "mean")).round(2).to_string())
chk(0.02 <= fr.mean() <= 0.05, "fraud customer rate in 2-5% band")
chk(0.4 <= ev.stealth.mean() <= 0.5, "stealth share ~45%")

print("\n== shortcut check: profile must NOT identify fraud (fraud rate by group, customer level)")
for k in ("gender", "age_band"):
    r = tt.groupby(k).apply(lambda g: g.persona.isin(["scam", "mule", "ato"]).mean()).round(3)
    print(k, r.to_dict())
r = tt.groupby("region").apply(lambda g: g.persona.isin(["scam", "mule", "ato"]).mean())
print(f"region fraud rate min/max: {r.min():.3f} / {r.max():.3f}")
print(f"tenure months: fraud median {tt[fr].tenure_months.median():.0f} vs legit {tt[~fr].tenure_months.median():.0f}")
seg = c.merge(t[["customer_id", "persona"]], on="customer_id")
seg["fr"] = seg.persona.isin(["scam", "mule", "ato"])
for k in ("segment", "occupation"):
    print(k, "fraud share per category min/max:", seg.groupby(k).fr.mean().round(3).agg(["min", "max"]).to_dict())

print("\n== signature strength: fraud days vs normal days (sample)")
x = x.merge(t[["customer_id", "persona", "stealth"]], on="customer_id")
x["night_share"] = x.night_outflow_amt / x.outflow_amt.replace(0, np.nan)
fd = x._fraud_day == 1
cols = ["night_share", "n_new_payee", "n_distinct_payee", "top_payee_share", "n_geo_mismatch", "new_device", "cred_change", "out_ewallet_topup", "out_transfer_p2p", "ftp_large"]
rep = pd.DataFrame({"normal": x[~fd][cols].mean(), "fraud_strong": x[fd & (x.stealth == 0)][cols].mean(), "fraud_stealth": x[fd & (x.stealth == 1)][cols].mean()})
print(rep.round(3).to_string())
chk((rep.fraud_stealth.drop("cred_change") < rep.fraud_strong.drop("cred_change") * 1.05).mean() > 0.6, "stealth fraud is weaker than strong fraud on most signals")

print("\n== hard negatives exist among legit customers (per customer-year)")
lg = x[x.persona.isin(["salaried", "gig", "sme", "seasonal", "student", "highvalue", "new", "dormant"])]
yrs = len(lg) / 365.0
print(f"new_device {lg.new_device.sum() / yrs:.2f}  cred_change {lg.cred_change.sum() / yrs:.2f}  false alerts {lg.n_fraud_alert.sum() / yrs:.2f}  "
      f"ftp_large {lg.ftp_large.sum() / yrs:.2f}  geo-mismatch days {(lg.n_geo_mismatch > 0).sum() / yrs:.1f}")
print(f"legit days with night_share>0.5: {(lg.night_share > 0.5).mean():.3%}   ewallet/gambling>20% of outflow: "
      f"{(lg.out_gambling_crypto > 0.2 * lg.outflow_amt)[lg.outflow_amt > 0].mean():.3%}")

print(f"\n{'ALL CHECKS PASSED' if fails == 0 else str(fails) + ' CHECK(S) FAILED'}")
