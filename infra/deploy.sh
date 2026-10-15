#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: bash infra/deploy.sh PROJECT_ID REGION SERVICE_ACCOUNT_EMAIL" >&2
  exit 2
fi

GCP_PROJECT="$1"
GCP_REGION="$2"
RUN_ACCOUNT="$3"
IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/review-analyzer/app:latest"
SECRET_NAME="${GEMINI_SECRET_NAME:-gemini-api-key}"

gcloud builds submit --project "$GCP_PROJECT" --tag "$IMAGE" .
gcloud run deploy app-store-review-analyzer \
  --project "$GCP_PROJECT" \
  --region "$GCP_REGION" \
  --image "$IMAGE" \
  --service-account "$RUN_ACCOUNT" \
  --cpu 1 \
  --memory 4Gi \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 1 \
  --timeout 300 \
  --set-env-vars "SENTIMENT_MODEL_PATH=/app/models/sentiment,HF_HUB_OFFLINE=1,SENTIMENT_THREADS=1,ANALYSIS_TIMEOUT_SECONDS=280" \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" \
  --no-allow-unauthenticated
