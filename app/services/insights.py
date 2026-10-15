import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Literal

from google import genai
import httpx
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field
from opentelemetry import trace

from app.observability import increment, observe, operation, record_usage, warning
from app.services.execution import remaining_seconds
from app.services.review_batch import ReviewBatch

from app.settings import GEMINI_MODEL as MODEL
from app.services.preprocessor import prepare_reviews, preprocess_text
from app.services.validator import InsightValidationError, build_report


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/generate_insights.txt"
SENTIMENTS = ("negative", "positive", "neutral")
PRIORITIES = ("high", "medium", "low")


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=180)
    priority: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list, max_length=8)


class ModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[Issue]


class InsightDecodeError(ValueError):
    pass


def generate_insights(batch: ReviewBatch, run, model=MODEL):
    data, groups = batch.source, batch.groups
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    for attempt in range(2):
        with run.stage("gemini", "insights.repair" if attempt else "insights.generate", {
            "gen_ai.request.model": model, "app.prompt.version": hashlib.sha256(prompt.encode()).hexdigest(),
        }):
            observe(gemini_model=model, prompt_version=hashlib.sha256(prompt.encode()).hexdigest())
            response = request_insights(groups, prompt, run.usage, model, deadline=run.deadline)
        try:
            with run.stage("validation", "insights.validate") as span:
                report = build_report(data, groups, response, prompt, model)
                span.set_attribute("app.issues.count", report["summary"]["issue_count"])
                span.set_attribute("app.validation.failures", 0)
            return report
        except InsightValidationError as exc:
            observe(insight_validation_reason=exc.reason)
            warning("INSIGHTS_INVALID", reason=exc.reason)
            if attempt:
                raise
            observe(insight_repair_count=1)
            warning("INSIGHTS_RETRY", reason=exc.reason)
            prompt += "\nRegenerate the report. Previous validation failed: " + exc.reason
            prompt += ". Use each evidence ID at most once per issue and only IDs from this list: "
            prompt += json.dumps([review["id"] for review in groups["negative"]])
            prompt += ". Keep categories unique and actions nonempty."



def request_insights(groups, prompt, usage=None, model=MODEL, *, deadline=None):
    if not any(groups.values()):
        return ModelReport(issues=[])
    schema = ModelReport.model_json_schema()
    negative_ids = [review["id"] for review in groups["negative"]]
    if negative_ids:
        schema["$defs"]["Issue"]["properties"]["evidence_ids"]["items"]["enum"] = negative_ids
    else:
        schema["properties"]["issues"]["maxItems"] = 0
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("set GEMINI_API_KEY or GOOGLE_API_KEY before generating insights")
    with operation("gemini.request", {
        "gen_ai.request.model": model, "gen_ai.provider.name": "gcp.gemini",
        "gen_ai.operation.name": "generate_content", "app.prompt.version": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }, trace.SpanKind.CLIENT) as span:
        attempts = 0
        metadata = None

        def on_request(request):
            nonlocal attempts
            timeout = remaining_seconds(deadline, 60)
            request.extensions["timeout"] = httpx.Timeout(timeout).as_dict()
            request.headers["X-Server-Timeout"] = str(math.ceil(timeout))
            attempts += 1

        def on_response(response):
            span.set_attribute("http.response.status_code", response.status_code)
            observe(gemini_http_status=response.status_code)

        try:
            with httpx.Client(event_hooks={"request": [on_request], "response": [on_response]}) as transport, genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    timeout=60000,
                    retry_options=types.HttpRetryOptions(attempts=1),
                    httpx_client=transport,
                ),
            ) as client:
                for attempt in range(3):
                    timeout = remaining_seconds(deadline, 60)
                    try:
                        response = client.models.generate_content(
                            model=model,
                            contents=json.dumps(groups, ensure_ascii=False, separators=(",", ":")),
                            config=types.GenerateContentConfig(
                                system_instruction=prompt, response_mime_type="application/json",
                                response_json_schema=schema, max_output_tokens=8192,
                                http_options=types.HttpOptions(timeout=max(1, int(timeout * 1000))),
                            ),
                        )
                        break
                    except (errors.APIError, httpx.TimeoutException, httpx.ConnectError) as exc:
                        remaining_seconds(deadline, 60)
                        if attempt == 2 or (isinstance(exc, errors.APIError) and exc.code not in {408, 429, 500, 502, 503, 504}):
                            raise
                        delay = remaining_seconds(deadline, 2**attempt)
                        span.add_event("retry.scheduled", {"app.retry.attempt": attempt + 1, "app.retry.delay_ms": delay * 1000})
                        warning("UPSTREAM_RETRY", provider="gemini", attempt=attempt + 1, delay_ms=delay * 1000)
                        time.sleep(delay)
            provider_usage = getattr(response, "usage_metadata", None)
            if provider_usage is not None:
                metadata = provider_usage.model_dump(mode="json", exclude_none=True)
                if usage is not None:
                    for key, value in metadata.items():
                        if key.endswith("token_count") and type(value) is int:
                            usage[key] = usage.get(key, 0) + value
                        else:
                            usage[key] = value
        finally:
            record_usage(span, metadata)
            span.set_attribute("app.retry_count_available", attempts > 0)
            if attempts:
                span.set_attribute("app.retry_count", attempts - 1)
            observe(gemini_retry_count_available=attempts > 0)
            if attempts:
                increment("gemini_retry_count", attempts - 1)
            else:
                observe(gemini_retry_count=None)
        remaining_seconds(deadline, 60)
    with operation("insights.decode"):
        if not response.text:
            raise InsightDecodeError("Gemini returned no report; no insights were saved")
        try:
            return ModelReport.model_validate_json(response.text)
        except ValueError as exc:
            raise InsightDecodeError("Gemini returned an invalid report") from exc



def render_report(report):
    summary = report["summary"]
    counts = summary["sentiments"]
    lines = [
        "# Review insights", "",
        f"Reviews analyzed: {summary['total_reviews']}",
        f"Sentiment: {counts['negative']} negative · {counts['positive']} positive · {counts['neutral']} neutral",
        f"Negative reviews categorized: {summary['categorized_negative_reviews']}/{counts['negative']}",
    ]
    for priority, buckets in report["insights"].items():
        for recurrence, items in buckets.items():
            if items:
                lines.extend(["", f"## {priority.title()} priority — {recurrence} reports", ""])
                for item in items:
                    action = item["action"].replace("<", "&lt;").replace(">", "&gt;")
                    count = item["review_count"]
                    lines.append(f"- {action} — {count} {'review' if count == 1 else 'reviews'}.")
    if not summary["issue_count"]:
        lines.extend(["", "No supported actionable complaints were identified."])
    lines.extend(["", "Counts refer to unique reviews per issue; a review can support several issues."])
    return "\n".join(lines) + "\n"

