"""Turn existing result files into small tidy CSVs for SAS Visual Analytics (aggregates only, no customer rows; all source data is synthetic).
Usage: python sas_export/build_va_data.py     -> sas_export/va_data/*.csv"""
import json, os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); OUT = os.path.join(HERE, "va_data")
os.makedirs(OUT, exist_ok=True)
t2 = json.load(open(os.path.join(ROOT, "artifacts", "test2_results.json")))
mon = json.load(open(os.path.join(ROOT, "monitoring", "monitoring_report.json")))
lim = json.load(open(os.path.join(ROOT, "limit", "artifacts", "limit_models_metrics.json")))


def save(df, name):
    df.to_csv(os.path.join(OUT, name), index=False); print(f"{name}: {len(df)} rows x {len(df.columns)} cols")


# 1. model performance on the locked test set (model x metric)
perf = pd.DataFrame(t2["table"]).rename_axis("model").reset_index().melt(id_vars="model", var_name="metric", value_name="value")
save(perf, "model_performance.csv")

# 2. risk bands: share of traffic / precision / share of fraud caught
b = pd.DataFrame(t2["bands"]).rename_axis("band").reset_index()
b["band_order"] = b.band.map({"genuine": 1, "suspicious": 2, "high_risk": 3})
save(b, "band_summary.csv")

# 2b. long format of the band shares, for a 100% stacked bar (Traffic vs Fraud caught, split by band)
bl = b.melt(id_vars=["band", "band_order"], value_vars=["share_of_traffic", "share_of_fraud_caught"], var_name="measure_type", value_name="share")
bl["measure_type"] = bl.measure_type.map({"share_of_traffic": "Traffic", "share_of_fraud_caught": "Fraud caught"})
save(bl[["measure_type", "band", "band_order", "share"]], "band_share_long.csv")

# 3. robustness: PR-AUC under degraded inputs, v1.1 vs v1
rob = pd.DataFrame({"v1.1 (63 fitur)": t2["robustness"]["v1_1_PR"], "v1 (68 fitur)": t2["robustness"]["v1_PR"]}).rename_axis("scenario").reset_index()
rob = rob.melt(id_vars="scenario", var_name="model", value_name="pr_auc")
SCEN = {"clean": ("Clean", 1), "miss20": ("20% missing", 2), "tanpa_payee": ("No payee data", 3), "noise15": ("15% noise", 4), "miss20+noise15": ("20% missing + 15% noise", 5)}
rob["scenario_order"] = rob.scenario.map(lambda s: SCEN[s][1]); rob["scenario"] = rob.scenario.map(lambda s: SCEN[s][0])
rob["model"] = rob.model.str.replace("fitur", "features")
rob = rob.rename(columns={"pr_auc": "PR_AUC"}).sort_values(["scenario_order", "model"]).reset_index(drop=True)
save(rob, "robustness.csv")

# 4. fairness: flag rate and recall per protected group (audit only, not model inputs)
f = pd.DataFrame(t2["fairness"]).rename_axis("group").reset_index()
f.insert(0, "group_type", f["group"].str.split("=").str[0]); f["group"] = f["group"].str.split("=").str[1]
save(f, "fairness_audit.csv")

# 5. monthly monitoring
m = pd.DataFrame(mon["monthly"]).rename_axis("month").reset_index()
STATUS = {"bulan pelatihan (dalam sampel)": "In-sample (training months)", "di luar sampel": "Out-of-sample", "sampel kecil / belum matang": "Out-of-sample, small sample"}
m["status"] = m.catatan.map(STATUS)
m = m.rename(columns={"baris": "rows", "hari_fraud": "fraud_days"})[["month", "status", "PR_AUC", "KS", "fraud_days", "rows"]]   # reason_psi / pemicu_KS left out: definition not verified
save(m, "monthly_monitoring.csv")

# 6. drift scenarios (injected on synthetic data). Letter prefixes keep the order alphabetical.
d = pd.DataFrame(mon["injected"]).T.rename_axis("scenario").reset_index()
SC = {"(a) sumber payee mati (30% baris)": "A. Payee source down (30% of rows)", "(b) perilaku bergeser seperti Lebaran": "B. Behaviour shift (Lebaran-like)", "(c) perubahan kecil 5%": "C. Small 5% change"}
d["scenario"] = d.scenario.map(SC)
d["keputusan_proposal_saja"] = d.keputusan_proposal_saja.replace({"tidak ada tindakan": "No action"})
d = d.rename(columns={"psi_skor": "score_psi", "fitur_drift": "drifted_features", "KS_turun_poin": "ks_drop_points", "PR_AUC_turun_relatif": "pr_auc_relative_drop",
                      "keputusan_proposal_saja": "decision_proposal_only", "keputusan_dengan_pemicu_tambahan": "decision_with_extra_triggers"})
save(d[["scenario", "score_psi", "ks_drop_points", "pr_auc_relative_drop", "decision_proposal_only", "decision_with_extra_triggers"]], "drift_scenarios.csv")

# 7. forecast quality (quantile model vs MVP rule)
fc = pd.DataFrame(lim["forecast"]).rename_axis("method").reset_index()
FM = {"MVP: 1,2 x P95(90h)": "A. MVP rule: 1.2 x P95 (90 days)", "XGB kuantil 0,95": "B. XGBoost quantile 0.95", "XGB kuantil 0,95 x 1,2": "C. XGBoost quantile 0.95 x 1.2"}
fc["method"] = fc.method.map(FM)
fc = fc.rename(columns={"coverage": "coverage", "pinball": "pinball_loss", "median_prakiraan_per_aktual": "median_forecast_over_actual"})
save(fc[["method", "coverage", "pinball_loss", "median_forecast_over_actual"]], "forecast_quality.csv")

# 7b. risk scorecard with vs without age (age is NOT used in the final scorecard; shown to document the cost of dropping it)
pa = lim["protected_audit"]
sc = pd.DataFrame({"variant": ["A. Without age (final)", "B. With age (not used)"], "AUC": [pa["AUC"]["tanpa usia (FINAL)"], pa["AUC"]["dengan usia (tidak dipakai)"]],
                   "Gini": [pa["Gini"]["tanpa usia (FINAL)"], pa["Gini"]["dengan usia (tidak dipakai)"]], "KS_points": [pa["KS"]["tanpa usia (FINAL)"], pa["KS"]["dengan usia (tidak dipakai)"]]})
save(sc, "scorecard_age_audit.csv")

# 8. scored validation cases (what the model returns in Viya) next to expected
v = pd.read_csv(os.path.join(HERE, "fraudXgbMaskV11", "lab_validation_cases.csv"))
save(v[["case_id", "expected_EM_EVENTPROBABILITY", "expected_band", "expected_quality", "expected_EM_CLASSIFICATION"]], "validation_scores.csv")
