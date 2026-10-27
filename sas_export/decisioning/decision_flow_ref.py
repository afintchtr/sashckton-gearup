"""Reference implementation of the SAS Intelligent Decisioning flow "Fraud verdict" built around the Viya-published model (fraudxgbmaskv11).

    Start -> [Model node: fraudXgbMaskV11] -> [Rule set: Fraud gate] -> [Rule set: Verdict] -> End

Single source of truth for the rule tables (RULES, VERDICT_RULES, CONTEXT_VARS).  It
  * writes the Intelligent Decisioning inputs (variables.csv, rules_*.csv),
  * builds the test table + expected results for the Scoring tab (id_test_input.csv, id_test_expected.csv),
  * proves the tables equal the backend gate (serving/gate.py) on thousands of random rows (--check-backend).
The model step uses the SAME score code that is published to MAS (sas_export/fraudXgbMaskV11/score_fraudXgbMaskV11.py).

Usage: python sas_export/decisioning/decision_flow_ref.py [--build] [--check-backend N]"""
import argparse, importlib.util, json, os, sys, tempfile
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE)); PKG = os.path.join(ROOT, "sas_export", "fraudXgbMaskV11")
FEATURES = [v["name"] for v in json.load(open(os.path.join(PKG, "inputVar.json")))]

# ---- variables that the decision needs besides the 63 model features (gate context, supplied by the caller)
CONTEXT_VARS = [
    ("daily_cap", "Decimal", "Daily transfer cap of the customer's current tier (Rp)"),
    ("ftp_large_today", "Integer", "Transfers today to a first-time payee with amount above 50% of daily_cap"),
    ("night_outflow_today", "Decimal", "Outflow today between 22:00 and 05:00 (Rp)"),
    ("outflow_today", "Decimal", "Total outflow today (Rp)"),
    ("alerts_30", "Integer", "Fraud alerts in the last 30 days"),
    ("disputes_30", "Integer", "Disputes in the last 30 days"),
]
# ---- Rule set 1 "Fraud gate": each rule sets flags; same thresholds as serving/gate.py (proposal 6.3).  expr = Intelligent Decisioning condition text.
RULES = [
    dict(id="G1", code="FRAUD_SCORE_H", expr='band = "high_risk"', hold=1, step_up=0, block=1, py=lambda d: d.band == "high_risk"),
    dict(id="G2", code="NEW_DEVICE", expr="new_device_7 > 0", hold=0, step_up=0, block=1, py=lambda d: d.new_device_7 > 0),
    dict(id="G3", code="CREDENTIAL_CHANGE", expr="cred_change_3 > 0", hold=0, step_up=0, block=1, py=lambda d: d.cred_change_3 > 0),
    dict(id="G4", code="FIRST_PAYEE_LARGE", expr="ftp_large_today > 0", hold=0, step_up=1, block=1, py=lambda d: d.ftp_large_today > 0),
    dict(id="G5", code="NIGHT_VELOCITY", expr="night_outflow_today > 0.3 * daily_cap", hold=0, step_up=1, block=1, py=lambda d: d.night_outflow_today > 0.3 * d.daily_cap),
    dict(id="G6", code="OUTFLOW_BALANCE", expr="(balance + outflow_today) > 50000000 AND outflow_today > 0.5 * (balance + outflow_today)", hold=0, step_up=1, block=1,
         py=lambda d: ((d.balance + d.outflow_today) > 50e6) & (d.outflow_today > 0.5 * (d.balance + d.outflow_today))),
    dict(id="G7", code="OPEN_ALERT_OR_DISPUTE", expr="alerts_30 > 0 OR disputes_30 > 0", hold=0, step_up=0, block=1, py=lambda d: (d.alerts_30 > 0) | (d.disputes_30 > 0)),
]
# ---- Rule set 2 "Verdict": ordered, first match wins
VERDICT_RULES = [
    dict(id="V1", expr='gate_hold = 1 OR band = "high_risk"', verdict="HOLD"),
    dict(id="V2", expr='gate_step_up = 1 OR band = "suspicious"', verdict="STEP_UP_AUTH"),
    dict(id="V3", expr="otherwise", verdict="ALLOW"),
]


def _mas_module():
    stub = tempfile.mkdtemp(); open(os.path.join(stub, "settings.py"), "w").write(f"pickle_path = {PKG!r}\n"); sys.path.insert(0, stub)
    spec = importlib.util.spec_from_file_location("mas_score", os.path.join(PKG, "score_fraudXgbMaskV11.py")); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def model_node(df, mod=None):
    """Model node: same MAS score code as published to Viya (batch path)."""
    mod = mod or _mas_module()
    out = mod.score(**{c: df[c].reset_index(drop=True) for c in FEATURES}); out.index = df.index
    return out


def run_flow(df):
    d = df.copy(); m = model_node(d)
    for c in m.columns: d[c] = m[c]
    d["gate_hold"], d["gate_step_up"], d["gate_block"] = 0, 0, 0
    for r in RULES:
        hit = r["py"](d).fillna(False).astype(int); d[f"hit_{r['code'].lower()}"] = hit
        d["gate_hold"] |= hit * r["hold"]; d["gate_step_up"] |= hit * r["step_up"]; d["gate_block"] |= hit * r["block"]
    d["verdict"] = np.select([(d.gate_hold == 1) | (d.band == "high_risk"), (d.gate_step_up == 1) | (d.band == "suspicious")], ["HOLD", "STEP_UP_AUTH"], "ALLOW")
    d["block_increase"] = ((d.gate_block == 1) | (d.band != "genuine")).astype(int)
    return d


def write_definitions():
    pd.DataFrame([(f, "input", "Decimal" if f not in ("occupation", "segment") else "String", "model feature (63), see docs/FEATURES_AND_EXPLAINABILITY.md") for f in FEATURES] +
                 [(n, "input", t, desc) for n, t, desc in CONTEXT_VARS] +
                 [("EM_EVENTPROBABILITY", "output of model node", "Decimal", "calibrated fraud probability"), ("EM_CLASSIFICATION", "output of model node", "String", '"1" when band is not genuine'),
                  ("band", "output of model node", "String", "genuine | suspicious | high_risk"), ("quality", "output of model node", "String", "ok | low (many features unavailable)")] +
                 [("gate_hold", "output of rule set 1", "Integer", "1 = hold"), ("gate_step_up", "output of rule set 1", "Integer", "1 = step-up authentication"), ("gate_block", "output of rule set 1", "Integer", "1 = block limit increase")] +
                 [(f"hit_{r['code'].lower()}", "output of rule set 1", "Integer", f"rule {r['id']} fired") for r in RULES] +
                 [("verdict", "output of rule set 2", "String", "ALLOW | STEP_UP_AUTH | HOLD"), ("block_increase", "output of flow", "Integer", "gate_block = 1 OR band <> genuine")],
                 columns=["name", "role", "type", "description"]).to_csv(os.path.join(HERE, "variables.csv"), index=False)
    pd.DataFrame([dict(rule=r["id"], name=r["code"], condition=r["expr"], then_hit=f"hit_{r['code'].lower()} = 1", gate_hold=r["hold"], gate_step_up=r["step_up"], gate_block=r["block"]) for r in RULES]).to_csv(os.path.join(HERE, "rules_fraud_gate.csv"), index=False)
    pd.DataFrame([dict(order=i + 1, rule=r["id"], condition=r["expr"], then_verdict=r["verdict"]) for i, r in enumerate(VERDICT_RULES)]).to_csv(os.path.join(HERE, "rules_verdict.csv"), index=False)


def build_tests():
    base = pd.read_csv(os.path.join(PKG, "lab_validation_cases.csv")); base = base[["case_id"] + FEATURES].set_index("case_id")
    CAP = 25e6; ctx0 = dict(daily_cap=CAP, ftp_large_today=0, night_outflow_today=0.0, outflow_today=0.0, alerts_30=0, disputes_30=0)
    rows = []

    def add(case_id, src, note, **over):
        r = base.loc[src].to_dict(); r.update(ctx0); r.update(over); r.update(case_id=case_id, scenario=note); rows.append(r)
    for cid in base.index: add(cid, cid, "validation case, no context signals")
    g = "case_12"                                                                                   # a genuine, quality ok case
    add("s01", g, "new device only", new_device_7=1)
    add("s02", g, "credential change only", cred_change_3=1)
    add("s03", g, "first-time large payee", ftp_large_today=1)
    add("s04", g, "night velocity 40% of cap", night_outflow_today=0.4 * CAP)
    add("s05", g, "outflow 90m vs balance 80m", balance=80e6, outflow_today=90e6)
    add("s06", g, "open fraud alert", alerts_30=1)
    add("s07", g, "two disputes", disputes_30=2)
    add("s08", g, "new device + credential change + first-time payee", new_device_7=1, cred_change_3=1, ftp_large_today=1)
    add("s09", "case_01", "suspicious case plus new device", new_device_7=1)
    add("s10", "case_06", "high_risk case plus alert", alerts_30=1)
    add("b01", g, "boundary: night = exactly 30% of cap (no hit)", night_outflow_today=0.3 * CAP)
    add("b02", g, "boundary: night just above 30% of cap (hit)", night_outflow_today=0.3 * CAP + 1)
    add("b03", g, "boundary: outflow exactly 50% of balance-before (no hit)", balance=50e6, outflow_today=50e6)
    add("b04", g, "boundary: outflow just above 50% (hit)", balance=50e6, outflow_today=50.01e6)
    add("b05", g, "boundary: balance-before exactly 50m (no hit)", balance=20e6, outflow_today=30e6)
    add("b06", g, "boundary: balance-before just above 50m (hit)", balance=20.01e6, outflow_today=30e6)
    low = base.loc[g].copy(); rng = np.random.default_rng(1); num = [c for c in FEATURES if c not in ("occupation", "segment")]
    for c in rng.choice(num, int(0.7 * len(num)), replace=False): low[c] = np.nan
    r = low.to_dict(); r.update(ctx0); r.update(case_id="q01", scenario="70% of numeric features unavailable (quality low)"); rows.append(r)
    r2 = dict(r); r2.update(case_id="q02", scenario="quality low plus new device and alert", new_device_7=1, alerts_30=1); rows.append(r2)
    t = pd.DataFrame(rows).set_index("case_id")
    inp = t[FEATURES + [n for n, _, _ in CONTEXT_VARS]]
    res = run_flow(inp)
    exp_cols = ["EM_EVENTPROBABILITY", "EM_CLASSIFICATION", "band", "quality"] + [f"hit_{r['code'].lower()}" for r in RULES] + ["gate_hold", "gate_step_up", "gate_block", "verdict", "block_increase"]
    inp.reset_index().to_csv(os.path.join(HERE, "id_test_input.csv"), index=False)
    out = res[exp_cols].copy(); out.insert(0, "scenario", t.scenario); out.reset_index().to_csv(os.path.join(HERE, "id_test_expected.csv"), index=False, float_format="%.8g")
    print(f"test table: {len(inp)} rows | verdicts: {res.verdict.value_counts().to_dict()} | quality: {res.quality.value_counts().to_dict()} | bands: {res.band.value_counts().to_dict()}")
    return res


def check_backend(n):
    """Random rows (real training-sample features, random context): rule tables vs serving/gate.py."""
    sys.path.insert(0, os.path.join(ROOT, "serving")); import gate as backend
    s = pd.read_csv(os.path.join(ROOT, "sas_export", "lab_evidence", "training_sample_20mb.csv")).sample(n, random_state=5).reset_index(drop=True)[FEATURES]
    rng = np.random.default_rng(2); cap = rng.choice([10e6, 25e6, 50e6, 100e6, 200e6], n)
    s["daily_cap"] = cap; s["ftp_large_today"] = (rng.random(n) < 0.1).astype(int); s["night_outflow_today"] = np.where(rng.random(n) < 0.3, cap * rng.uniform(0, 0.6, n), 0.0)
    s["balance"] = np.where(rng.random(n) < 0.5, s.balance, rng.uniform(0, 150e6, n)); s["outflow_today"] = np.where(rng.random(n) < 0.3, s.balance * rng.uniform(0, 1.6, n), 0.0)
    s["alerts_30"] = (rng.random(n) < 0.1).astype(int); s["disputes_30"] = (rng.random(n) < 0.05).astype(int)
    s["new_device_7"] = np.where(rng.random(n) < 0.15, 1, s.new_device_7.fillna(0)); s["cred_change_3"] = np.where(rng.random(n) < 0.1, 1, s.cred_change_3.fillna(0))
    ref = run_flow(s); bad = 0; codes = {r["code"]: r for r in RULES}
    for i in range(n):
        row = s.iloc[i]; band = ref.band.iloc[i]
        g = backend.evaluate(pd.DataFrame([row]), dict(ftp_large=row.ftp_large_today, night_outflow_amt=row.night_outflow_today, outflow_amt=row.outflow_today), dict(cap=row.daily_cap),
                             dict(alerts_30=row.alerts_30, disputes_30=row.disputes_30), band)
        verdict = "HOLD" if (g["hold"] or band == "high_risk") else ("STEP_UP_AUTH" if (g["step_up"] or band == "suspicious") else "ALLOW")
        mine = {c for c, r in codes.items() if ref[f"hit_{c.lower()}"].iloc[i] == 1}
        ok = mine == set(g["codes"]) and verdict == ref.verdict.iloc[i] and bool(g["block_increase"] or band != "genuine") == bool(ref.block_increase.iloc[i])
        bad += not ok
        if not ok and bad <= 3: print("MISMATCH", i, mine, g["codes"], verdict, ref.verdict.iloc[i])
    print(f"backend equivalence on {n} random rows: {n - bad} match, {bad} differ | verdicts {ref.verdict.value_counts().to_dict()} | hits per rule { {c: int(ref[f'hit_{c.lower()}'].sum()) for c in codes} }")
    assert bad == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--build", action="store_true"); ap.add_argument("--check-backend", type=int, default=0); a = ap.parse_args()
    if a.build or not a.check_backend: write_definitions(); build_tests()
    if a.check_backend: check_backend(a.check_backend)
