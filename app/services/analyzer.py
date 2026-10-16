import resource
import sys
import time

import httpx
from app.observability import observe, operation, warning
from app.admission import Admission
from app.services.collector import collect_reviews
from app.services.app_info import lookup_app_info
from app.services.insights import generate_insights, InsightDecodeError
from app.services.metrics import calculate_metrics
from app.services.review_batch import ReviewBatch
from app.services.sentiment import BATCH_SIZE
from app.services.validator import InsightValidationError
from app.services.execution import AnalysisError, AnalysisRun
from app.settings import SENTIMENT_MODEL


class Analyzer:
    def __init__(self, settings, classifier):
        self.settings = settings
        self.classifier = classifier
        self.admission = Admission(settings)

    def collect(self, request, deadline=None):
        observe(app_id=request.app_id, country=request.country, requested_count=request.review_count)
        self.check_count(request)
        with self.admission.work("collection"):
            return self._collect(request, deadline)

    def check_count(self, request):
        if request.review_count > self.settings.max_review_count:
            raise AnalysisError(f"review_count must not exceed {self.settings.max_review_count}", 422, "INVALID_REQUEST", "admission")

    def _collect(self, request, deadline=None):
        with operation("reviews.collect", {"app.reviews.requested_count": request.review_count, "app.country": request.country}) as span:
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
        self.check_count(request)
        with self.admission.work("analysis"):
            return self._analyze(request)

    def _analyze(self, request):
        run = AnalysisRun(self.settings)
        warnings = []
        stage = run.stage

        try:
            with stage("collection"):
                collected = self._collect(request, run.deadline)
            with stage("app_info", "appstore.lookup"):
                app_info = lookup_app_info(request.app_id, request.country, deadline=run.deadline)
            with stage("preprocessing", "reviews.preprocess"):
                batch = ReviewBatch(collected)
                preprocessing = batch.preprocessing
            with stage("sentiment", "sentiment.classify", {"app.model": SENTIMENT_MODEL, "app.reviews.count": len(collected["reviews"]), "app.batch_size": BATCH_SIZE}):
                batch.classify(self.classifier, self.settings.sentiment_revision)
                classified = batch.source
            insights = generate_insights(batch, run, self.settings.gemini_model)
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
                if preprocessing["truncated_review_ids"]:
                    warnings.append("Oversized review text was truncated for model analysis; original text is preserved.")
                elapsed = round(time.monotonic() - run.started, 4)
                peak_bytes = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
                metadata = {
                    "models": {"sentiment": SENTIMENT_MODEL, "sentiment_revision": self.settings.sentiment_revision, "insights": self.settings.gemini_model},
                    "processing_time": {**run.durations, "total": elapsed},
                    "gemini_usage": run.usage,
                    "prompt_sha256": insights["metadata"]["insight_generation"]["prompt_sha256"],
                    "peak_memory_mb": round(peak_bytes / 1024**2, 2),
                    "preprocessing": preprocessing,
                    "warnings": warnings,
                }
                observe(issues_identified=len(issues), peak_process_rss_mb=metadata["peak_memory_mb"])
                observe(sentiment_counts=metrics["sentiments"]["counts"])
                if preprocessing["truncated_review_ids"]:
                    warning("TEXT_TRUNCATED", count=len(preprocessing["truncated_review_ids"]))
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
