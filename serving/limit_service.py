"""
Limit service (proposal section 8: 'limit service' + 'decision flow'): holds each customer's limit state (tier, cooldown timers, pending offer, self-set cap,
reviewer pin) and runs one policy evaluation = scorecard + capacity forecast + fraud gate -> PolicyEngine (limit/policy.py).

    evaluate(customer, as_of)   the 02:00 batch decision for one customer (features through the previous day)
    accept(customer, as_of)     customer accepts a pending offer (applied after the 24 h delay, gate re-checked)
    set_self_cap(customer, tier) customer lowers their own limit (instant); raising goes through the offer path
    appeal / review             human review within 2 working days; a reviewer can pin a tier for 6 months
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "limit"))
from forecast import QuantileForecast, add_calendar  # noqa: E402
from policy import ACTIONS, CAP, PolicyEngine  # noqa: E402
from scorecard import Scorecard  # noqa: E402
import gate  # noqa: E402

WIN_START = pd.Timestamp("2025-07-01")
STATE_COLS = ["tier", "tier_since", "last_change", "consec_lower", "offer_to", "offer_day", "accepted_day", "dismissed_until", "self_cap", "pinned_until"]
MSG = {
    "OFFER_INCREASE": "Your daily transfer limit can rise to Rp{cap:.0f} million because of your stable income, no disputes and a growing transfer need. Accept? It takes effect 24 hours after you accept.",
    "APPLY_INCREASE": "Your daily transfer limit is now Rp{cap:.0f} million.",
    "STEP_DOWN": "Your limit moved to Rp{cap:.0f} million because activity dropped over the last weeks. You can request a review and we will reply within 2 working days.",
    "GATE_STEP_DOWN": "For your safety your limit was lowered to Rp{cap:.0f} million after unusual activity. You can request a review and we will reply within 2 working days.",
    "HOLD": "A transfer was held for your safety. Confirm in the app after step-up authentication or call us.",
    "CANCEL_OFFER": "Your pending limit increase was cancelled for security reasons. You can ask for a review.",
    "EXPIRE_OFFER": "Your limit offer expired. You will receive a new one after 30 days if you still qualify.",
    "REVIEW_QUEUE": "Your request for the highest tier is waiting for a manual review.",
    "KEEP": "",
}


class LimitService:
    def __init__(self, store, scorer, art_dir=None):
        art = art_dir or os.path.join(ROOT, "limit", "artifacts")
        self.store, self.scorer = store, scorer
        self.card = Scorecard.from_json(os.path.join(art, "scorecard.json"))
        self.fc = QuantileForecast.load(os.path.join(art, "forecast_q95.json"))
        self.cuts = tuple(json.load(open(os.path.join(art, "tier_cuts.json")))["score_cut"])
        with store.lock:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS limits (customer_id TEXT PRIMARY KEY, baseline_tier INTEGER, tier INTEGER, tier_since INTEGER, last_change INTEGER, consec_lower INTEGER,
                offer_to INTEGER, offer_day INTEGER, accepted_day INTEGER, dismissed_until INTEGER, self_cap INTEGER, pinned_until INTEGER, last_eval INTEGER);
            CREATE TABLE IF NOT EXISTS appeals (ticket INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, day INTEGER, status TEXT, note TEXT);
            """)

    # ------------------------------------------------------------------ state
    def _state(self, cid):
        r = self.store.db.execute(f"SELECT {','.join(STATE_COLS)}, baseline_tier FROM limits WHERE customer_id=?", (cid,)).fetchone()
        if r is None:
            base = int(self.store.profile(cid)["baseline_tier"])
            return dict(zip(STATE_COLS, [base, -10_000, -10_000, 0, 0, -1, -1, -1, 0, -1]), baseline_tier=base)
        return dict(zip(STATE_COLS + ["baseline_tier"], r))

    def _engine(self, st):
        e = PolicyEngine([st["baseline_tier"]], {"score_cut": self.cuts})
        for c in STATE_COLS: setattr(e, c, np.array([st[c]], dtype=int))
        return e

    def _save(self, cid, e, t, base):
        st = {c: int(getattr(e, c)[0]) for c in STATE_COLS}
        with self.store.lock:
            self.store.db.execute(f"INSERT OR REPLACE INTO limits (customer_id, baseline_tier, {','.join(STATE_COLS)}, last_eval) VALUES (?,?,{','.join('?' * len(STATE_COLS))},?)", [cid, base, *st.values(), t])
            self.store.db.execute("UPDATE customers SET current_tier=? WHERE customer_id=?", (st["tier"], cid)); self.store.db.commit()
        return st

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, cid, as_of, accept=False):
        as_of = pd.Timestamp(as_of).normalize(); t = (as_of - WIN_START).days
        st = self._state(cid); e = self._engine(st); prof = self.store.profile(cid)
        y_date = as_of - pd.Timedelta(days=1)                                               # the batch uses features through the previous day
        feats, today, prof, ctx = self.store.features_for(cid, y_date)
        hist = self.store.history([cid], y_date)
        cap_now = float(CAP[st["tier"]])
        extras = dict(near_limit_days_90=float((hist.outflow_amt >= 0.8 * cap_now).sum()), declined_cnt_90=float(hist.n_declined_limit.sum()))
        row = feats.iloc[[0]].assign(snap_date=y_date, **extras)
        score = float(self.card.score(row)[0]); forecast = float(self.fc.predict(add_calendar(row))[0])
        m = self.scorer.score_df(feats, reasons=False).iloc[0]
        g = gate.evaluate(feats, today, prof, ctx, m.band)
        f = feats.iloc[0]
        tenure_true = (as_of - pd.Timestamp(prof["open_date"])).days / 30.4
        inp = dict(score=np.array([score]), tenure_months=np.array([tenure_true]), regular_windows=np.array([f.regular_inflow_windows_90]), disputes_90=np.array([float(hist.n_dispute.sum())]),
                   kyc_age_months=np.array([f.kyc_age_months]), forecast=np.array([forecast]), feature_ok=np.array([f.hist_days >= 30]),
                   active_recent=np.array([(f.n_out_7 > 0) or (f.out_30 > 0)]), gate_block=np.array([bool(g["block_increase"] or m.band != "genuine")]), gate_hold=np.array([bool(g["hold"] or m.band == "high_risk")]))
        act, tgt, prev = e.step(t, inp, np.array([bool(accept)]))
        a = ACTIONS[int(act[0])]
        new = self._save(cid, e, t, st["baseline_tier"])
        ceil, dem = int(e.ceiling_tier(inp)[0]), int(e.demand_tier(inp["forecast"])[0])
        return dict(customer_id=cid, as_of=str(as_of.date()), action=a, tier_before=int(prev[0]), tier_after=new["tier"], cap_after_million=float(CAP[new["tier"]] / 1e6),
                    offer=dict(to_tier=new["offer_to"], to_cap_million=float(CAP[new["offer_to"]] / 1e6) if new["offer_to"] else None) if new["offer_to"] else None,
                    score_points=round(score, 1), reason_codes=self.card.reason_codes(row, 3)[0], forecast_peak_need_million=round(forecast / 1e6, 1), ceiling_tier=ceil, demand_tier=dem,
                    gate_hits=g["codes"], fraud_band=m.band, message=MSG[a].format(cap=CAP[new["tier"] if a != "OFFER_INCREASE" else new["offer_to"]] / 1e6),
                    transparency=dict(factors=["scorecard points and reason codes", "30-day peak-need forecast", "fraud gate checks", "tenure, regular inflow, disputes, KYC age"],
                                      how_to_object="POST /limit/appeal (human review within 2 working days)"))

    def accept(self, cid, as_of):
        return self.evaluate(cid, as_of, accept=True)

    def set_self_cap(self, cid, tier, as_of):
        t = (pd.Timestamp(as_of).normalize() - WIN_START).days
        st = self._state(cid); e = self._engine(st); e.self_cap[:] = int(tier)
        if st["tier"] > tier: e.tier[:] = int(tier); e.last_change[:] = t; e.tier_since[:] = t
        e.offer_to[:] = np.where(e.offer_to > tier, 0, e.offer_to)
        return self._save(cid, e, t, st["baseline_tier"])

    def appeal(self, cid, as_of, note=""):
        t = (pd.Timestamp(as_of).normalize() - WIN_START).days
        with self.store.lock:
            cur = self.store.db.execute("INSERT INTO appeals (customer_id, day, status, note) VALUES (?,?,?,?)", (cid, t, "open", note)); self.store.db.commit()
        return dict(ticket=cur.lastrowid, status="open", sla="human review within 2 working days")

    def review(self, ticket, decision, as_of, tier=None):
        t = (pd.Timestamp(as_of).normalize() - WIN_START).days
        r = self.store.db.execute("SELECT customer_id FROM appeals WHERE ticket=?", (ticket,)).fetchone()
        if r is None: raise KeyError(ticket)
        cid = r[0]; st = self._state(cid); e = self._engine(st)
        if decision == "pin_tier":                                                           # reviewer pins a tier for 6 months (4.4)
            e.tier[:] = int(tier); e.pinned_until[:] = t + 182; e.last_change[:] = t; e.tier_since[:] = t; e.offer_to[:] = 0
        self._save(cid, e, t, st["baseline_tier"])
        with self.store.lock:
            self.store.db.execute("UPDATE appeals SET status=? WHERE ticket=?", (decision, ticket)); self.store.db.commit()
        return dict(ticket=ticket, customer_id=cid, decision=decision, tier=int(e.tier[0]), pinned_until_day=int(e.pinned_until[0]))

    def get(self, cid):
        st = self._state(cid); return dict(customer_id=cid, **st, cap_million=float(CAP[st["tier"]] / 1e6))
