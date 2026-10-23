"""
Standalone fraud scorer for the exported model bundle (no dependency on the training notebooks).

Requires only: xgboost, numpy, pandas  (see requirements.txt in the bundle).

    from score import FraudScorer
    sc = FraudScorer("path/to/bundle")
    out = sc.score_df(df)                       # DataFrame in, DataFrame out
    res = sc.score_record({"out_7": 1.2e6, ...})  # one request (dict), missing keys / None = unavailable feature

Output per row
    score_raw   probability from the model (trained on down-sampled negatives, so inflated)
    score       prior-corrected probability (use this one for thresholds / monitoring)
    band        genuine | suspicious | high_risk   (thresholds in schema.json)
    reasons     top-3 features pushing the score up (SHAP contributions), "feature=value (+contribution)"
    missing_share, missing_groups, quality   data-quality signal: 'low' when >40% of features or a whole
                feature group is unavailable -> downstream decision rules should fall back to deterministic rules
"""
import json
import os

import numpy as np
import pandas as pd
import xgboost as xgb


class FraudScorer:
    def __init__(self, bundle_dir):
        self.dir = bundle_dir
        self.schema = json.load(open(os.path.join(bundle_dir, "schema.json")))
        self.features = [f["name"] for f in self.schema["features"]]
        self.cats = {f["name"]: f["categories"] for f in self.schema["features"] if f["type"] == "categorical"}
        self.groups = self.schema["groups"]
        self.booster = xgb.Booster()
        self.booster.load_model(os.path.join(bundle_dir, "model.json"))
        self.k = self.schema["neg_keep"]
        self.t_sus = self.schema["thresholds"]["suspicious"]
        self.t_high = self.schema["thresholds"]["high_risk"]

    # ------------------------------------------------------------------ input handling
    def _frame(self, df):
        d = pd.DataFrame(index=df.index)
        for f in self.features:
            col = df[f] if f in df.columns else pd.Series(np.nan, index=df.index)
            if f in self.cats:                                   # unknown / missing category -> NaN (treated as unavailable)
                d[f] = pd.Categorical(col.where(col.isin(self.cats[f])), categories=self.cats[f])
            else:
                d[f] = pd.to_numeric(col, errors="coerce").astype("float32")
        return d

    # ------------------------------------------------------------------ scoring
    def score_df(self, df, reasons=True):
        d = self._frame(df)
        dm = xgb.DMatrix(d, enable_categorical=True, missing=np.nan)
        p = self.booster.predict(dm)
        cal = p * self.k / (p * self.k + (1 - p))
        out = pd.DataFrame({"score_raw": p, "score": cal}, index=df.index)
        out["band"] = np.select([cal >= self.t_high, cal >= self.t_sus], ["high_risk", "suspicious"], "genuine")
        miss = d.isna()
        out["missing_share"] = miss.mean(axis=1).round(4)
        names = np.array(list(self.groups))                                   # vectorised: one boolean column per feature group
        grp = np.stack([miss[cols].all(axis=1).to_numpy() for cols in self.groups.values()], axis=1)
        out["missing_groups"] = [list(names[row]) for row in grp]
        out["quality"] = np.where((out.missing_share > 0.4) | grp.any(axis=1), "low", "ok")
        if reasons:
            contrib = self.booster.predict(dm, pred_contribs=True)[:, :-1]
            vals = d.reset_index(drop=True)
            r = []
            for i in range(len(d)):
                idx = [j for j in np.argsort(-contrib[i])[:3] if contrib[i, j] > 0]
                r.append([f"{self.features[j]}={vals.iloc[i, j]} (+{contrib[i, j]:.2f})" for j in idx])
            out["reasons"] = r
        return out

    def score_record(self, record):
        row = self.score_df(pd.DataFrame([record]), reasons=True).iloc[0]
        return {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in row.to_dict().items()}
