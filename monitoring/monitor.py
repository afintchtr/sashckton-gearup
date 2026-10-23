"""
Model and policy monitoring (proposal 6.4): PSI on inputs and score, KS / PR-AUC on matured outcomes, tier migration, fairness by group, retrain triggers.

PSI thresholds (industry convention): < 0.10 stable, 0.10-0.25 watch, > 0.25 drift.  Proposal triggers: retrain when score PSI > 0.25 or KS drops > 5 points.
A Reference stores the TRAINING distribution (bin edges + expected shares) as plain JSON, so production monitoring does not need the training data.
"""
import json

import numpy as np
import pandas as pd

PSI_WATCH, PSI_DRIFT, KS_DROP_POINTS = 0.10, 0.25, 5.0
MISSING_RISE, PRAUC_REL_DROP, DRIFT_FEATURES_REVIEW = 0.05, 0.20, 5
TIME_DRIFT = ("kyc_age_months",)       # grows mechanically with calendar time, so its PSI is not evidence of model drift


def _edges(x, w, n):
    x = np.asarray(x, float); ok = ~np.isnan(x)
    if ok.sum() == 0: return np.array([-np.inf, np.inf])
    xs, ws = x[ok], (np.ones(ok.sum()) if w is None else np.asarray(w)[ok])
    o = np.argsort(xs); cw = np.cumsum(ws[o]) / ws.sum()
    q = np.unique(xs[o][np.searchsorted(cw, np.linspace(0, 1, n + 1)[1:-1])])
    return np.concatenate([[-np.inf], q, [np.inf]])


def _shares(x, edges, w):
    x = np.asarray(x, float); w = np.ones(len(x)) if w is None else np.asarray(w, float); tot = w.sum()
    miss = np.isnan(x)
    b = np.clip(np.searchsorted(edges, x[~miss], side="right") - 1, 0, len(edges) - 2)
    return np.append(np.bincount(b, weights=w[~miss], minlength=len(edges) - 1) / tot, w[miss].sum() / tot)


def psi_value(expected, actual, eps=1e-4):
    e, a = np.clip(np.asarray(expected), eps, None), np.clip(np.asarray(actual), eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def status(v): return "drift" if v > PSI_DRIFT else ("pantau" if v > PSI_WATCH else "stabil")


class Reference:
    def __init__(self, bins=10): self.bins, self.num, self.cat = bins, {}, {}

    def fit(self, df, numeric, categorical=(), score=None, weight=None):
        w = None if weight is None else df[weight].to_numpy()
        for f in numeric:
            e = _edges(df[f], w, self.bins); self.num[f] = dict(edges=e.tolist(), share=_shares(df[f], e, w).tolist())
        for f in categorical:
            s = df[f].astype(object).where(df[f].notna(), "__missing__"); ww = np.ones(len(s)) if w is None else w
            self.cat[f] = (pd.Series(ww, index=s.values).groupby(level=0).sum() / ww.sum()).to_dict()
        if score is not None:
            s = np.asarray(score); e = _edges(s, w, self.bins); self.num["__score__"] = dict(edges=e.tolist(), share=_shares(s, e, w).tolist())
        return self

    def psi_table(self, df, score=None, weight=None, ignore=TIME_DRIFT):
        w = None if weight is None else df[weight].to_numpy(); rows = []
        for f, d in self.num.items():
            if f == "__score__": continue
            a = _shares(df[f], np.array(d["edges"]), w); rows.append(dict(feature=f, psi=psi_value(d["share"], a), missing_ref=d["share"][-1], missing_now=a[-1]))
        for f, d in self.cat.items():
            s = df[f].astype(object).where(df[f].notna(), "__missing__"); ww = np.ones(len(s)) if w is None else w
            now = (pd.Series(ww, index=s.values).groupby(level=0).sum() / ww.sum()).to_dict(); keys = sorted(set(d) | set(now))
            rows.append(dict(feature=f, psi=psi_value([d.get(k, 0) for k in keys], [now.get(k, 0) for k in keys]), missing_ref=d.get("__missing__", 0.0), missing_now=now.get("__missing__", 0.0)))
        t = pd.DataFrame(rows); t["status"] = t.psi.map(status); t["naik_kosong"] = t.missing_now - t.missing_ref; t["alarm_kosong"] = t.naik_kosong > MISSING_RISE
        t.loc[t.feature.isin(ignore), "status"] = "waktu"
        out = t.sort_values("psi", ascending=False).reset_index(drop=True)
        score_psi = None
        if score is not None and "__score__" in self.num:
            d = self.num["__score__"]; score_psi = psi_value(d["share"], _shares(score, np.array(d["edges"]), w))
        return out, score_psi

    def to_json(self, path): json.dump(dict(bins=self.bins, num=self.num, cat=self.cat), open(path, "w"))

    @classmethod
    def from_json(cls, path):
        d = json.load(open(path)); r = cls(d["bins"]); r.num, r.cat = d["num"], d["cat"]; return r


def ks_points(y, s, w=None):
    y = np.asarray(y, float); w = np.ones(len(y)) if w is None else np.asarray(w, float); o = np.argsort(-np.asarray(s))
    yw, nw = (y * w)[o], ((1 - y) * w)[o]
    return float(100 * np.max(np.abs(np.cumsum(yw) / yw.sum() - np.cumsum(nw) / nw.sum())))


def decide(table, score_psi, ks_drop, prauc_ref=None, prauc_now=None):
    """One verdict from every monitor. Beyond the proposal's two triggers (score PSI > 0.25, KS drop > 5 points) it adds the ones the tests showed are needed:
    PR-AUC relative drop (KS barely moves on a rare-event model), a missing-rate alarm (a robust model hides input outages from the score), and many drifting features."""
    drift = int((table.status == "drift").sum()); miss = int(table.alarm_kosong.sum())
    pr_drop = None if not (prauc_ref and prauc_now) else (prauc_ref - prauc_now) / prauc_ref
    t = dict(score_psi_trigger=bool(score_psi is not None and score_psi > PSI_DRIFT), ks_trigger=bool(ks_drop > KS_DROP_POINTS), prauc_trigger=bool(pr_drop is not None and pr_drop > PRAUC_REL_DROP))
    action = "RETRAIN / ESCALATE" if any(t.values()) else ("INVESTIGATE DATA PIPELINE" if miss else ("REVIEW" if drift >= DRIFT_FEATURES_REVIEW else "OK"))
    return dict(action=action, features_drift=drift, missing_alarms=miss, prauc_rel_drop=pr_drop, **t)


def retrain_triggers(score_psi, ks_now, ks_ref):
    return dict(score_psi=score_psi, score_psi_trigger=bool(score_psi is not None and score_psi > PSI_DRIFT), ks_drop=ks_ref - ks_now, ks_trigger=bool(ks_ref - ks_now > KS_DROP_POINTS))


def migration_matrix(tier_start, tier_end):
    return pd.crosstab(pd.Series(tier_start, name="awal"), pd.Series(tier_end, name="akhir")).reindex(index=range(1, 6), columns=range(1, 6), fill_value=0)


def group_gap(rates, min_ratio=0.8, max_ratio=1.25):
    """rates: Series of a rate per group. Returns min/max ratio and whether it breaches the monitoring band."""
    r = rates.dropna(); ratio = float(r.min() / r.max()) if len(r) and r.max() > 0 else np.nan
    return dict(min_over_max=ratio, breach=bool(ratio < min_ratio) if not np.isnan(ratio) else False)
