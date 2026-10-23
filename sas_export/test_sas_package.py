"""Offline checks of the SAS package: (1) the MAS score code (run with a stub `settings`) reproduces the bundle's scores, incl. null inputs;
(2) the generated SAS DATA step for the scorecard reproduces Python points.  NOT a substitute for importing into a real Viya lab."""
import importlib.util, json, os, re, sys, tempfile
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
V1 = "--v1" in sys.argv; PFX = "fraudXgbMaskV1" if V1 else "fraudXgbMaskV11"; BD = "fraud_xgb_mask_v1" if V1 else "fraud_xgb_mask_v1_1"; PKG = os.path.join(HERE, PFX)
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "limit")); sys.path.insert(0, os.path.join(ROOT, "bundle", BD))
import splits as S
from score import FraudScorer
from scorecard import Scorecard
# ---- (1) MAS score code
stub = tempfile.mkdtemp(); open(os.path.join(stub, "settings.py"), "w").write(f"pickle_path = {PKG!r}\n"); sys.path.insert(0, stub)
spec = importlib.util.spec_from_file_location("mas_score", os.path.join(PKG, f"score_{PFX}.py")); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
sc = FraudScorer(os.path.join(ROOT, "bundle", BD))
dev = S.load_dev(); oot = S.dev_oot(dev).sample(600, random_state=3)[sc.features].reset_index(drop=True)
rng = np.random.default_rng(0); X = oot.copy()
for c in sc.features: X[c] = X[c].astype(object).where(rng.random(len(X)) > 0.15, None)         # 15% of values unavailable (None), as MAS would pass them
ref = sc.score_df(X.astype(object).where(X.notna(), np.nan), reasons=False)
got = [mod.score(**{k: (None if pd.isna(v) else v) for k, v in row.items()}) for row in X.to_dict("records")]
cal = np.array([g[0] for g in got]); d = np.abs(cal - ref.score.values)
print(f"MAS score code vs bundle: max |diff| {d.max():.2e} | band agreement {(np.array([g[2] for g in got]) == ref.band.values).mean():.4f} | quality agreement {(np.array([g[3] for g in got]) == ref.quality.values).mean():.4f}")
assert d.max() < 1e-5 and (np.array([g[2] for g in got]) == ref.band.values).all()
# batch path: Model Manager passes whole columns as pandas Series
bout = mod.score(**{c: X[c].where(X[c].notna(), None) for c in sc.features})
bd = np.abs(bout.EM_EVENTPROBABILITY.values - cal).max()
print(f"batch (Series) path vs row path: {len(bout)} rows | max |diff| {bd:.2e} | band same {(bout.band.values == np.array([g[2] for g in got])).all()} | quality same {(bout.quality.values == np.array([g[3] for g in got])).all()}")
assert len(bout) == len(X) and bd < 1e-9 and (bout.band.values == np.array([g[2] for g in got])).all() and (bout.quality.values == np.array([g[3] for g in got])).all()
# ---- (2) generated SAS DATA step == Python scorecard
card = Scorecard.from_json(os.path.join(ROOT, "limit", "artifacts", "scorecard.json")); text = open(os.path.join(HERE, "scorecard", "scorecard_score.sas")).read()
rules = {}
for f in card.features:
    block = text.split(f"/* {f} */")[1].split("risk_points +")[0]
    miss = float(re.search(r"then pts_%s = ([-\d.]+);" % f, block.split("\n")[1] if False else block).group(1))
    steps = [(float(m.group(1)), float(m.group(2))) for m in re.finditer(r"else if %s < ([-\d.eE+]+) then pts_%s = ([-\d.]+);" % (f, f), block)]
    last = float(re.findall(r"else pts_%s = ([-\d.]+);" % f, block)[-1]); rules[f] = (miss, steps, last)
snap = pd.read_parquet(os.path.join(ROOT, "data/dev", "snapshots.parquet")).sample(5000, random_state=1)
def sas_points(row):
    tot = 0.0
    for f, (miss, steps, last) in rules.items():
        v = row[f]
        if pd.isna(v): tot += miss; continue
        for edge, p in steps:
            if v < edge: tot += p; break
        else: tot += last
    return tot
sas = np.array([sas_points(r) for r in snap.to_dict("records")]); py = card.score(snap)
print(f"SAS DATA step logic vs Python scorecard: max |diff| {np.abs(sas - py).max():.3f} points over {len(snap):,} rows")
assert np.abs(sas - py).max() < 0.05
print("SAS PACKAGE CHECKS PASSED")
