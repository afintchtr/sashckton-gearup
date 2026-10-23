# Dynamic Customer Limit (SAS Hackathon 2026, Team Gear Up)

Fraud-gated, behaviour-based daily transfer limits: a fraud model and fraud gate, a risk scorecard and capacity forecast, a limit policy engine, a scoring API, and a simulation that compares static and dynamic limits. **All data is synthetic.** Proposal: `Dynamic-Customer-Limit-Proposal.docx` (section numbers such as 6.3 refer to it).

**Start here:** [docs/PROGRESS_REPORT.md](docs/PROGRESS_REPORT.md) (status, results, gaps, SAS Viya readiness) and [docs/CHECKPOINT.md](docs/CHECKPOINT.md) (what to do next).

## Map
| Folder / file | What it is |
|---|---|
| `docs/` | progress report and checkpoint |
| `notebooks/` | every analysis, documented, numbered in the order it was done (see below) |
| `generator/` | synthetic data generator (`gen_v2.py`) and its validator |
| `data/` | `dev/` (training and selection), `test/` and `test2/` (locked held-out sets, SHA-256 manifests, read-only), `sim/` (simulation customers) |
| `splits.py`, `build_features.py` | the single source of truth for splits and the training feature builder (kept at the root because every notebook imports them) |
| `limit/` | scorecard, capacity forecast, policy engine, simulation (+ `artifacts/`: fitted scorecard and forecast) |
| `serving/` | scoring API: rolling-feature store, fraud gate, limit service, demo, tests |
| `monitoring/` | PSI / KS / fairness / tier-migration monitor and the training reference distribution |
| `bundle/` | exported fraud models: `fraud_xgb_mask_v1_1` (**active**), `fraud_xgb_mask_v1` (comparison) |
| `models/`, `artifacts/` | trained model files; results (test reports, selection, tuning, robustness) |
| `sas_export/` | SAS Viya package: pickle, MAS score code, metadata, scorecard DATA step, validation cases, lab runbook |
| `mock_ui/` | interactive customer-journey mockup (English) |
| `deploy/` | Dockerfile, Kubernetes manifest, score template (written, not yet built or applied) |
| `archive/` | superseded v1 data files and early exploration notebooks |
| `.venv/` | Python environment (do not move: the Jupyter kernel points here) |

## Notebooks (run with the project root as working directory; each starts with a cell that does this)
| # | Notebook | Purpose |
|---|---|---|
| 02 | `02_model_selection` | baselines, Optuna tuning, model comparison |
| 02b | `02b_robustness` | training with corrupted data so the model survives missing inputs |
| 02c | `02c_decision_and_export` | champion decision and portable bundle (v1) |
| 03 | `03_final_test` | one-shot evaluation of v1 on TEST |
| 04 | `04_limit_scorecard_forecast` | WoE scorecard and capacity forecast |
| 05 | `05_limit_policy_simulation` | policy engine tests, static vs dynamic simulation, sensitivity |
| 06 | `06_protected_attribute_audit` | cost of removing gender, age, region |
| 07 / 07b | `07_model_v1_1`, `07b_final_test_v1_1` | model v1.1 (no protected attributes) and its one-shot evaluation on TEST2 |
| 08 | `08_monitoring` | monitor design, false-alarm and detection tests |

## Quick start
```bash
.venv/bin/python serving/seed_store.py                                       # demo feature store from DEV data
STORE_PATH=serving/demo.db .venv/bin/python -m uvicorn app:app --app-dir serving --port 8080
# then open http://localhost:8080/docs  (interactive API)
.venv/bin/python serving/demo.py                                             # replay fraud and normal customers through the API

# tests
.venv/bin/python serving/test_rolling_parity.py && .venv/bin/python serving/test_service.py && .venv/bin/python serving/test_limit.py
.venv/bin/python sas_export/preflight.py && .venv/bin/python sas_export/test_sas_package.py

# container (not yet built): docker build -f deploy/Dockerfile -t fraud-gate:1.1 .
```

## Reproduce the data (it is not in the repository: about 1.9 GB, all synthetic)
Seeds and prefixes make every dataset reproducible. Run from the project root with the pinned environment (`pip install -r requirements.txt`).
```bash
# 1) generate (generator writes relative to its own folder)
cd generator
python gen_v2.py --n 50000 --seed 7  --out ../data/dev
python gen_v2.py --n 20000 --seed 8  --prefix T --out ../data/test      # held-out set used for model v1
python gen_v2.py --n 20000 --seed 10 --prefix U --out ../data/test2     # held-out set used for model v1.1
python gen_v2.py --n 10000 --seed 9  --prefix S --out ../data/sim       # simulation customers
cd ..
# 2) features
python build_features.py --data data/dev   --out data/dev/snapshots.parquet                 # 90-day snapshots
python build_features.py --data data/dev   --out data/dev/daydetect.parquet --mode day      # day-level detection
python build_features.py --data data/test  --out data/test/daydetect.parquet  --mode day
python build_features.py --data data/test2 --out data/test2/daydetect.parquet --mode day
python limit/build_sim_features.py --data data/sim --out data/sim/sim_days.parquet --mode sim --neg-keep 1.0
python limit/build_targets.py                                                            # demand / declined-by-limit labels
# 3) lock the held-out sets: write data/test*/MANIFEST.json (SHA-256 of each file) and chmod -R a-w data/test data/test2
```
Hashes in a regenerated `MANIFEST.json` can differ from the original run if library versions differ; the numbers in the report were produced with the versions in `requirements.txt`.

## Notes
* Everything here is a competition prototype on synthetic data; no real customer data is used or included.
* No licence file is included yet: until one is added the code is "all rights reserved".
