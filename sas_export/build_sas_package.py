"""
Build the SAS Viya (Model Manager / MAS) package for the fraud model and the scorecard.
Everything here is generated and tested OFFLINE; actually importing into a Viya lab needs credentials and has not been done (see sas_export/README.md).
Usage: python sas_export/build_sas_package.py [--version v1_1|v1]      (default v1_1: 63 features, no protected attributes)
"""
import argparse, json, os, pickle, shutil, sys, zipfile
import numpy as np, pandas as pd, joblib
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "limit"))
from sasctl import pzmm
from scorecard import Scorecard

ap = argparse.ArgumentParser(); ap.add_argument("--version", default="v1_1", choices=["v1", "v1_1"]); ARGS = ap.parse_args()
V1 = ARGS.version == "v1"
PFX = "fraudXgbMaskV1" if V1 else "fraudXgbMaskV11"; BUNDLE_DIR = "fraud_xgb_mask_v1" if V1 else "fraud_xgb_mask_v1_1"; OUT = os.path.join(HERE, PFX)
shutil.rmtree(OUT, ignore_errors=True); os.makedirs(OUT)
schema = json.load(open(f"{ROOT}/bundle/{BUNDLE_DIR}/schema.json")); feats = schema["features"]

class Model: pass                                                     # stub to open the joblib wrapper from notebook 02b
clf = joblib.load(f"{ROOT}/models/baseline_XGB_mask.joblib").m if V1 else joblib.load(f"{ROOT}/models/fraud_v1_1_xgb_mask.joblib")
pzmm.PickleModel.pickle_trained_model(model_prefix=PFX, trained_model=clf, pickle_path=OUT)

# ---- input / output variable definitions (68 inputs; outputs follow Model Manager's binary-classification convention)
sample = pd.read_parquet(f"{ROOT}/data/dev/daydetect.parquet").head(200)
X = pd.DataFrame({f["name"]: (sample[f["name"]].astype(str) if f["type"] == "categorical" else sample[f["name"]].astype(float)) for f in feats})
out_df = pd.DataFrame({"EM_EVENTPROBABILITY": [0.01], "EM_CLASSIFICATION": ["0"], "band": ["genuine"], "quality": ["ok"]})
pzmm.JSONFiles.write_var_json(X, is_input=True, json_path=OUT); pzmm.JSONFiles.write_var_json(out_df, is_input=False, json_path=OUT)
pzmm.JSONFiles.write_model_properties_json(model_name=PFX, target_variable="fraud_today", target_values=["1", "0"], json_path=OUT, model_desc="Fraud risk score (XGBoost, mask-augmented), synthetic-data hackathon model. Input features are the 68 rolling features; null = unavailable.",
                                          model_algorithm="XGBoost", model_function="Classification", modeler="Team Gear Up", train_table="synthetic_customer_daily")
pzmm.JSONFiles.write_file_metadata_json(PFX, json_path=OUT)

# ---- custom MAS score code (pzmm's generated code would pass categorical features as plain strings, which an enable_categorical XGBoost model rejects)
args = ", ".join(f["name"] for f in feats)
cats = {f["name"]: f["categories"] for f in feats if f["type"] == "categorical"}
code = f'''import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

import settings

with open(Path(settings.pickle_path) / "{PFX}.pickle", "rb") as _f:
    _model = pickle.load(_f)

_FEATURES = {[f["name"] for f in feats]!r}
_CATEGORIES = {cats!r}
_NEG_KEEP = {schema["neg_keep"]}
_T_SUSPICIOUS = {schema["thresholds"]["suspicious"]!r}
_T_HIGH_RISK = {schema["thresholds"]["high_risk"]!r}
_GROUPS = {schema["groups"]!r}


def score({args}):
    "Output: EM_EVENTPROBABILITY, EM_CLASSIFICATION, band, quality"
    row = {{{", ".join(f"{f['name']!r}: {f['name']}" for f in feats)}}}
    d = {{}}
    for name in _FEATURES:
        v = row[name]
        if name in _CATEGORIES:
            d[name] = pd.Categorical([v if v in _CATEGORIES[name] else None], categories=_CATEGORIES[name])
        else:
            d[name] = [np.nan if v is None else float(v)]
    X = pd.DataFrame(d)
    p = float(_model.predict_proba(X)[0, 1])
    cal = p * _NEG_KEEP / (p * _NEG_KEEP + (1 - p))
    band = "high_risk" if cal >= _T_HIGH_RISK else ("suspicious" if cal >= _T_SUSPICIOUS else "genuine")
    miss = X.isna().iloc[0]
    low = bool(miss.mean() > 0.4 or any(miss[c].all() for c in _GROUPS.values()))
    return cal, ("1" if band != "genuine" else "0"), band, ("low" if low else "ok")
'''
open(f"{OUT}/score_{PFX}.py", "w").write(code)
try:
    reqs = pzmm.JSONFiles.create_requirements_json(model_path=OUT, output_path=OUT)
    assert os.path.exists(f"{OUT}/requirements.json"), 'requirements.json missing'
except Exception as e:
    json.dump([{"step": "import xgboost", "command": "pip install xgboost==3.2.0"}, {"step": "import pandas", "command": "pip install pandas==2.3.3"}, {"step": "import numpy", "command": "pip install numpy==2.2.6"}], open(f"{OUT}/requirements.json", "w"), indent=1)
    print("requirements.json written manually:", repr(e)[:80])

# ---- scorecard as a SAS DATA step (bins and points are plain numbers, so it can be audited and run anywhere)
SC = os.path.join(HERE, "scorecard"); shutil.rmtree(SC, ignore_errors=True); os.makedirs(SC)
card = Scorecard.from_json(f"{ROOT}/limit/artifacts/scorecard.json"); L = ["/* Behavioural risk scorecard (proposal 6.1). Points scale: base %d at odds %d:1, PDO %d. Tier cuts in tier_cuts.json */" % (card.base_points, card.base_odds, card.pdo),
                                                                      "data work.scored; set work.input;", "  risk_points = 0;"]
for f, s in card.spec.items():
    e = s["edges"]; L.append(f"  /* {f} */\n  if missing({f}) then pts_{f} = {s['points_missing']};")
    for i, p in enumerate(s["points"]):
        cond = f"{f} < {e[i + 1]}" if i < len(s["points"]) - 1 else ""
        L.append(f"  else {'if ' + cond + ' then ' if cond else ''}pts_{f} = {p};")
    L.append(f"  risk_points + pts_{f};")
L.append("run;"); open(f"{SC}/scorecard_score.sas", "w").write("\n".join(L) + "\n")
shutil.copy(f"{ROOT}/limit/artifacts/scorecard.json", f"{SC}/scorecard.json"); shutil.copy(f"{ROOT}/limit/artifacts/tier_cuts.json", f"{SC}/tier_cuts.json")

# ---- validation cases for the lab: inputs with the scores this package must reproduce inside Viya (includes unavailable values)
sys.path.insert(0, f"{ROOT}/bundle/{BUNDLE_DIR}")
from score import FraudScorer
sc_ = FraudScorer(f"{ROOT}/bundle/{BUNDLE_DIR}")
day = pd.read_parquet(f"{ROOT}/data/dev/daydetect.parquet"); day = day[day.snap_date >= "2026-07-01"]
pick = pd.concat([day[day.label_fraud_today == 1].sample(10, random_state=3), day[day.label_fraud_today == 0].sample(10, random_state=3)]).reset_index(drop=True)
cases = pick[[f["name"] for f in feats]].copy()
for f in feats:
    if f["type"] == "categorical": cases[f["name"]] = cases[f["name"]].astype(object)
r_ = np.random.default_rng(5)
for i in range(5):                                                              # five extra rows with ~25% of the values unavailable
    row = cases.iloc[[i]].copy()
    for f in feats:
        if r_.random() < 0.25:
            if f["type"] == "categorical": row[f["name"]] = pd.Series([None], index=row.index, dtype=object)
            else: row[f["name"]] = np.nan
    cases = pd.concat([cases, row], ignore_index=True)
exp = sc_.score_df(cases.astype(object).where(cases.notna(), np.nan), reasons=False)
val = cases.copy(); val.insert(0, "case_id", [f"case_{i + 1:02d}" for i in range(len(val))])
val["expected_EM_EVENTPROBABILITY"] = exp.score.values.round(8); val["expected_band"] = exp.band.values; val["expected_quality"] = exp.quality.values; val["expected_EM_CLASSIFICATION"] = np.where(exp.band.values != "genuine", "1", "0")
val.to_csv(f"{OUT}/lab_validation_cases.csv", index=False)

# ---- zip for Model Manager (model files only)
zpath = os.path.join(HERE, f"{PFX}.zip")
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for fn in sorted(os.listdir(OUT)):
        if fn != "lab_validation_cases.csv": z.write(os.path.join(OUT, fn), fn)
print("package:", sorted(os.listdir(OUT)), "| zip", os.path.getsize(zpath) // 1024, "KB")
