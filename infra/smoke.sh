#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash infra/smoke.sh IMAGE" >&2
  exit 2
fi
cd "$(dirname "$0")/.."
IMAGE="$1"
SMOKE_PORT="${SMOKE_PORT:-18080}"
docker run --rm --network none --cpus 1 --memory 4g "$IMAGE" .venv/bin/python -c 'from app.services.sentiment import load_classifier; result = load_classifier()(["I love this app"], truncation=True, max_length=512); assert result[0]["label"].lower() in {"positive", "neutral", "negative"}'
CONTAINER="$(docker run -d --rm --cpus 1 --memory 4g -p "127.0.0.1:${SMOKE_PORT}:8080" -e OTEL_ENABLED=false -e LOG_FORMAT=json "$IMAGE")"
cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then docker logs "$CONTAINER" >&2 || true; fi
  docker rm -f "$CONTAINER" >/dev/null || true
}
trap cleanup EXIT
[[ "$(docker exec "$CONTAINER" id -u)" != "0" ]]
python3 scripts/check_health.py "http://127.0.0.1:${SMOKE_PORT}" --timeout 240
curl --fail --silent --output /dev/null "http://127.0.0.1:${SMOKE_PORT}/"
curl --fail --silent --output /dev/null "http://127.0.0.1:${SMOKE_PORT}/docs"
STATUS="$(curl --silent --output /dev/null --write-out '%{http_code}' -H 'Content-Type: application/json' -d '{"app_id":"123"}' "http://127.0.0.1:${SMOKE_PORT}/api/analyze")"
[[ "$STATUS" == "503" ]]
echo "Container smoke checks passed"
