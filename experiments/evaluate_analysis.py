import hashlib
import json
import time
from pathlib import Path

import hydra
from google.genai.errors import APIError
from hydra.core.hydra_config import HydraConfig
from hydra.utils import to_absolute_path

from app.services.execution import AnalysisError, AnalysisRun
from app.services.insights import InsightDecodeError
from app.services.interpretation import interpret_reviews
from app.services.metrics import calculate_metrics
from app.services.review_batch import ReviewBatch
from app.services.validator import InsightValidationError
from app.settings import Settings


LABELS = ("negative", "neutral", "positive")


def classification_scores(predictions, gold):
    if set(predictions) != set(gold) or any(label not in LABELS for label in (*gold.values(), *predictions.values())):
        raise ValueError("Gold labels must cover exactly the review IDs with valid sentiment names")
    f1 = []
    for label in LABELS:
        tp = sum(predictions[key] == label == gold[key] for key in gold)
        fp = sum(predictions[key] == label and gold[key] != label for key in gold)
        fn = sum(predictions[key] != label and gold[key] == label for key in gold)
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    negative = sum(label == "negative" for label in gold.values())
    return {"macro_f1": sum(f1) / len(LABELS), "negative_recall": sum(predictions[key] == gold[key] == "negative" for key in gold) / negative if negative else None}


@hydra.main(version_base=None, config_path="../conf", config_name="analysis_evaluation")
def main(cfg):
    if not cfg.modes or len(set(cfg.modes)) != len(cfg.modes) or any(mode not in {"fast", "quality"} for mode in cfg.modes):
        raise ValueError("Select fast and/or quality once each")
    raw = Path(to_absolute_path(cfg.input)).read_bytes()
    saved = json.loads(raw)
    data = {"metadata": saved.get("collection", saved.get("metadata", {})), "reviews": [{key: review[key] for key in ("id", "title", "text", "rating")} for review in saved["reviews"]]}
    settings = Settings.from_env().model_copy(update={"otel_enabled": False})
    classifier = None
    if "quality" in cfg.modes:
        from app.services.sentiment import load_classifier

        classifier = load_classifier(settings)
    gold = json.loads(Path(to_absolute_path(cfg.gold_labels)).read_text()) if cfg.gold_labels else None
    output = Path(HydraConfig.get().runtime.output_dir)
    summaries, reports, predictions = [], {}, {}
    for mode in cfg.modes:
        run = AnalysisRun(settings)
        started = time.monotonic()
        failure = None
        try:
            with run.stage("preprocessing", "reviews.preprocess"):
                batch = ReviewBatch(data)
            insights = interpret_reviews(batch, mode, classifier, run, settings)
            with run.stage("metrics", "metrics.calculate"):
                metrics = calculate_metrics(batch.source, insights)
            predictions[mode] = {review["id"]: review["sentiment"]["label"] for review in batch.source["reviews"]}
            reports[mode] = {"reviews": batch.source["reviews"], "insights": insights, "metrics": metrics}
        except (AnalysisError, APIError, InsightDecodeError, InsightValidationError) as exc:
            failure = {"type": type(exc).__name__, "stage": run.active_stage, "reason": exc.reason if isinstance(exc, InsightValidationError) else "Provider or structured output failure"}
        summary = {"mode": mode, "status": "failed" if failure else "success", "duration_seconds": time.monotonic() - started, "stages_seconds": run.durations, "gemini_usage": run.usage, "failure": failure}
        if not failure:
            summary.update(sentiment_counts=metrics["sentiments"]["counts"], issue_count=insights["summary"]["issue_count"], negative_issue_coverage=metrics["issues"]["coverage"], prompt_sha256=insights["metadata"]["insight_generation"]["prompt_sha256"], quality_scores=classification_scores(predictions[mode], gold) if gold else None)
        summaries.append(summary)
        print(f"{mode}: {summary['status']}, {summary['duration_seconds']:.3f}s")
    ids = [review["id"] for review in data["reviews"]]
    agreement = sum(predictions["fast"][key] == predictions["quality"][key] for key in ids) / len(ids) if ids and set(predictions) == {"fast", "quality"} else None
    summary = {"input_sha256": hashlib.sha256(raw).hexdigest(), "review_count": len(ids), "model": settings.gemini_model, "sentiment_revision": settings.sentiment_revision, "torch_threads": settings.sentiment_threads, "scope": "Local inference on identical saved reviews; excludes collection and model loading; one run per mode", "sentiment_agreement": agreement, "runs": summaries}
    (output / "evaluation.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "reports.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
    print(f"Saved evaluation to {output / 'evaluation.json'}")
    if any(run["status"] == "failed" for run in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
