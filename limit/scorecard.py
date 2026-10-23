"""
Behavioural risk scorecard (proposal 6.1): monotone WoE binning -> logistic regression -> points with reason codes.
No external binning library: bins start as quantile cuts and are merged with isotonic regression (pool-adjacent-violators) so that
the bad rate is monotone in every feature.  The fitted card is a plain JSON spec (bins + points), so it can be audited,
re-implemented in SAS Scorecard / DATA step, or served anywhere.

Points scale (credit-style): `base_points` at `base_odds` (good:bad) and `pdo` points to double the odds.
"""
import json

import numpy as np
import pandas as pd

REASON_TEXT = {
    "tenure_months": "short time with the bank", "kyc_age_months": "identity (KYC) not refreshed recently", "inflow_cv_90": "unstable monthly inflow",
    "regular_inflow_windows_90": "few months with regular inflow", "balance_to_income": "low balance relative to income", "days_low_balance_90": "many days with near-zero balance",
    "new_payee_rate_30": "high share of transfers to new payees", "night_share_30": "high share of night-time transfers", "payee_concentration_90": "unusual payee concentration",
    "distinct_per_tx_30": "many different payees per transaction", "device_cnt_90": "many devices used recently", "mix_gambling_crypto_30": "spending on gambling/crypto",
    "mix_ewallet_topup_30": "high e-wallet top-up share", "mix_shift_30_vs_prev60": "recent change in spending pattern", "geo_mismatch_share_30": "transactions from unusual locations",
    "fraud_alerts_90": "recent fraud alerts", "disputes_90": "recent disputes", "ftp_large_30": "large first-time payee transfers", "age_ord": "age band", "declared_income": "declared income",
    "shared_device_flag": "shared device", "passthrough_7": "pass-through of incoming funds", "out_to_balance_max_3d": "large outflow relative to balance",
}


def _ks(y, s):
    o = np.argsort(-s); ys = y[o]
    return float(np.max(np.abs(np.cumsum(ys) / max(ys.sum(), 1) - np.cumsum(1 - ys) / max((1 - ys).sum(), 1))))


class Scorecard:
    def __init__(self, features, n_quantiles=20, min_bin=0.03, iv_min=0.015, max_corr=0.75, max_features=12, pdo=40, base_points=600, base_odds=50, C=1.0):
        self.cand, self.nq, self.min_bin, self.iv_min, self.max_corr, self.max_features = list(features), n_quantiles, min_bin, iv_min, max_corr, max_features
        self.pdo, self.base_points, self.base_odds, self.C = pdo, base_points, base_odds, C

    # ------------------------------------------------------------------ binning
    def _bin_feature(self, x, y):
        from scipy.stats import spearmanr
        from sklearn.isotonic import IsotonicRegression
        x = pd.Series(x).astype("float64")
        miss = x.isna().to_numpy()
        xv, yv = x[~miss].to_numpy(), y[~miss]
        qs = np.unique(np.quantile(xv, np.linspace(0, 1, self.nq + 1)[1:-1])) if len(xv) else np.array([])
        edges = np.concatenate([[-np.inf], qs, [np.inf]])
        b = np.clip(np.searchsorted(edges, xv, side="right") - 1, 0, len(edges) - 2)
        n = np.bincount(b, minlength=len(edges) - 1).astype(float); bad = np.bincount(b, weights=yv, minlength=len(edges) - 1)
        keep = n > 0
        rate = np.where(n > 0, bad / np.maximum(n, 1), 0)
        direction = np.sign(spearmanr(np.arange(keep.sum()), rate[keep]).statistic) if keep.sum() > 2 else 0
        iso = IsotonicRegression(increasing=bool(direction >= 0), out_of_bounds="clip")
        fit = np.full(len(n), np.nan); fit[keep] = iso.fit_transform(np.arange(keep.sum()), rate[keep], sample_weight=n[keep])
        # merge neighbouring bins with the same fitted rate; then merge bins that are too small
        groups, cur = [], [0]
        idx = np.where(keep)[0]
        for k in range(1, len(idx)):
            if np.isclose(fit[idx[k]], fit[idx[k - 1]]): cur.append(k)
            else: groups.append(cur); cur = [k]
        groups.append(cur)
        grp_edges = [edges[idx[g[0]]] for g in groups] + [np.inf]; grp_edges[0] = -np.inf
        gn = np.array([n[idx[g]].sum() for g in groups]); gb = np.array([bad[idx[g]].sum() for g in groups])
        while len(gn) > 1 and gn.min() < self.min_bin * len(xv):
            j = int(np.argmin(gn)); k = j - 1 if j > 0 else j + 1
            lo, hi = min(j, k), max(j, k)
            gn[lo] += gn[hi]; gb[lo] += gb[hi]; gn = np.delete(gn, hi); gb = np.delete(gb, hi); grp_edges.pop(hi)   # drop the shared edge
        return np.array(grp_edges), gn, gb, int(miss.sum()), float(y[miss].sum())

    def _woe(self, gn, gb, nm, bm, tot_good, tot_bad):
        good = gn - gb
        w = np.log(((good + 0.5) / tot_good) / ((gb + 0.5) / tot_bad))
        wm = np.log(((nm - bm + 0.5) / tot_good) / ((bm + 0.5) / tot_bad)) if nm > 0 else 0.0
        iv = float(np.sum(((good + 0.5) / tot_good - (gb + 0.5) / tot_bad) * w))
        return w, wm, iv

    def _transform(self, X, spec):
        x = pd.Series(X[spec["name"]]).astype("float64").to_numpy(); e = np.array(spec["edges"])
        b = np.clip(np.searchsorted(e, x, side="right") - 1, 0, len(e) - 2)
        w = np.array(spec["woe"])[b]
        return np.where(np.isnan(x), spec["woe_missing"], w), np.where(np.isnan(x), -1, b)

    # ------------------------------------------------------------------ fit
    def fit(self, X, y):
        from sklearn.linear_model import LogisticRegression
        y = np.asarray(y, float); tot_bad, tot_good = y.sum(), len(y) - y.sum()
        specs = {}
        for f in self.cand:
            e, gn, gb, nm, bm = self._bin_feature(X[f], y)
            w, wm, iv = self._woe(gn, gb, nm, bm, tot_good, tot_bad)
            specs[f] = dict(name=f, edges=e.tolist(), woe=w.tolist(), woe_missing=float(wm), iv=iv, n=gn.tolist(), bad=gb.tolist(), n_missing=nm)
        self.iv = pd.Series({f: s["iv"] for f, s in specs.items()}).sort_values(ascending=False)
        # selection: IV threshold, then greedy low-correlation filter on WoE-transformed columns
        Z = pd.DataFrame({f: self._transform(X, specs[f])[0] for f in self.iv.index[self.iv >= self.iv_min]})
        chosen = []
        for f in Z.columns:
            if all(abs(np.corrcoef(Z[f], Z[c])[0, 1]) < self.max_corr for c in chosen): chosen.append(f)
            if len(chosen) >= self.max_features: break
        lr = LogisticRegression(C=self.C, max_iter=2000).fit(Z[chosen], 1 - y)          # models P(good): WoE is ln(good/bad) so coefficients are positive
        coef = dict(zip(chosen, lr.coef_[0])); chosen = [f for f in chosen if coef[f] > 0] or chosen      # drop sign-flipped (collinear) features
        lr = LogisticRegression(C=self.C, max_iter=2000).fit(Z[chosen], 1 - y)
        self.coef, self.intercept, self.features = dict(zip(chosen, lr.coef_[0])), float(lr.intercept_[0]), chosen
        factor = self.pdo / np.log(2); offset = self.base_points - factor * np.log(self.base_odds)
        self.factor, self.offset = float(factor), float(offset)
        m = len(chosen)
        for f in chosen:
            s = specs[f]; base = (offset + factor * self.intercept) / m
            s["points"] = (base + factor * self.coef[f] * np.array(s["woe"])).round(2).tolist()
            s["points_missing"] = round(float(base + factor * self.coef[f] * s["woe_missing"]), 2)
        self.spec = {f: specs[f] for f in chosen}
        return self

    # ------------------------------------------------------------------ scoring
    def points(self, X):
        """Per-feature points matrix (n x m) and total score."""
        cols = {}
        for f, s in self.spec.items():
            _, b = self._transform(X, s)
            p = np.array(s["points"] + [s["points_missing"]])
            cols[f] = p[np.where(b < 0, len(s["points"]), b)]
        P = pd.DataFrame(cols, index=getattr(X, "index", None)); return P, P.sum(axis=1)

    def score(self, X): return self.points(X)[1].to_numpy()

    def reason_codes(self, X, top=3):
        """Features whose bin gives the fewest points relative to the best possible bin (largest shortfall first)."""
        P, _ = self.points(X)
        best = pd.Series({f: max(s["points"] + [s["points_missing"]]) for f, s in self.spec.items()})
        gap = (best - P).clip(lower=0)
        return [[f"{REASON_TEXT.get(f, f)} (-{gap.iloc[i][f]:.0f} pts)" for f in gap.iloc[i].nlargest(top).index if gap.iloc[i][f] > 0] for i in range(len(gap))]

    def prob_bad(self, points):
        return 1 / (1 + np.exp((np.asarray(points) - self.offset) / self.factor))

    def evaluate(self, X, y):
        s = self.score(X); y = np.asarray(y, float)
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score(y, -s); return dict(AUC=auc, Gini=2 * auc - 1, KS=_ks(y, -s))

    # ------------------------------------------------------------------ persistence
    def to_json(self, path):
        json.dump(dict(features=self.features, intercept=self.intercept, coef=self.coef, factor=self.factor, offset=self.offset, pdo=self.pdo,
                       base_points=self.base_points, base_odds=self.base_odds, bins=self.spec, reason_text={f: REASON_TEXT.get(f, f) for f in self.features}), open(path, "w"), indent=1)

    @classmethod
    def from_json(cls, path):
        d = json.load(open(path)); c = cls(d["features"]); c.spec, c.features, c.coef, c.intercept = d["bins"], d["features"], d["coef"], d["intercept"]
        c.factor, c.offset, c.pdo, c.base_points, c.base_odds = d["factor"], d["offset"], d["pdo"], d["base_points"], d["base_odds"]
        return c
