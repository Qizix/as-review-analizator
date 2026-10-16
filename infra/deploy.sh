#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: bash infra/deploy.sh PROJECT_ID REGION SERVICE_ACCOUNT_EMAIL" >&2
  exit 2
fi

GCP_PROJECT="$1"
GCP_REGION="$2"
RUN_ACCOUNT="$3"
cd "$(dirname "$0")/.."
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Commit all changes before deploying a revision." >&2
  exit 1
fi
REVISION="$(git rev-parse HEAD)"
IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/review-analyzer/app:${REVISION}"
SECRET_NAME="${GEMINI_SECRET_NAME:-gemini-api-key}"

uv run --locked --extra quality pytest -q
npm test --prefix frontend

gcloud builds submit --project "$GCP_PROJECT" --region europe-west1 --config infra/cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" .
gcloud run deploy app-store-review-analyzer \
  --project "$GCP_PROJECT" \
  --region "$GCP_REGION" \
  --image "$IMAGE" \
  --service-account "$RUN_ACCOUNT" \
  --cpu 1 \
  --no-cpu-throttling \
  --memory 4Gi \
  --min-instances 0 \
  --max 1 \
  --max-instances 1 \
  --concurrency 8 \
  --timeout 300 \
  --set-env-vars "WORK_ENABLED=true,MAX_REVIEW_COUNT=100,ANALYZE_REQUESTS_PER_MINUTE=3,ANALYZE_REQUESTS_PER_DAY=100,COLLECT_REQUESTS_PER_MINUTE=6,COLLECT_REQUESTS_PER_DAY=200,SENTIMENT_MODEL_PATH=/app/models/sentiment,HF_HUB_OFFLINE=1,SENTIMENT_THREADS=1,ANALYSIS_TIMEOUT_SECONDS=280,OTEL_ENABLED=true,OTEL_EXPORTER=google,OTEL_SERVICE_NAME=appstore-review-analyzer,GOOGLE_CLOUD_PROJECT=${GCP_PROJECT},LOG_LEVEL=INFO,LOG_FORMAT=json" \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:${GEMINI_SECRET_VERSION:-latest}" \
  --startup-probe "httpGet.path=/api/health,httpGet.port=8080,periodSeconds=5,timeoutSeconds=3,failureThreshold=48" \
  --no-invoker-iam-check

gcloud run services update-traffic app-store-review-analyzer --project "$GCP_PROJECT" --region "$GCP_REGION" --to-latest

SERVICE_URL="$(gcloud run services describe app-store-review-analyzer --project "$GCP_PROJECT" --region "$GCP_REGION" --format='value(status.url)')"
uv run --locked python scripts/check_health.py "$SERVICE_URL" --require-gemini
