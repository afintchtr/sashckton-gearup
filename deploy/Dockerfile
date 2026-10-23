# Fraud-gate scoring service (competition demo; synthetic data only)
# Build from the project root:  docker build -f deploy/Dockerfile -t fraud-gate:1.1 .
FROM python:3.10-slim
WORKDIR /app
COPY serving/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && apt-get update >/dev/null 2>&1 || true
COPY bundle/fraud_xgb_mask_v1_1 /app/bundle/fraud_xgb_mask_v1_1
COPY serving/app.py serving/store.py serving/rolling.py serving/gate.py serving/limit_service.py /app/serving/
COPY limit/policy.py limit/scorecard.py limit/forecast.py /app/limit/
COPY limit/artifacts/scorecard.json limit/artifacts/forecast_q95.json limit/artifacts/tier_cuts.json /app/limit/artifacts/
COPY serving/demo.db /app/seed/demo.db
ENV BUNDLE_DIR=/app/bundle/fraud_xgb_mask_v1_1 STORE_PATH=/data/store.db LOG_PATH=/data/decision_log.jsonl PYTHONUNBUFFERED=1
EXPOSE 8080
# seed the store on first start (empty volume), then serve. SQLite => ONE replica; swap store.py for Redis/Postgres to scale out.
CMD ["sh", "-c", "mkdir -p /data && [ -f /data/store.db ] || cp /app/seed/demo.db /data/store.db; exec python -m uvicorn app:app --app-dir /app/serving --host 0.0.0.0 --port 8080"]
