"""
Fraud-gate scoring service (stand-in for the SAS MAS REST endpoint in the proposal's architecture, section 8).

    POST /events          stream transactions / security events into the feature store (today's running daily record)
    POST /score           rolling features -> model score -> deterministic gate (6.3) -> verdict + reason codes + decision log
    GET  /customers/{id}/features
    GET  /health  /ready  /metrics

Env: STORE_PATH (sqlite file), BUNDLE_DIR (exported model bundle), LOG_PATH (decision log, JSONL).
"""
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from typing import List, Optional

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUNDLE_DIR = os.environ.get("BUNDLE_DIR", os.path.join(ROOT, "bundle", "fraud_xgb_mask_v1_1"))   # v1.1: no protected attributes (proposal 6.4)
STORE_PATH = os.environ.get("STORE_PATH", os.path.join(HERE, "demo.db"))
LOG_PATH = os.environ.get("LOG_PATH", os.path.join(HERE, "decision_log.jsonl"))
sys.path.insert(0, HERE)
sys.path.insert(0, BUNDLE_DIR)
from score import FraudScorer  # noqa: E402
import gate  # noqa: E402
from store import FeatureStore  # noqa: E402
from limit_service import LimitService  # noqa: E402

app = FastAPI(title="Fraud gate scoring service", version="1.0")
scorer = FraudScorer(BUNDLE_DIR)
store = FeatureStore(STORE_PATH)
limits = LimitService(store, scorer)
MODEL_VERSION = f"{scorer.schema['model']}@{scorer.schema['version']}"
M = dict(requests=Counter(), verdict=Counter(), band=Counter(), quality=Counter(), lat_sum=0.0, lat_n=0, lat_bucket=Counter())
BUCKETS = (10, 25, 50, 100, 250, 500, 1000)


class Event(BaseModel):
    customer_id: str
    ts: str
    type: str                                   # transfer | inflow | device_new | credential_change | fraud_alert | dispute
    amount: float = 0.0
    payee_id: Optional[str] = None
    category: Optional[str] = "transfer_p2p"
    geo_mismatch: bool = False
    declined_by_limit: bool = False
    balance_after: Optional[float] = None


class ScoreRequest(BaseModel):
    customer_id: str
    as_of: Optional[str] = None                 # ISO timestamp; default = now (use a fixed date when replaying history)
    reasons: bool = True


MESSAGES = {
    "HOLD": "A transfer was held for your safety ({why}). Confirm in the app after step-up authentication or call us.",
    "STEP_UP_AUTH": "Please confirm this transfer with step-up authentication ({why}).",
    "ALLOW": "",
}


def verdict_of(band, g):
    if g["hold"] or band == "high_risk":
        return "HOLD"
    if g["step_up"] or band == "suspicious":
        return "STEP_UP_AUTH"
    return "ALLOW"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    n = store.customers_count()
    if n == 0:
        raise HTTPException(503, "feature store empty")
    return {"status": "ready", "customers": n, "model": MODEL_VERSION}


@app.post("/events")
def events(evs: List[Event]):
    try:
        for e in evs:
            store.ingest(e.model_dump())
    except KeyError as ex:
        raise HTTPException(404, f"unknown customer {ex}")
    except ValueError as ex:
        raise HTTPException(422, str(ex))
    M["requests"]["events"] += len(evs)
    return {"ingested": len(evs)}


@app.get("/customers/{customer_id}/features")
def customer_features(customer_id: str, as_of: Optional[str] = None):
    try:
        feats, today, prof, ctx = store.features_for(customer_id, as_of or datetime.now())
    except KeyError:
        raise HTTPException(404, "unknown customer")
    return {"features": json.loads(feats.iloc[0].to_json()), "today": {k: (str(v) if k == "date" else v) for k, v in today.items()}, "context": ctx}


@app.post("/score")
def score(req: ScoreRequest):
    t0 = time.perf_counter()
    as_of = pd.Timestamp(req.as_of) if req.as_of else pd.Timestamp.now()
    try:
        feats, today, prof, ctx = store.features_for(req.customer_id, as_of)
    except KeyError:
        raise HTTPException(404, "unknown customer")
    m = scorer.score_df(feats, reasons=req.reasons).iloc[0]
    g = gate.evaluate(feats, today, prof, ctx, m.band)
    verdict = verdict_of(m.band, g)
    why = ", ".join([h["text"] for h in g["hits"]][:2]) or "unusual activity"
    out = dict(customer_id=req.customer_id, as_of=str(as_of), model_version=MODEL_VERSION, score=float(m.score), band=m.band, verdict=verdict,
               block_increase=bool(g["block_increase"] or m.band != "genuine"), hold=verdict == "HOLD", gate_hits=g["codes"],
               reasons=list(m.get("reasons", [])), quality=m.quality, missing_share=float(m.missing_share), missing_groups=list(m.missing_groups),
               customer_message=MESSAGES[verdict].format(why=why), tier=int(prof["current_tier"]), cap=prof["cap"])
    lat = (time.perf_counter() - t0) * 1000
    out["latency_ms"] = round(lat, 1)
    M["requests"]["score"] += 1; M["verdict"][verdict] += 1; M["band"][m.band] += 1; M["quality"][m.quality] += 1; M["lat_sum"] += lat; M["lat_n"] += 1
    for b in BUCKETS:
        if lat <= b:
            M["lat_bucket"][b] += 1
    with open(LOG_PATH, "a") as fh:                       # decision log (proposal 7): inputs, scores, action, reason codes, model version
        fh.write(json.dumps(dict(out, features=json.loads(feats.iloc[0].to_json())), default=str) + "\n")
    return out


class LimitReq(BaseModel):
    customer_id: str
    as_of: str                                  # evaluation date (the 02:00 batch of that day uses the previous day's features)


class SelfCapReq(LimitReq):
    tier: int


class AppealReq(LimitReq):
    note: str = ""


class ReviewReq(BaseModel):
    ticket: int
    decision: str                               # pin_tier | reject
    as_of: str
    tier: Optional[int] = None


def _limit_call(fn, *a, **kw):
    try:
        out = fn(*a, **kw)
    except KeyError as ex:
        raise HTTPException(404, f"unknown {ex}")
    M["requests"]["limit"] += 1
    with open(LOG_PATH, "a") as fh:
        fh.write(json.dumps(dict(kind="limit", **out) if isinstance(out, dict) else dict(kind="limit", out=str(out)), default=str) + "\n")
    return out


@app.post("/limit/evaluate")
def limit_evaluate(r: LimitReq):
    return _limit_call(limits.evaluate, r.customer_id, r.as_of)


@app.post("/limit/accept")
def limit_accept(r: LimitReq):
    return _limit_call(limits.accept, r.customer_id, r.as_of)


@app.post("/limit/self-cap")
def limit_self_cap(r: SelfCapReq):
    return _limit_call(limits.set_self_cap, r.customer_id, r.tier, r.as_of)


@app.post("/limit/appeal")
def limit_appeal(r: AppealReq):
    return _limit_call(limits.appeal, r.customer_id, r.as_of, r.note)


@app.post("/limit/review")
def limit_review(r: ReviewReq):
    return _limit_call(limits.review, r.ticket, r.decision, r.as_of, r.tier)


@app.post("/batch/daily")
def batch_daily(date: str):
    """The 02:00 batch (proposal 8.1): evaluate every customer in the store for `date`."""
    ids = [r[0] for r in store.db.execute("SELECT customer_id FROM customers")]
    acts = Counter()
    for cid in ids:
        acts[limits.evaluate(cid, date)["action"]] += 1
    return {"date": date, "customers": len(ids), "actions": dict(acts)}


@app.get("/limit/{customer_id}")
def limit_state(customer_id: str):
    return _limit_call(limits.get, customer_id)


@app.get("/metrics")
def metrics():
    L = ["# TYPE fraud_requests_total counter"] + [f'fraud_requests_total{{endpoint="{k}"}} {v}' for k, v in M["requests"].items()]
    L += ["# TYPE fraud_verdict_total counter"] + [f'fraud_verdict_total{{verdict="{k}"}} {v}' for k, v in M["verdict"].items()]
    L += ["# TYPE fraud_band_total counter"] + [f'fraud_band_total{{band="{k}"}} {v}' for k, v in M["band"].items()]
    L += ["# TYPE fraud_quality_total counter"] + [f'fraud_quality_total{{quality="{k}"}} {v}' for k, v in M["quality"].items()]
    L += ["# TYPE fraud_score_latency_ms histogram"] + [f'fraud_score_latency_ms_bucket{{le="{b}"}} {M["lat_bucket"][b]}' for b in BUCKETS]
    L += [f'fraud_score_latency_ms_bucket{{le="+Inf"}} {M["lat_n"]}', f'fraud_score_latency_ms_sum {M["lat_sum"]:.2f}', f'fraud_score_latency_ms_count {M["lat_n"]}']
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(L) + "\n")
