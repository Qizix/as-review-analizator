import resource
import sys
import threading
import time

import httpx
from app.observability import observe, operation, warning
from app.services.collector import collect_reviews
from app.services.app_info import lookup_app_info
from app.services.insights import generate_insights, InsightDecodeError
from app.services.metrics import calculate_metrics
from app.services.preprocessor import group_reviews, preprocess_collection
from app.services.sentiment import analyze_sentiment
from app.services.validator import InsightValidationError
from app.services.execution import AnalysisError, AnalysisRun
from app.settings import SENTIMENT_MODEL


class Analyzer:
    def __init__(self, settings, classifier):
        self.settings = settings
        self.classifier = classifier
        self.lock = threading.Lock()

    def collect(self, request, deadline=None):
        observe(app_id=request.app_id, country=request.country, requested_count=request.review_count)
        with operation("reviews.collect", {"app.reviews.requested_count": request.review_count, "app.country": request.country}) as span:
            if request.review_count > self.settings.max_review_count:
                raise AnalysisError(f"review_count must not exceed {self.settings.max_review_count}", 422, "INVALID_REQUEST", "reviews.collect")
            with httpx.Client(follow_redirects=True) as client:
                result = collect_reviews(client, request.app_id, request.country, request.review_count, request.model_dump()["seed"], deadline)
            metadata = result["metadata"]
            span.set_attribute("app.reviews.collected_count", len(result["reviews"]))
            span.set_attribute("app.collection.failed_pages", len(metadata["failed_pages"]))
            observe(reviews_collected=len(result["reviews"]), shortfall=metadata["shortfall"], failed_pages=len(metadata["failed_pages"]))
            for key in ("invalid_records", "duplicate_records"):
                if key in metadata:
                    observe(**{key: metadata[key]})
            if metadata["shortfall"]:
                warning("REVIEW_SHORTFALL", count=metadata["shortfall"])
            if metadata["failed_pages"]:
                warning("APPLE_PAGE_FAILED", count=len(metadata["failed_pages"]))
            if not result["reviews"] and metadata["failed_pages"]:
                raise AnalysisError("Apple returned no accessible reviews; collection pages failed", code="APPLE_UNAVAILABLE", stage="reviews.collect")
            return result

    def analyze(self, request):
        if not self.lock.acquire(blocking=False):
            raise AnalysisError("An analysis is already running; retry later", 409, "ANALYSIS_BUSY", "startup")
        run = AnalysisRun(self.settings)
        warnings = []
        stage = run.stage

        try:
            with stage("collection"):
                collected = self.collect(request, run.deadline)
            with stage("app_info", "appstore.lookup"):
                app_info = lookup_app_info(request.app_id, request.country)
            with stage("preprocessing", "reviews.preprocess"):
                prepared = preprocess_collection(collected)
            with stage("sentiment", "sentiment.classify", {"app.model": SENTIMENT_MODEL, "app.reviews.count": len(prepared["reviews"]), "app.batch_size": 8}):
                normalized = analyze_sentiment(prepared, self.classifier, self.settings.sentiment_revision)
                labels = {review["id"]: review["sentiment"] for review in normalized["reviews"]}
                classified = {**normalized, "reviews": [{**review, "sentiment": labels[review["id"]]} for review in collected["reviews"]]}
            insights = generate_insights(classified, group_reviews(normalized), run, self.settings.gemini_model)
            with stage("metrics", "metrics.calculate", {"app.reviews.count": len(classified["reviews"]), "app.metrics.phase": "issues"}):
                metrics = calculate_metrics(classified, insights)
            with stage("report_assembly", "report.assemble"):
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
                elapsed = round(time.monotonic() - run.started, 4)
                peak_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
                metadata = {
                    "models": {"sentiment": SENTIMENT_MODEL, "sentiment_revision": self.settings.sentiment_revision, "insights": self.settings.gemini_model},
                    "processing_time": {**run.durations, "total": elapsed},
                    "gemini_usage": run.usage,
                    "prompt_sha256": insights["metadata"]["insight_generation"]["prompt_sha256"],
                    "peak_memory_mb": round(peak_bytes / 1024**2, 2),
                    "preprocessing": prepared["metadata"]["preprocessing"],
                    "warnings": warnings,
                }
                observe(issues_identified=len(issues), peak_process_rss_mb=metadata["peak_memory_mb"])
                observe(sentiment_counts=metrics["sentiments"]["counts"])
                if prepared["metadata"]["preprocessing"]["truncated_review_ids"]:
                    warning("TEXT_TRUNCATED", count=len(prepared["metadata"]["preprocessing"]["truncated_review_ids"]))
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
            raise
        except Exception as exc:
            code = {
                "collection": "APPLE_UNAVAILABLE", "sentiment": "SENTIMENT_FAILED",
                "preprocessing": "REVIEWS_INVALID", "gemini": "GEMINI_FAILED", "validation": "INSIGHTS_INVALID",
            }.get(run.active_stage, "ANALYSIS_FAILED")
            if isinstance(exc, (InsightDecodeError, InsightValidationError)):
                code = "INSIGHTS_INVALID"
                raise AnalysisError("Model output failed schema or evidence validation; no report was produced", code=code, stage=run.active_stage) from exc
            raise AnalysisError("An upstream service or inference step failed; retry later", code=code, stage=run.active_stage) from exc
        finally:
            observe(analysis_duration_ms=round((time.monotonic() - run.started) * 1000, 3))
            self.lock.release()
