"""
Static vs dynamic limit simulation (the proposal's headline claim, 2.3 / 10).

Open-loop replay on synthetic customers: the policy engine decides from the features observed in the static world; outcomes are then recomputed
under the dynamic caps with the SAME transfer-realisation logic the data generator uses (intended transfers are capped; the excess is declined).
Known simplification: customer behaviour does not react to a raised limit (no feedback), so unmet demand that a higher cap would reveal is not modelled.

Scenarios
  S0  static baseline tiers, no model gate                      (today)
  S1  static baseline tiers + fraud gate (holds)
  S2  dynamic limits + fraud gate                               (proposal)
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "bundle", "fraud_xgb_mask_v1"))
import xgboost as xgb
from forecast import QuantileForecast, add_calendar
from policy import PolicyEngine, A, ACTIONS, CAP
from scorecard import Scorecard

WIN_START = pd.Timestamp("2025-07-01"); D = (pd.Timestamp("2026-09-30") - WIN_START).days + 1; T0 = 92


def apply_cap(tot, m, n, cap):
    """Generator's realisation logic: intended total/largest/count under a daily cap -> (realised outflow, declined count)."""
    avg = np.where(n > 0, tot / np.maximum(n, 1), 0.0)
    big = m > cap
    over = (tot > cap) | big
    rest = tot - m
    real = np.where(~over, tot, np.where(big, np.minimum(rest, cap), cap))
    extra = np.maximum(0.0, rest - cap)
    dec_big = 1 + np.where((extra > 0) & (avg > 0), np.ceil(extra / np.where(avg > 0, avg, 1)), 0)
    dec_small = np.where(avg > 0, np.maximum(1, np.ceil((tot - cap) / np.where(avg > 0, avg, 1))), 1)
    dec = np.where(~over, 0, np.where(big, dec_big, dec_small))
    return real, dec


def dense(df, ci, di, col, n, fill=np.nan):
    m = np.full((n, D), fill, np.float32); m[ci, di] = df[col].to_numpy(np.float32); return m


def prepare(n_customers=None, seed=1, data="data/sim"):
    """Heavy, policy-independent part: raw daily arrays, features, model scores (reused across scenarios)."""
    cust = pd.read_csv(f"{ROOT}/{data}/customers.csv")
    if n_customers: cust = cust.sample(n_customers, random_state=seed)
    ids = cust.customer_id.to_numpy(); n = len(ids); pos = pd.Index(ids)
    # ---------------- daily raw arrays
    parts = sorted(glob.glob(f"{ROOT}/{data}/customer_daily/part-*.parquet"))
    cols = ["customer_id", "date", "intended_amount", "max_attempt_amount", "outflow_amt", "n_outflow", "n_declined_limit", "night_outflow_amt", "n_fraud_alert", "n_dispute", "_fraud_day", "cap_applied", "end_balance"]
    raw = pd.concat([pd.read_parquet(p, columns=cols) for p in parts], ignore_index=True)
    raw = raw[raw.customer_id.isin(ids)]
    ci = pos.get_indexer(raw.customer_id); di = (raw.date - WIN_START).dt.days.to_numpy()
    R = {c: dense(raw, ci, di, c, n) for c in cols[2:11]}
    cap_static = np.nanmax(dense(raw, ci, di, "cap_applied", n), axis=1)
    baseline = cust.baseline_tier.to_numpy(int)
    assert (CAP[baseline] == cap_static).all(), "baseline tier / static cap mismatch"
    z = np.nan_to_num
    csum = lambda a: np.concatenate([np.zeros((n, 1)), np.cumsum(z(a), axis=1)], axis=1)
    cs_alert, cs_disp = csum(R["n_fraud_alert"]), csum(R["n_dispute"])
    open_alert = np.zeros((n, D), bool)
    for t in range(D): open_alert[:, t] = (cs_alert[:, t + 1] - cs_alert[:, max(0, t - 29)] + cs_disp[:, t + 1] - cs_disp[:, max(0, t - 29)]) > 0
    # ---------------- features and model scores
    f = pd.read_parquet(f"{ROOT}/{data}/sim_days.parquet"); f = f[f.customer_id.isin(ids)].reset_index(drop=True)
    fci = pos.get_indexer(f.customer_id); fdi = (f.snap_date - WIN_START).dt.days.to_numpy()
    sc = Scorecard.from_json(f"{HERE}/artifacts/scorecard.json"); pts = sc.score(f)
    fc = QuantileForecast.load(f"{HERE}/artifacts/forecast_q95.json"); fcst = fc.predict(add_calendar(f))
    FB = os.environ.get("FRAUD_BUNDLE") or (f"{ROOT}/bundle/fraud_xgb_mask_v1_1" if os.path.exists(f"{ROOT}/bundle/fraud_xgb_mask_v1_1/model.json") else f"{ROOT}/bundle/fraud_xgb_mask_v1")
    sch = json.load(open(f"{FB}/schema.json")); booster = xgb.Booster(); booster.load_model(f"{FB}/model.json")
    X = pd.DataFrame({ft["name"]: (pd.Categorical(f[ft["name"]].astype(object).where(f[ft["name"]].isin(ft["categories"])), categories=ft["categories"]) if ft["type"] == "categorical" else f[ft["name"]].astype("float32")) for ft in sch["features"]})
    p = booster.predict(xgb.DMatrix(X, enable_categorical=True)); k = sch["neg_keep"]; cal = p * k / (p * k + (1 - p))
    band = np.select([cal >= sch["thresholds"]["high_risk"], cal >= sch["thresholds"]["suspicious"]], [2, 1], 0)
    tenure_true = ((f.snap_date - pd.to_datetime(cust.set_index("customer_id").loc[f.customer_id, "open_date"]).values).dt.days / 30.4).to_numpy()
    nb = f.out_30.to_numpy()
    F = {}
    for name, v in dict(score=pts, forecast=fcst, regular_windows=f.regular_inflow_windows_90.to_numpy(), disputes_90=f.disputes_90.to_numpy(), kyc_age_months=f.kyc_age_months.to_numpy(),
                        tenure_months=tenure_true, band=band, cal=cal, new_device_7=f.new_device_7.to_numpy(), cred_change_3=f.cred_change_3.to_numpy(), ftp_large_1=f.ftp_large_1.to_numpy(),
                        out_to_balance_1=f.out_to_balance_1.to_numpy(), balance=f.balance.to_numpy(), out_1=f.out_1.to_numpy(), night_share_1=f.night_share_1.to_numpy(),
                        active=(f.n_out_7.to_numpy() > 0) | (nb > 0)).items():
        m = np.full((n, D), np.nan, np.float32); m[fci, fdi] = np.asarray(v, np.float32); F[name] = m
    has = ~np.isnan(F["score"])
    return dict(cust=cust, ids=ids, n=n, R=R, cap_static=cap_static, baseline=baseline, open_alert=open_alert, F=F, has=has)


def run(prep=None, n_customers=None, seed=1, accept_p=0.7, hold_effectiveness=0.5, policy=None, data="data/sim", verbose=True):
    rng = np.random.default_rng(seed)
    prep = prep or prepare(n_customers, seed, data)
    cust, ids, n, R, cap_static, baseline, open_alert, F, has = (prep[k] for k in ("cust", "ids", "n", "R", "cap_static", "baseline", "open_alert", "F", "has"))
    z = np.nan_to_num
    # ---------------- policy loop
    pol = dict(policy or {})
    if "score_cut" not in pol: pol["score_cut"] = tuple(json.load(open(f"{HERE}/artifacts/tier_cuts.json"))["score_cut"])
    eng = PolicyEngine(baseline, pol)
    cap_dyn = np.tile(CAP[baseline][:, None].astype(np.float32), (1, D)); tier_hist = np.tile(baseline[:, None], (1, D)).astype(np.int8)
    will_accept = rng.random(n) < accept_p; accept_at = np.full(n, -1); counts = {a: 0 for a in ACTIONS}; ever_review = np.zeros(n, bool); ever_hold = np.zeros(n, bool); first_up = np.full(n, -1)
    for t in range(T0, D):
        y = t - 1                                                                   # batch uses yesterday's features
        g = lambda name: np.nan_to_num(F[name][:, y], nan=0.0)
        cap_now = eng.cap()
        night_amt = g("night_share_1") * g("out_1")
        bal_before = g("balance") + g("out_1")
        h_rule = (night_amt > 0.3 * cap_now) | ((bal_before > 50e6) & (g("out_1") > 0.5 * bal_before))
        block = (g("band") > 0) | (g("new_device_7") > 0) | (g("cred_change_3") > 0) | (g("ftp_large_1") > 0) | h_rule | open_alert[:, y]
        hold = (g("band") == 2)                                                  # only the model triggers a hold; 6.3 rules (night, balance) only block increases / ask step-up
        inp = dict(score=g("score"), tenure_months=g("tenure_months") + 1 / 30.4, regular_windows=g("regular_windows"), disputes_90=g("disputes_90"), kyc_age_months=g("kyc_age_months"),
                   forecast=g("forecast"), feature_ok=has[:, y], active_recent=g("active") > 0, gate_block=block, gate_hold=hold)
        accept_today = will_accept & (accept_at == t)
        act, tgt, prev = eng.step(t, inp, accept_today)
        new_offer = act == A["OFFER_INCREASE"]
        accept_at = np.where(new_offer, t + rng.integers(0, 4, n), np.where(np.isin(act, [A["APPLY_INCREASE"], A["CANCEL_OFFER"], A["EXPIRE_OFFER"], A["GATE_STEP_DOWN"], A["STEP_DOWN"]]), -1, accept_at))
        cap_dyn[:, t] = eng.cap(); tier_hist[:, t] = eng.tier
        for a, c in zip(*np.unique(act, return_counts=True)): counts[ACTIONS[a]] += int(c)
        ever_review |= act == A['REVIEW_QUEUE']; ever_hold |= act == A['HOLD']; first_up = np.where((first_up < 0) & (act == A['APPLY_INCREASE']), t - T0, first_up)
    # ---------------- outcomes
    sl = slice(T0, D)
    tot, mx = z(R["intended_amount"])[:, sl], z(R["max_attempt_amount"])[:, sl]
    n_int = z(R["n_outflow"])[:, sl] + z(R["n_declined_limit"])[:, sl]
    fraud_day = z(R["_fraud_day"])[:, sl] > 0
    # true fraud days include unreported ones for loss accounting? keep confirmed label (reported) for comparability
    real_s, dec_s = apply_cap(tot, mx, n_int, cap_static[:, None]); real_d, dec_d = apply_cap(tot, mx, n_int, cap_dyn[:, sl])
    band2 = np.nan_to_num(F["band"][:, sl]) == 2
    night_amt = np.nan_to_num(F["night_share_1"][:, sl]) * np.nan_to_num(F["out_1"][:, sl]); bal_b = np.nan_to_num(F["balance"][:, sl]) + np.nan_to_num(F["out_1"][:, sl])
    bal_rule = (bal_b > 50e6) & (np.nan_to_num(F["out_1"][:, sl]) > 0.5 * bal_b)
    night_s, night_d = night_amt > 0.3 * cap_static[:, None], night_amt > 0.3 * cap_dyn[:, sl]
    held_s, held = band2, band2                                                     # holds come from the model only
    band1 = np.nan_to_num(F["band"][:, sl]) == 1
    step_s, step_d = night_s | bal_rule | band1, night_d | bal_rule | band1         # step-up prompts: 6.3 rules + 'suspicious' band
    out = {}
    legit = ~fraud_day
    def pack(real, dec, hold_on):
        fraud_loss = (real * fraud_day * (1 - hold_effectiveness * hold_on)).sum()
        return dict(declined_events=int((dec * legit).sum()), declined_amount=float(((tot - real).clip(min=0) * legit).sum()), customers_with_decline=int(((dec * legit).sum(axis=1) > 0).sum()),
                    legit_volume=float((real * legit).sum()), fraud_outflow=float(fraud_loss), fraud_days=int(fraud_day.sum()),
                    false_holds=int((hold_on & legit).sum()), fraud_days_held=int((hold_on & fraud_day).sum()), customer_years=float(n * (D - T0) / 365))
    out["S0_static"] = pack(real_s, dec_s, np.zeros_like(held)); out["S1_static_gate"] = pack(real_s, dec_s, held_s); out["S2_dynamic_gate"] = pack(real_d, dec_d, held)
    out["hold_sources_dynamic"] = {nm: dict(legit_days=int((m & legit).sum()), fraud_days=int((m & fraud_day).sum())) for nm, m in dict(model_high_risk=band2, rule_night_velocity=night_d, rule_outflow_balance=bal_rule, model_suspicious=band1).items()}
    out["stepup_prompts"] = dict(static_per_1000=float((step_s & legit).sum() / n * 1000), dynamic_per_1000=float((step_d & legit).sum() / n * 1000), fraud_days_covered=int((step_d & fraud_day).sum()))
    first_fraud = np.where(fraud_day.any(axis=1), fraud_day.argmax(axis=1), -1); has_f = first_fraud >= 0
    out["fraud_cap_on_first_day"] = dict(static=float(cap_static[has_f].mean()) if has_f.any() else None,
                                          dynamic=float(cap_dyn[:, sl][np.arange(n)[has_f], first_fraud[has_f]].mean()) if has_f.any() else None, n=int(has_f.sum()))
    end_tier = tier_hist[:, -1]
    mig = pd.crosstab(pd.Series(baseline, name="baseline"), pd.Series(end_tier, name="end")).reindex(index=range(1, 6), columns=range(1, 6), fill_value=0)
    out["actions"] = counts; out["end_tier_share"] = pd.Series(end_tier).value_counts(normalize=True).sort_index().round(4).to_dict()
    out["baseline_tier_share"] = pd.Series(baseline).value_counts(normalize=True).sort_index().round(4).to_dict()
    out["customers_ever_increased"] = int((tier_hist[:, sl].max(axis=1) > baseline).sum()); out["customers_ever_below_baseline"] = int((tier_hist[:, sl].min(axis=1) < baseline).sum())
    out["n_customers"] = int(n)
    out["tier_changes_per_customer_year"] = float((np.diff(tier_hist[:, T0 - 1:], axis=1) != 0).sum() / (n * (D - T0) / 365))
    out["customers_review_queue"] = int(ever_review.sum()); out["customers_ever_held"] = int(ever_hold.sum())
    out["median_days_to_first_increase"] = float(np.median(first_up[first_up >= 0])) if (first_up >= 0).any() else None
    per_cust = dict(declined_static=(dec_s * legit).sum(axis=1), declined_dynamic=(dec_d * legit).sum(axis=1), holds_legit=(held & legit).sum(axis=1), stepup_legit=(step_d & legit).sum(axis=1))
    return out, mig, dict(per_cust=per_cust, tier_hist=tier_hist, cap_dyn=cap_dyn, baseline=baseline, ids=ids, F=F, fraud_day_full=z(R["_fraud_day"]) > 0)
