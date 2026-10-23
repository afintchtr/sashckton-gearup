"""
Self-contained check you can run INSIDE the Viya Python environment (e.g. SAS Viya Workbench or the MAS Python used by Model Manager) before registering the model.
It imports the MAS score code with a stub `settings` module, scores lab_validation_cases.csv and compares with the expected values.

    python verify_validation_cases.py <package_dir>        # package_dir = folder with the .pickle, score_*.py and lab_validation_cases.csv

Needs only: numpy, pandas, xgboost (versions in requirements.json).  Exit code 0 = PASS.
"""
import glob, importlib.util, json, os, sys, tempfile
import numpy as np, pandas as pd

pkg = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
req = json.load(open(os.path.join(pkg, "requirements.json"))) if os.path.exists(os.path.join(pkg, "requirements.json")) else []
import xgboost, pandas, numpy
have = {"xgboost": xgboost.__version__, "numpy": numpy.__version__, "pandas": pandas.__version__}
ok_ver = True
for r in req:
    name, _, want = r["command"].replace("pip install ", "").partition("==")
    flag = "OK" if have.get(name) == want else "BERBEDA"
    ok_ver &= flag == "OK"
    print(f"  paket {name:8s} dibutuhkan {want:8s} terpasang {have.get(name, '?'):8s} {flag}")
if not ok_ver: print("  PERINGATAN: versi berbeda; pickle XGBoost bisa gagal dimuat atau berperilaku lain. Pasang versi persis atau latih ulang dengan versi lab.")

stub = tempfile.mkdtemp(); open(os.path.join(stub, "settings.py"), "w").write(f"pickle_path = {pkg!r}\n"); sys.path.insert(0, stub)
code = glob.glob(os.path.join(pkg, "score_*.py"))[0]
spec = importlib.util.spec_from_file_location("mas_score", code); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
cases = pd.read_csv(os.path.join(pkg, "lab_validation_cases.csv"))
inputs = [c for c in cases.columns if not c.startswith("expected_") and c != "case_id"]
bad = 0
for _, r in cases.iterrows():
    row = {c: (None if pd.isna(r[c]) else r[c]) for c in inputs}
    p, cls, band, quality = mod.score(**row)
    ok = abs(p - r["expected_EM_EVENTPROBABILITY"]) < 1e-5 + 1e-4 * r["expected_EM_EVENTPROBABILITY"] and band == r["expected_band"] and quality == r["expected_quality"] and cls == str(int(r["expected_EM_CLASSIFICATION"]))
    bad += not ok
    if not ok: print(f"  GAGAL {r['case_id']}: skor {p:.8f} vs {r['expected_EM_EVENTPROBABILITY']:.8f}, pita {band} vs {r['expected_band']}, kualitas {quality} vs {r['expected_quality']}")
print(f"{len(cases) - bad} dari {len(cases)} kasus cocok" + (" | PASS" if bad == 0 else " | FAIL"))
sys.exit(0 if bad == 0 else 1)
