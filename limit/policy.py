"""
Dynamic limit policy engine (proposal 4.2 tiers, 4.3 policy rules, 4.4 customer safeguards, 7 decision flow).

Vectorised across customers: one call to `PolicyEngine.step(t, inp, accept_today)` = one daily evaluation (the 02:00 batch, using features
as of the previous day).  The same engine serves the simulation (arrays of thousands of customers) and the limit service (arrays of length 1).

Decision flow implemented (section 7):
    IF gate.any_hit            -> NO increase;  gate hold-hit -> HOLD (and, above baseline, an immediate one-tier step-down, appealable)
    ELIF not eligible (tenure < 12 m, data < 6 m, abstain, pinned by a reviewer)  -> KEEP (baseline tier; never lowered by the model)
    ELSE ceiling = tier_from_score(...), demand = tier_from_forecast(...), rec = MIN(ceiling, demand, current + 1)
         rec > current and cooldown >= 7 d  -> OFFER_INCREASE (customer accepts -> applied after a 24 h delay, re-checked by the gate)
         rec < current for 3 consecutive evaluations -> STEP_DOWN (never below the baseline tier, never because of dormancy)
         otherwise KEEP
Non-negotiable floor (4.4): a customer is never worse off than the baseline tier unless the fraud gate fires on live evidence.
"""
import numpy as np

CAP = np.array([0, 10e6, 25e6, 50e6, 100e6, 200e6])
ACTIONS = ["KEEP", "OFFER_INCREASE", "APPLY_INCREASE", "STEP_DOWN", "HOLD", "CANCEL_OFFER", "EXPIRE_OFFER", "REVIEW_QUEUE", "GATE_STEP_DOWN"]
A = {n: i for i, n in enumerate(ACTIONS)}

DEFAULT_POLICY = dict(
    score_cut=(0, 0, 0, 0, 0),             # minimum scorecard points for tier 1..5 (index tier-1); set from the scorecard distribution
    min_tenure_months=12, min_data_months=6, regular_windows=3, max_disputes=0,
    forecast_ratio=0.6,                    # tier k needs a forecast above 60% of the cap of tier k-1  (4.2 entry conditions)
    months_at_prior_tier=6,                # for entry into T3+
    kyc_max_months={4: 24, 5: 12}, t5_manual_review=True,
    cooldown_days=7, increase_delay_days=1, lower_needed=14,       # proposal says 3 consecutive evaluations; with DAILY evaluation that is only 3 days -> 14 (see notebook 05, sensitivity)
     offer_expiry_days=14, reoffer_days=30,
    gate_stepdown=True, stale_after_days=2,
)


class PolicyEngine:
    def __init__(self, baseline_tier, policy=None):
        self.p = {**DEFAULT_POLICY, **(policy or {})}
        n = len(baseline_tier)
        self.baseline = np.asarray(baseline_tier, int)
        self.tier = self.baseline.copy()
        self.tier_since = np.full(n, -10_000)
        self.last_change = np.full(n, -10_000)
        self.consec_lower = np.zeros(n, int)
        self.offer_to = np.zeros(n, int); self.offer_day = np.full(n, -1); self.accepted_day = np.full(n, -1)
        self.dismissed_until = np.full(n, -1)
        self.self_cap = np.zeros(n, int)
        self.pinned_until = np.full(n, -1)

    def cap(self): return CAP[self.tier]

    def ceiling_tier(self, inp):
        p = self.p; cut = p["score_cut"]; s = inp["score"]
        ok2 = (inp["regular_windows"] >= p["regular_windows"]) & (inp["disputes_90"] <= p["max_disputes"]) & (s >= cut[1])
        ok3 = ok2 & (s >= cut[2])
        ok4 = ok3 & (s >= cut[3]) & (inp["kyc_age_months"] <= p["kyc_max_months"][4])
        ok5 = ok4 & (s >= cut[4]) & (inp["kyc_age_months"] <= p["kyc_max_months"][5])
        return 1 + ok2.astype(int) + ok3 + ok4 + ok5

    def demand_tier(self, forecast):
        r = self.p["forecast_ratio"]
        return 1 + sum((forecast > r * CAP[k - 1]).astype(int) for k in range(2, 6))

    def step(self, t, inp, accept_today=None):
        """inp keys (arrays): score, tenure_months, regular_windows, disputes_90, kyc_age_months, forecast, feature_ok, active_recent,
        gate_block (any gate hit), gate_hold (hold-type hit). Returns (action codes, new target tier or 0)."""
        p, n = self.p, len(self.tier)
        act = np.zeros(n, int); tgt = np.zeros(n, int)
        prev_tier = self.tier.copy()

        # -- customer self-lowering is honoured instantly (and is the ceiling for offers)
        lowered = (self.self_cap > 0) & (self.tier > self.self_cap)
        self.tier = np.where(lowered, self.self_cap, self.tier); self.last_change = np.where(lowered, t, self.last_change)

        # -- offer lifecycle: acceptance, delayed application (gate re-check), expiry
        if accept_today is not None:
            newly = accept_today & (self.offer_to > 0) & (self.accepted_day < 0)
            self.accepted_day = np.where(newly, t, self.accepted_day)
        due = (self.offer_to > 0) & (self.accepted_day >= 0) & (t >= self.accepted_day + p["increase_delay_days"])
        cancel = due & inp["gate_block"]
        apply_ = due & ~inp["gate_block"] & ((self.self_cap == 0) | (self.offer_to <= self.self_cap))
        act[apply_] = A["APPLY_INCREASE"]; tgt[apply_] = self.offer_to[apply_]
        self.tier = np.where(apply_, self.offer_to, self.tier); self.tier_since = np.where(apply_, t, self.tier_since); self.last_change = np.where(apply_, t, self.last_change)
        act[cancel] = A["CANCEL_OFFER"]
        expire = (self.offer_to > 0) & (self.accepted_day < 0) & (t - self.offer_day > p["offer_expiry_days"])
        act[expire] = A["EXPIRE_OFFER"]
        self.dismissed_until = np.where(expire | cancel, t + p["reoffer_days"], self.dismissed_until)
        clear = apply_ | cancel | expire
        self.offer_to = np.where(clear, 0, self.offer_to); self.accepted_day = np.where(clear, -1, self.accepted_day); self.offer_day = np.where(clear, -1, self.offer_day)

        # -- fraud gate: hold now; above the baseline also step down one tier immediately
        hold = inp["gate_hold"]
        gstep = hold & (self.tier > self.baseline) & p["gate_stepdown"]
        act[hold] = np.where(act[hold] == 0, A["HOLD"], act[hold])
        act[gstep] = A["GATE_STEP_DOWN"]; tgt[gstep] = np.maximum(self.tier[gstep] - 1, self.baseline[gstep])
        self.tier = np.where(gstep, np.maximum(self.tier - 1, self.baseline), self.tier); self.last_change = np.where(gstep, t, self.last_change)
        self.offer_to = np.where(gstep, 0, self.offer_to); self.accepted_day = np.where(gstep, -1, self.accepted_day)

        # -- model-driven recommendation (eligible, no gate hit today)
        elig = (inp["tenure_months"] >= p["min_tenure_months"]) & inp["feature_ok"] & (t >= self.pinned_until) & ~inp["gate_block"]
        ceil, dem = self.ceiling_tier(inp), self.demand_tier(inp["forecast"])
        rec = np.minimum(np.minimum(ceil, dem), self.tier + 1)
        rec = np.where((self.self_cap > 0), np.minimum(rec, self.self_cap), rec)
        months_at = (t - self.tier_since) / 30.4
        rec = np.where((rec >= 3) & (rec > self.tier) & (months_at < p["months_at_prior_tier"]), self.tier, rec)
        cool = (t - self.last_change) >= p["cooldown_days"]

        want_up = elig & (rec > self.tier) & cool & (self.offer_to == 0) & (t >= self.dismissed_until) & (act == 0)
        review = want_up & (rec == 5) & p["t5_manual_review"]
        offer = want_up & ~review
        act[review] = A["REVIEW_QUEUE"]
        act[offer] = A["OFFER_INCREASE"]; tgt[offer] = rec[offer]
        self.offer_to = np.where(offer, rec, self.offer_to); self.offer_day = np.where(offer, t, self.offer_day)

        target_low = np.maximum(rec, self.baseline)
        lower = elig & (target_low < self.tier) & inp["active_recent"]
        self.consec_lower = np.where(lower, self.consec_lower + 1, 0)
        down = lower & (self.consec_lower >= p["lower_needed"]) & cool & (act == 0)
        act[down] = A["STEP_DOWN"]; tgt[down] = target_low[down]
        self.tier = np.where(down, target_low, self.tier); self.last_change = np.where(down, t, self.last_change)
        self.tier_since = np.where(down | gstep | lowered, t, self.tier_since); self.consec_lower = np.where(down, 0, self.consec_lower)
        self.offer_to = np.where(down, 0, self.offer_to)
        return act, tgt, prev_tier

    # customer-initiated actions (limit service)
    def set_self_cap(self, idx, tier): self.self_cap[idx] = tier
    def pin(self, idx, until_day): self.pinned_until[idx] = until_day
