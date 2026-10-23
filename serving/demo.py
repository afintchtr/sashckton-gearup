"""Live demo against a running server (default http://localhost:8080): replays a fraud customer and a normal customer day by day, then scores.
Usage: python serving/demo.py [base_url]"""
import json, os, sys, time
import httpx, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from replay import day_to_events
base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
c = httpx.Client(base_url=base, timeout=30)
rep = pd.read_parquet(os.path.join(HERE, "demo_replay.parquet"))
caps = {1: 10e6, 2: 25e6, 3: 50e6, 4: 100e6, 5: 200e6}
cust = pd.read_csv(os.path.join(os.path.dirname(HERE), "data/dev", "customers.csv")).set_index("customer_id")

def run(cid, until):
    out = None
    for d in sorted(rep[(rep.customer_id == cid) & (rep.date <= until)].date.unique()):
        row = rep[(rep.customer_id == cid) & (rep.date == d)].iloc[0]
        evs = day_to_events(row, caps[int(cust.loc[cid, "baseline_tier"])], seed=1)
        if evs: c.post("/events", json=evs).raise_for_status()
        out = c.post("/score", json=dict(customer_id=cid, as_of=(pd.Timestamp(d) + pd.Timedelta(hours=23, minutes=59)).isoformat())).json()
    return out

fr = rep[(rep._fraud_day == 1)].sort_values("date")
pick = fr[fr.stealth == 0].groupby("fraud_type").head(1)
cases = [(r.customer_id, r.date, f"FRAUD ({r.fraud_type})") for r in pick.itertuples()]
normal = rep[~rep.customer_id.isin(rep[rep._fraud_day == 1].customer_id)].customer_id.unique()[:2]
cases += [(n, rep.date.min() + pd.Timedelta(days=6), "NORMAL") for n in normal]
for cid, day, label in cases:
    r = run(cid, day)
    print(f"\n[{label}] {cid} on {pd.Timestamp(day).date()}  ->  score={r['score']:.4f} band={r['band']} VERDICT={r['verdict']} block_increase={r['block_increase']} quality={r['quality']}")
    print("   gate:", r["gate_hits"], "| top reasons:", r["reasons"][:2]); 
    if r["customer_message"]: print("   message:", r["customer_message"])
t = []
for _ in range(100):
    s = time.perf_counter(); c.post("/score", json=dict(customer_id=cases[0][0], as_of=str(pd.Timestamp(cases[0][1]) + pd.Timedelta(hours=23)), reasons=True)); t.append((time.perf_counter() - s) * 1000)
print(f"\nlatency /score over HTTP (100 calls, with reasons): p50 {np.percentile(t, 50):.0f} ms | p95 {np.percentile(t, 95):.0f} ms | max {max(t):.0f} ms")
print([l for l in c.get("/metrics").text.splitlines() if l.startswith(("fraud_verdict", "fraud_band", "fraud_requests"))])
