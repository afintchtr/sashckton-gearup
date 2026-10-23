"""Limit-service lifecycle test: batch decisions, offer -> accept -> 24 h delay -> applied, self-cap, appeal/review, invariants.  Usage: python serving/test_limit.py"""
import os, shutil, sys, tempfile, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
tmp = tempfile.mkdtemp(); shutil.copy(os.path.join(HERE, "demo.db"), os.path.join(tmp, "t.db"))
os.environ["STORE_PATH"] = os.path.join(tmp, "t.db"); os.environ["LOG_PATH"] = os.path.join(tmp, "log.jsonl")
from fastapi.testclient import TestClient
import app as A
c = TestClient(A.app)
d0 = pd.Timestamp("2026-08-15")
r = c.post("/batch/daily", params={"date": str(d0.date())}).json(); print("batch", r)
assert r["customers"] > 0 and sum(r["actions"].values()) == r["customers"]
# pick a customer that received an offer
offers = [row[0] for row in A.store.db.execute("SELECT customer_id FROM limits WHERE offer_to>0")]
assert offers, "no offers produced by the batch"
cid = offers[0]; s = c.get(f"/limit/{cid}").json(); print("offer for", cid, s["tier"], "->", s["offer_to"])
base = s["baseline_tier"]
r1 = c.post("/limit/accept", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=1)).date()))).json()
assert r1["action"] == "KEEP" and r1["tier_after"] == base, r1                       # accepted today, not applied yet (24 h delay)
r2 = c.post("/limit/evaluate", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=2)).date()))).json()
print("after delay:", r2["action"], r2["tier_before"], "->", r2["tier_after"], "|", r2["message"][:70])
assert r2["action"] in ("APPLY_INCREASE", "CANCEL_OFFER")                            # applied unless the gate objects at that moment
# self-cap: customer lowers own limit instantly and offers are suppressed afterwards
cap_t = max(1, base)
st = c.post("/limit/self-cap", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=3)).date()), tier=cap_t)).json(); assert st["tier"] == cap_t
r3 = c.post("/limit/evaluate", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=4)).date()))).json(); assert r3["tier_after"] <= cap_t and r3["action"] != "OFFER_INCREASE", r3
# appeal -> reviewer pins a tier
t = c.post("/limit/appeal", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=5)).date()), note="test")).json(); assert t["status"] == "open"
rv = c.post("/limit/review", json=dict(ticket=t["ticket"], decision="pin_tier", tier=base, as_of=str((d0 + pd.Timedelta(days=6)).date()))).json(); assert rv["tier"] == base
r4 = c.post("/limit/evaluate", json=dict(customer_id=cid, as_of=str((d0 + pd.Timedelta(days=7)).date()))).json(); assert r4["action"] == "KEEP"
# invariants over everyone after a second and third batch
for k in (3, 10):
    c.post("/batch/daily", params={"date": str((d0 + pd.Timedelta(days=k)).date())})
rows = A.store.db.execute("SELECT customer_id, baseline_tier, tier FROM limits").fetchall()
assert all(t >= min(b, 1) and t >= b - 0 or t >= b for _, b, t in rows if True), "tier below baseline"
below = [r for r in rows if r[2] < r[1]]; assert not below, below[:3]
print("limit lifecycle: PASSED | customers with state:", len(rows))
