import json
import logging
import resource
import sys
import threading
import time
from contextlib import contextmanager

import httpx

from app.services.collector import collect_reviews
from app.services.app_info import lookup_app_info
from app.services.insights import PROMPT_PATH, request_insights
from app.services.metrics import calculate_metrics
from app.services.preprocessor import prepare_reviews, preprocess_collection
from app.services.sentiment import analyze_sentiment
from app.services.validator import build_report
from app.settings import SENTIMENT_MODEL


logger = logging.getLogger("review_analyzer")


class AnalysisError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def empty_insights(metadata=None):
    return {"metadata": metadata or {}, "insights": {priority: {"repeated": [], "single": []} for priority in ("high", "medium", "low")}}


class Analyzer:
    def __init__(self, settings, classifier):
        self.settings = settings
        self.classifier = classifier
        self.lock = threading.Lock()

    def collect(self, request, deadline=None):
        if request.review_count > self.settings.max_review_count:
            raise AnalysisError(f"review_count must not exceed {self.settings.max_review_count}", 422)
        with httpx.Client(follow_redirects=True) as client:
            result = collect_reviews(client, request.app_id, request.country, request.review_count, request.seed, deadline)
        if not result["reviews"] and result["metadata"]["failed_pages"]:
            raise AnalysisError("Apple returned no accessible reviews; collection pages failed")
        return result

    def analyze(self, request):
        if not self.lock.acquire(blocking=False):
            raise AnalysisError("An analysis is already running; retry later", 409)
        started = time.monotonic()
        durations = {}
        usage = {}
        warnings = []

        @contextmanager
        def stage(name):
            if time.monotonic() - started > self.settings.analysis_timeout_seconds:
                raise AnalysisError("Analysis exceeded its processing deadline", 504)
            before = time.monotonic()
            try:
                yield
            finally:
                durations[name] = round(time.monotonic() - before, 4)

        try:
            with stage("collection"):
                collected = self.collect(request, started + self.settings.analysis_timeout_seconds)
            with stage("app_info"):
                app_info = lookup_app_info(request.app_id, request.country)
            with stage("preprocessing"):
                prepared = preprocess_collection(collected)
            with stage("rating_metrics"):
                calculate_metrics(collected, empty_insights(collected["metadata"]))
            with stage("sentiment"):
                classified = analyze_sentiment(prepared, self.classifier, self.settings.sentiment_revision)
                labels = {review["id"]: review["sentiment"] for review in classified["reviews"]}
                classified["reviews"] = [{**review, "sentiment": labels[review["id"]]} for review in collected["reviews"]]
            with stage("sentiment_metrics"):
                calculate_metrics(classified, empty_insights(classified["metadata"]))
            with stage("gemini"):
                groups = prepare_reviews(classified)
                prompt = PROMPT_PATH.read_text(encoding="utf-8")
                model_report = request_insights(groups, prompt, usage, self.settings.gemini_model)
            with stage("validation"):
                insights = build_report(classified, groups, model_report, prompt)
                metrics = calculate_metrics(classified, insights)
            issues = []
            for priority, buckets in insights["insights"].items():
                for items in buckets.values():
                    issues.extend({**issue, "priority": priority} for issue in items)
            collection = collected["metadata"]
            if collection["shortfall"]:
                warnings.append(f"Only {collection['actual_count']} of {collection['requested_count']} requested reviews were available.")
            if collection["failed_pages"]:
                warnings.append("Some Apple review pages failed; the collection is incomplete.")
            if prepared["metadata"]["preprocessing"]["truncated_review_ids"]:
                warnings.append("Oversized review text was truncated for model analysis; original text is preserved.")
            elapsed = round(time.monotonic() - started, 4)
            peak_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            metadata = {
                "models": {"sentiment": SENTIMENT_MODEL, "sentiment_revision": self.settings.sentiment_revision, "insights": self.settings.gemini_model},
                "processing_time": {**durations, "total": elapsed},
                "gemini_usage": usage,
                "prompt_sha256": insights["metadata"]["insight_generation"]["prompt_sha256"],
                "peak_memory_mb": round(peak_bytes / 1024**2, 2),
                "preprocessing": prepared["metadata"]["preprocessing"],
                "warnings": warnings,
            }
            logger.info(json.dumps({"event": "analysis_completed", "app_id": request.app_id, "review_count": len(classified["reviews"]), **metadata}))
            return {
                "app": app_info,
                "collection": collection,
                "metrics": {"total_reviews": metrics["total_reviews"], "ratings": metrics["ratings"], "sentiment": metrics["sentiments"], "issues": metrics["issues"]},
                "insights": {
                    "issues": [{key: value for key, value in issue.items() if key != "action"} for issue in issues],
                    "recommendations": [{"category": issue["category"], "priority": issue["priority"], "action": issue["action"], "evidence_ids": issue["evidence_ids"]} for issue in issues],
                },
                "reviews": classified["reviews"],
                "metadata": metadata,
            }
        except AnalysisError:
            logger.error(json.dumps({"event": "analysis_failed", "stage": next(reversed(durations), "startup"), "duration_seconds": round(time.monotonic() - started, 4), "gemini_usage": usage}))
            raise
        except Exception as exc:
            logger.error(json.dumps({"event": "analysis_failed", "stage": next(reversed(durations), "startup"), "error_type": type(exc).__name__, "gemini_usage": usage}))
            if isinstance(exc, ValueError):
                raise AnalysisError("Model output failed schema or evidence validation; no report was produced") from exc
            raise AnalysisError("An upstream service or inference step failed; retry later") from exc
        finally:
            self.lock.release()
