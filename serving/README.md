# Fraud-gate scoring service (stand-in for SAS MAS REST, proposal section 8)

> **Status (27 Oct 2026): prototype, reference implementation and test oracle.** The target architecture puts the runtime in **SAS Viya** (middleware -> Viya gateway endpoint -> Intelligent Decisioning flows; model in Viya; PostgreSQL keyed by CIF; Kafka for transactions, channel enrichment and actions; Python for model development only). This service is **not** the production runtime. It remains useful as (1) the **specification** of the rolling features, fraud gate and limit engine that must be rebuilt in Viya, (2) the **oracle** for parity tests (`test_rolling_parity.py`: 68/68 features identical to training; `sas_export/decisioning/decision_flow_ref.py --check-backend`: gate rule tables identical on 3,000 random rows), and (3) a runnable demo. See `docs/STORYBOARD.md` (frames 2.6, 3.1-3.3) and `docs/DIAGRAMS.md`. The Dockerfile and Kubernetes manifest under `deploy/` are prototype-only and were never built or applied.
Serves model **v1.1** (63 features, no gender / age / region / tenure quirk) from `bundle/fraud_xgb_mask_v1_1`. Override with `BUNDLE_DIR`.

## How the rolling features work
Every model feature (7/30/90-day sums, P95, shares, trends...) is a **pure function of the customer's last 90 daily records**
plus static profile and the as-of date. So the feature store keeps *daily records*, not features:

```
 core banking / the mobile banking app events                                   02:00 batch (proposal 8.1)
        |                                                              |
        v                                                              v
  POST /events  --->  TODAY's running daily record  <---  closes yesterday, fills idle days (zeros, balance carried forward),
                      (sum / count / max updated             prunes records older than ~95 days
                       per event: outflow, night amount,
                       new payee, distinct payees,
                       top-payee share, geo, device...)
                                   |
        90-day ring buffer of daily records per customer   (SQLite here; Redis/Postgres in production)
                                   |
                                   v
             rolling.compute_features()  --- the SAME arithmetic as training (build_features.py) ---> 68 features
                                   |
                                   v
               model bundle (XGB|mask)  ->  score, band, SHAP reasons        +   gate.evaluate() (proposal 6.3 rules)
                                   |
                                   v
              verdict ALLOW / STEP_UP_AUTH / HOLD, block_increase flag, customer message, decision log (JSONL)
```
Why this removes training-serving skew: training, the daily batch and the real-time scorer all call the same feature function over the same
record schema. `test_rolling_parity.py` proves it: 6,000 customer-days (695 fraud days) recomputed from raw daily records give **identical values for all 68 features**.

Conventions the store must respect: a record exists for **every** day since opening (idle day = zeros, balance carried forward);
the as-of day's record is day-to-date.

## Endpoints
`POST /events` (stream transfers, inflows, device/credential changes, alerts, disputes) | `POST /score` | `GET /customers/{id}/features` |
`GET /health` `GET /ready` `GET /metrics` (Prometheus text).
Limit engine: `POST /limit/evaluate` | `/limit/accept` | `/limit/self-cap` | `/limit/appeal` | `/limit/review` | `GET /limit/{id}` | `POST /batch/daily?date=` (the 02:00 batch). State (tier, cooldown, offer, self cap, reviewer pin) lives in the `limits` table; policy in `limit/policy.py`.

## Run locally
```bash
python serving/seed_store.py                       # builds serving/demo.db from the DEV synthetic data (no TEST data)
STORE_PATH=serving/demo.db python -m uvicorn app:app --app-dir serving --port 8080
python serving/demo.py                              # replays fraud / normal customers as event streams and scores them
python serving/test_rolling_parity.py               # training vs serving feature parity
python serving/test_service.py                      # ingestion semantics + end-to-end replay vs reference path
```
Container / cluster: `deploy/Dockerfile` (build from the project root: `docker build -f deploy/Dockerfile .`), `deploy/k8s/fraud-gate.yaml` (1 replica: SQLite is a single-writer store; replace `store.py` with Redis/Postgres before scaling or adding an HPA).

## Known limits (read before presenting)
* **Day-level model.** It was trained on end-of-day aggregates. Calling `/score` mid-day uses day-to-date aggregates (lower than a full day), so scores are conservative; an intraday-trained model needs intraday labels, which the synthetic data does not have.
* **Event replay is approximate** (daily records carry no intraday detail): replaying days as events gives Spearman 0.95 / 96.6% band agreement vs the exact-record path, and flags 67% of fraud days vs 100% on exact records (`test_report.json`).
* `tenure_months` / `hist_days` replicate a training quirk (measured from 2025-07-01, not true account age). Retrain with true tenure before real use.
* No dedicated **mule-account model** yet (a hand-written proxy was rejected: it flagged 46% of normal days). Mule typology is covered by the fraud score.
* Synthetic data only; performance numbers validate the pipeline, not production behaviour.
