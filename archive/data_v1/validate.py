#!/usr/bin/env python3
"""Sanity checks for the synthetic tables. Usage: python3 validate.py out"""
import sys
import numpy as np
import pandas as pd

d = sys.argv[1] if len(sys.argv) > 1 else "out"
c = pd.read_csv(f"{d}/customers.csv")
x = pd.read_csv(f"{d}/customer_daily.csv", parse_dates=["date"])
t = pd.read_csv(f"{d}/_truth.csv")
x = x.merge(t[["customer_id", "persona"]], on="customer_id")
c = c.merge(t[["customer_id", "persona"]], on="customer_id")
ok = lambda b: "OK  " if b else "FAIL"

print("== structure")
print(f"customers={len(c):,}  daily rows={len(x):,}  date range {x.date.min().date()} -> {x.date.max().date()}")
print(ok(c.customer_id.is_unique), "customer_id unique")
print(ok(x.groupby(["customer_id", "date"]).size().max() == 1), "one row per customer-day")
print(ok((x.end_balance >= 0).all()), "balance never negative")
print(ok((x.outflow_amt <= x.cap_applied + 1000).all()), "realized outflow <= cap")
print(ok((x.n_new_payee <= x.n_outflow).all()), "new payees <= outflow count")
print(ok((x.night_outflow_amt <= x.outflow_amt).all()), "night outflow <= outflow")
print(ok((x.intended_amount >= x.outflow_amt).all()), "intended >= realized")

print("\n== persona mix")
print(c.persona.value_counts().to_string())

print("\n== baseline tier mix")
print(c.baseline_tier.value_counts(normalize=True).sort_index().round(3).to_string())

# customer-month view
x["ym"] = x.date.dt.to_period("M")
m = x.groupby(["customer_id", "ym"]).agg(inflow=("inflow_amt", "sum"), outflow=("outflow_amt", "sum"),
                                        decl=("n_declined_limit", "sum"), p95=("outflow_amt", lambda s: s.quantile(.95))).reset_index()
m = m.merge(t[["customer_id", "persona"]], on="customer_id")

print("\n== average monthly outflow and inflow (Rp million) by persona")
g = m.groupby("persona")[["inflow", "outflow"]].mean() / 1e6
print(g.round(1).to_string())

print("\n== declined-by-limit: share of customers with >=1 decline in last 90 days, by baseline tier")
last = x[x.date > x.date.max() - pd.Timedelta(days=90)]
dd = last.groupby("customer_id").n_declined_limit.sum().gt(0).rename("hit").reset_index().merge(c[["customer_id", "baseline_tier", "persona"]])
print(dd.groupby("baseline_tier").hit.mean().round(3).to_string())
print("\n   by persona:")
print(dd.groupby("persona").hit.mean().round(3).to_string())
tot_decl = x.n_declined_limit.sum()
print(f"\n   total declined-by-limit events: {tot_decl:,}  ({(x.n_declined_limit > 0).mean():.2%} of customer-days)")

print("\n== regularity: months with an inflow >= 50% of declared income (salaried-like personas)")
mm = m.merge(c[["customer_id", "declared_income"]], on="customer_id")
mm["regular"] = mm.inflow >= 0.5 * mm.declared_income
print(mm.groupby("persona").regular.mean().round(2).to_string())

print("\n== scam / mule signatures (should stand out)")
sc = t[t.persona == "scam"].dropna(subset=["scam_event_date"])
ev = x.merge(sc[["customer_id", "scam_event_date"]], on="customer_id")
ev["is_event"] = ev.date == pd.to_datetime(ev.scam_event_date)
print(f"scam customers: {len(sc)}; event-day mean night share {ev[ev.is_event].night_outflow_amt.sum() / max(1, ev[ev.is_event].outflow_amt.sum()):.2f} "
      f"vs normal days {ev[~ev.is_event].night_outflow_amt.sum() / max(1, ev[~ev.is_event].outflow_amt.sum()):.2f}")
print(f"   event-day avg new payees {ev[ev.is_event].n_new_payee.mean():.2f} vs normal {ev[~ev.is_event].n_new_payee.mean():.2f}")
print(f"   event-day share with a limit decline {(ev[ev.is_event].n_declined_limit > 0).mean():.2f}")
mu = t[t.persona == "mule"].dropna(subset=["mule_start"]).copy()
mu["ms"], mu["me"] = pd.to_datetime(mu.mule_start), pd.to_datetime(mu.mule_end)
xm = x.merge(mu[["customer_id", "ms", "me"]], on="customer_id")
inrun = (xm.date >= xm.ms) & (xm.date < xm.me)
print(f"mule customers: {len(mu)}; in-run: inflow/day {xm[inrun].inflow_amt.mean() / 1e6:.1f}M, outflow/inflow {xm[inrun].outflow_amt.sum() / max(1, xm[inrun].inflow_amt.sum()):.2f}, "
      f"avg end balance {xm[inrun].end_balance.mean() / 1e6:.2f}M, n_new_payee {xm[inrun].n_new_payee.mean():.2f}")

print("\n== seasonality (avg daily outflow per active customer, Rp thousand) for seasonal vs salaried")
sx = x[x.persona.isin(["seasonal", "salaried"])].copy()
sx["mo"] = sx.date.dt.to_period("M")
print((sx.groupby(["mo", "persona"]).outflow_amt.mean().unstack() / 1e3).round(0).to_string())

print("\n== dormant customers: inactive days and return")
dm = t[t.persona == "dormant"]
print(f"{len(dm)} dormant customers; mean dormant span {(pd.to_datetime(dm.dormant_to) - pd.to_datetime(dm.dormant_from)).dt.days.mean():.0f} days")

print("\n== file sizes")
import os
for f in ("customers.csv", "customer_daily.csv", "_truth.csv"):
    print(f"{f}: {os.path.getsize(f'{d}/{f}') / 1e6:.1f} MB")
