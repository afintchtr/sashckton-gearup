"""Local pre-flight before importing into Viya: package completeness, JSON validity, variable counts, and a clean-interpreter run of the validation cases.
Usage: python sas_export/preflight.py"""
import json, os, subprocess, sys, zipfile
HERE = os.path.dirname(os.path.abspath(__file__)); PFX = "fraudXgbMaskV11"; PKG = os.path.join(HERE, PFX)
need = [f"{PFX}.pickle", f"score_{PFX}.py", "inputVar.json", "outputVar.json", "ModelProperties.json", "fileMetadata.json", "requirements.json"]
z = zipfile.ZipFile(os.path.join(HERE, f"{PFX}.zip")).namelist(); res = []
res.append(("zip berisi semua berkas model", all(n in z for n in need), ", ".join(n for n in need if n not in z) or "lengkap"))
inv = json.load(open(f"{PKG}/inputVar.json")); outv = json.load(open(f"{PKG}/outputVar.json")); props = json.load(open(f"{PKG}/ModelProperties.json"))
res.append(("variabel input", len(inv) == 63, f"{len(inv)} variabel"))
res.append(("variabel output memuat EM_EVENTPROBABILITY", any(v["name"] == "EM_EVENTPROBABILITY" for v in outv), ", ".join(v["name"] for v in outv)))
res.append(("target dan nilai", props.get("targetVariable") == "fraud_today" and sorted(props.get("targetEvent", ["1"]) if isinstance(props.get("targetEvent"), list) else ["1"]) is not None, f"target {props.get('targetVariable')}, fungsi {props.get('function')}"))
names = [v["name"] for v in inv]; res.append(("tanpa atribut terproteksi", not ({"gender", "age_ord", "region", "tenure_months", "hist_days"} & set(names)), "gender/age_ord/region/tenure_months/hist_days tidak ada"))
r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "verify_validation_cases.py"), PKG], capture_output=True, text=True)
res.append(("kasus validasi di interpreter bersih", r.returncode == 0, r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]))
w = max(len(a) for a, _, _ in res)
for a, ok, d in res: print(f"{'LULUS' if ok else 'GAGAL'}  {a:<{w}}  {d}")
print("\nPREFLIGHT:", "SIAP DIIMPOR" if all(ok for _, ok, _ in res) else "ADA YANG GAGAL"); sys.exit(0 if all(ok for _, ok, _ in res) else 1)
