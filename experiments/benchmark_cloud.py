import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import hydra
from hydra.core.hydra_config import HydraConfig

from app.schemas import AnalysisResponse
from app.services.metrics import calculate_metrics


def verify_report(report):
    AnalysisResponse.model_validate(report)
    reviews = report["reviews"]
    source = {review["id"]: review for review in reviews}
    if len(source) != len(reviews) or any(type(review["rating"]) is not int or not 1 <= review["rating"] <= 5 for review in reviews):
        raise ValueError("Invalid or duplicate review records")
    grouped = {priority: {"single": [], "repeated": []} for priority in ("high", "medium", "low")}
    for issue in report["insights"]["issues"]:
        ids = set(issue["evidence_ids"])
        if len(ids) != len(issue["evidence_ids"]) or issue["review_count"] != len(ids):
            raise ValueError("Invalid issue support count")
        quotes = issue["evidence_quotes"]
        if len(quotes) != len(ids) or {quote["review_id"] for quote in quotes} != ids:
            raise ValueError("Incomplete issue quotations")
        for quote in quotes:
            original = source[quote["review_id"]]
            if not quote["quote"].strip() or not any(quote["quote"] in original[field] for field in ("title", "text")):
                raise ValueError("Quotation does not occur in original review")
        grouped[issue["priority"]]["repeated" if len(ids) > 1 else "single"].append(issue)
    actual = calculate_metrics({"reviews": reviews}, {"insights": grouped})
    expected = {"total_reviews": actual["total_reviews"], "ratings": actual["ratings"], "sentiment": actual["sentiments"], "issues": actual["issues"]}
    if expected != report["metrics"]:
        raise ValueError("Report metrics do not match source reviews")
    collection = report["collection"]
    if collection["actual_count"] != len(reviews) or collection["shortfall"] != max(0, collection["requested_count"] - len(reviews)):
        raise ValueError("Collection totals are inconsistent")
    canonical = [{key: review[key] for key in ("id", "title", "text", "rating")} for review in sorted(reviews, key=lambda review: review["id"])]
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@hydra.main(version_base=None, config_path="../conf", config_name="cloud_benchmark")
def main(cfg):
    if not cfg.modes or any(mode not in {"fast", "quality"} for mode in cfg.modes) or not 1 <= cfg.repeats <= 10:
        raise ValueError("Choose supported modes and 1–10 repeats")
    output = Path(HydraConfig.get().runtime.output_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
    records = []
    with httpx.Client(timeout=310, follow_redirects=True) as client:
        for repetition in range(1, cfg.repeats + 1):
            for mode in cfg.modes:
                request_id = f"cloud-benchmark-{run_id}-{mode}-{repetition}"
                body = {"app_id": str(cfg.app_id), "country": cfg.country, "review_count": cfg.review_count, "mode": mode}
                record = {"request_id": request_id, "repetition": repetition, "mode": mode, "requested_count": cfg.review_count, "started_at": datetime.now(timezone.utc).isoformat()}
                print(f"Starting {request_id}", flush=True)
                started = time.monotonic()
                try:
                    response = client.post(cfg.base_url.rstrip("/") + "/api/analyze", json=body, headers={"x-request-id": request_id})
                    record.update(http_status=response.status_code, client_duration_seconds=time.monotonic() - started, response_bytes=len(response.content))
                    payload = response.json()
                    (output / f"{mode}-{repetition}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                    if response.status_code == 200:
                        if payload["metadata"].get("mode") != mode:
                            raise ValueError("Response uses a different analysis mode")
                        record.update(review_payload_sha256=verify_report(payload), verified=True, actual_count=len(payload["reviews"]), collection=payload["collection"], metrics=payload["metrics"], metadata=payload["metadata"], sentiments_by_id={review["id"]: review["sentiment"]["label"] for review in payload["reviews"]}, original_review_characters=sum(len(review["title"]) + len(review["text"]) for review in payload["reviews"]))
                    else:
                        record.update(verified=False, error_detail=payload.get("detail", "Request failed"))
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    record.update(verified=False, failure_type=type(exc).__name__, client_duration_seconds=time.monotonic() - started)
                record["finished_at"] = datetime.now(timezone.utc).isoformat()
                records.append(record)
                (output / "requests.json").write_text(json.dumps({"run_id": run_id, "base_url": cfg.base_url, "records": records}, indent=2) + "\n")
                count_label = f"{record['actual_count']}/{cfg.review_count} reviews" if "actual_count" in record else "no report"
                print(f"Completed {mode} #{repetition}: HTTP {record.get('http_status', '?')}, {count_label}, {record['client_duration_seconds']:.3f}s, verified={record['verified']}", flush=True)
    print(f"Saved experiment to {output / 'requests.json'}", flush=True)
    if not all(record["verified"] for record in records):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
