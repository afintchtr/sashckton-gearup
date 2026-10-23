"""Score the 25 lab validation cases through the published MAS module (REST) and compare with the expected_* columns.
Token is read from the environment so it never ends up in the repo or in chat:
    export VIYA_TOKEN='<bearer token>'
    python sas_export/test_mas_rest.py [--dry-run] [--omit-nulls]
Pass criteria (same as LAB_RUNBOOK.md step 4): |prob diff| < 1e-5, band/quality/classification identical, empty-value cases still score."""
import argparse, json, os, sys, urllib.error, urllib.request
import pandas as pd

HOST = "https://hack-w6i3ob4oj4.engage.sas.com"
MODULE = "fraudxgbmaskv11"
CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fraudXgbMaskV11", "lab_validation_cases.csv")
OUT = ["EM_EVENTPROBABILITY", "EM_CLASSIFICATION", "band", "quality"]

ap = argparse.ArgumentParser()
ap.add_argument("--dry-run", action="store_true", help="print the first request body and exit (no network)")
ap.add_argument("--omit-nulls", action="store_true", help="leave unavailable variables out of the request instead of sending null")
args = ap.parse_args()

df = pd.read_csv(CASES)
inputs = [c for c in df.columns if c != "case_id" and not c.startswith("expected_")]
assert len(inputs) == 63, len(inputs)


def body(row):
    items = []
    for c in inputs:
        v = row[c]
        if pd.isna(v):
            if not args.omit_nulls:
                items.append({"name": c, "value": None})
            continue
        items.append({"name": c, "value": v if isinstance(v, str) else float(v)})
    return {"inputs": items}


if args.dry_run:
    print(json.dumps(body(df.iloc[0]), indent=1)[:1500], "...")
    sys.exit(0)

token = os.environ.get("VIYA_TOKEN")
if not token:
    sys.exit("Set VIYA_TOKEN first (bearer token from the lab admin / your SAS login).")
url = f"{HOST}/microanalyticScore/modules/{MODULE}/steps/score"
ok = 0
for _, r in df.iterrows():
    req = urllib.request.Request(url, data=json.dumps(body(r)).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        resp = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        print(f"{r.case_id}: HTTP {e.code} {e.read().decode()[:300]}"); continue
    got = {o["name"]: o["value"] for o in resp.get("outputs", [])}
    exp = {k: r[f"expected_{k}"] for k in OUT}
    diff = abs(float(got["EM_EVENTPROBABILITY"]) - float(exp["EM_EVENTPROBABILITY"]))
    same = all(str(got[k]) == str(exp[k]) for k in OUT[1:])
    good = diff < 1e-5 and same
    ok += good
    print(f"{r.case_id}: {'PASS' if good else 'FAIL'} | prob {float(got['EM_EVENTPROBABILITY']):.6g} (diff {diff:.1e}) | {got['band']}/{got['quality']}/{got['EM_CLASSIFICATION']}")
print(f"\n{ok} dari {len(df)} kasus cocok | {'PASS' if ok == len(df) else 'FAIL'}")
