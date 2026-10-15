#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash infra/smoke.sh IMAGE" >&2
  exit 2
fi
cd "$(dirname "$0")/.."
IMAGE="$1"
docker run --rm --network none --cpus 1 --memory 4g "$IMAGE" .venv/bin/python -c 'import os; from app.services.sentiment import load_classifier; assert os.getuid() != 0; result = load_classifier()(["I love this app"], truncation=True, max_length=512); assert result[0]["label"].lower() in {"positive", "neutral", "negative"}'
CONTAINER="$(docker run -d --rm --network none --cpus 1 --memory 4g -e OTEL_ENABLED=false -e LOG_FORMAT=json "$IMAGE")"
cleanup() {
  status=$?
  if [[ $status -ne 0 ]]; then docker logs "$CONTAINER" >&2 || true; fi
  docker rm -f "$CONTAINER" >/dev/null || true
}
trap cleanup EXIT
docker exec -i "$CONTAINER" .venv/bin/python - http://127.0.0.1:8080 --timeout 240 < scripts/check_health.py
docker exec -i "$CONTAINER" .venv/bin/python - <<'PYTHON'
import urllib.error
import urllib.request

base = "http://127.0.0.1:8080"
for path in ("/", "/docs", "/static/app.mjs"):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        assert response.status == 200
request = urllib.request.Request(base + "/api/analyze", data=b'{"app_id":"123"}', headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as exc:
    assert exc.code == 503
else:
    raise AssertionError("Analysis unexpectedly succeeded without Gemini credentials")
PYTHON
echo "Container smoke checks passed"
