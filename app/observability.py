import logging
import os
import re
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from opentelemetry import trace

from app.log_formatters import ConsoleFormatter, JsonFormatter


logger = logging.getLogger("review_analyzer")
current_observation = ContextVar("observation", default=None)
NOOP_TRACER = trace.NoOpTracerProvider().get_tracer(__name__)
DURATION_FIELDS = {
    "reviews.collect": "collection_duration_ms", "reviews.preprocess": "preprocessing_duration_ms",
    "sentiment.classify": "sentiment_duration_ms", "metrics.calculate": "metrics_duration_ms",
    "gemini.request": "gemini_duration_ms", "insights.validate": "validation_duration_ms",
}
USAGE_FIELDS = {
    "prompt_token_count": "gen_ai.usage.input_tokens",
    "candidates_token_count": "gen_ai.usage.output_tokens",
    "thoughts_token_count": "app.gen_ai.thinking_tokens",
    "total_token_count": "app.gen_ai.total_tokens",
    "cached_content_token_count": "gen_ai.usage.cache_read.input_tokens",
}


def configure_logging(settings):
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "review_analyzer_handler", False):
            root.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.review_analyzer_handler = True
    console = not os.environ.get("K_SERVICE") and (settings.log_format == "console" or (settings.log_format == "auto" and sys.stdout.isatty()))
    handler.setFormatter(ConsoleFormatter() if console else JsonFormatter())
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    logger.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "transformers"):
        dependency = logging.getLogger(name)
        dependency.handlers.clear()
        dependency.propagate = True
        dependency.setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "huggingface_hub", "transformers", "google", "opentelemetry"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.captureWarnings(True)


def emit(event, level=logging.INFO, **fields):
    message = event.replace(".", " ")
    if event.startswith("request."):
        message = f'{fields["method"]} {fields["path"]}: {fields["outcome"]}, HTTP {fields["status"]}, {fields["duration_ms"]:.0f} ms'
    logger.log(level, event, extra={"event_data": {"event": event, "message": message, **fields}})


def error_details(exc):
    cause = exc.__cause__ or exc
    return {
        "type": type(cause).__name__,
        "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name} for frame in traceback.extract_tb(cause.__traceback__)[-20:]],
    }


def record_exception(span, exc):
    details = error_details(exc)
    frames = "\n".join(f'  File "{frame["file"]}", line {frame["line"]}, in {frame["function"]}' for frame in details["frames"])
    span.add_event("exception", {"exception.type": details["type"], "exception.message": "Operation failed; exception details withheld", "exception.stacktrace": frames})
    span.set_attribute("error.type", details["type"])
    span.set_status(trace.Status(trace.StatusCode.ERROR))


@dataclass
class Observation:
    telemetry: object
    fields: dict = field(default_factory=dict)
    stages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    failure: dict | None = None
    finished: bool = False
    omitted: int = 0

    def update(self, **fields):
        if not self.finished:
            self.fields.update(fields)


def observe(**fields):
    observation = current_observation.get()
    if observation is not None:
        observation.update(**fields)


def increment(name):
    observation = current_observation.get()
    if observation is not None and not observation.finished:
        observation.fields[name] = observation.fields.get(name, 0) + 1


def warning(code, **fields):
    observation = current_observation.get()
    if observation is not None and not observation.finished:
        if len(observation.warnings) < 32:
            observation.warnings.append({"code": code, **fields})
        else:
            observation.omitted += 1


@contextmanager
def operation(name, attributes=None, kind=trace.SpanKind.INTERNAL):
    observation = current_observation.get()
    tracer = observation.telemetry.tracer if observation else NOOP_TRACER
    started = time.monotonic()
    failed = False
    with tracer.start_as_current_span(name, attributes=attributes, kind=kind, record_exception=False, set_status_on_exception=False) as span:
        try:
            yield span
        except Exception as exc:
            failed = True
            if name == "insights.validate":
                span.set_attribute("app.validation.failures", 1)
            record_exception(span, exc)
            if observation is not None and not observation.finished:
                observation.failure = {"stage": name, **error_details(exc)}
            raise
        finally:
            duration = round((time.monotonic() - started) * 1000, 3)
            if observation is not None and not observation.finished:
                if len(observation.stages) < 128:
                    observation.stages.append({"name": name, "duration_ms": duration, "outcome": "error" if failed else "success"})
                else:
                    observation.omitted += 1
                if name in DURATION_FIELDS:
                    key = DURATION_FIELDS[name]
                    observation.fields[key] = round(observation.fields.get(key, 0) + duration, 3)


def record_usage(span, usage):
    available = usage is not None
    span.set_attribute("app.gen_ai.usage_available", available)
    counts = {}
    for key, attribute in USAGE_FIELDS.items():
        value = usage.get(key) if usage is not None else None
        if type(value) is int and value >= 0:
            span.set_attribute(attribute, value)
            counts[key] = value
    observation = current_observation.get()
    if observation is not None and not observation.finished:
        totals = dict(observation.fields.get("gemini_usage", {}))
        for key, count in counts.items():
            totals[key] = totals.get(key, 0) + count
        observe(gemini_usage_available=available and observation.fields.get("gemini_usage_available", True), gemini_usage=totals)
        if not available:
            increment("gemini_usage_missing_count")


class RequestObservationMiddleware:
    def __init__(self, app, telemetry, settings):
        self.app = app
        self.telemetry = telemetry
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        supplied = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = supplied if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", supplied) else uuid.uuid4().hex
        path = scope.get("path", "")
        known = path in {"/", "/docs", "/redoc", "/openapi.json", "/api/analyze", "/api/reviews/collect", "/api/health"}
        route = path if known else "/static/*" if path.startswith("/static/") else "unmatched"
        observation = Observation(self.telemetry, {"request_id": request_id, "method": scope["method"], "path": route})
        token = current_observation.set(observation)
        started = time.monotonic()
        status = 500
        interrupted = False
        request_failed = False
        try:
            context = trace.get_current_span().get_span_context()
            if context.is_valid:
                trace_id = format(context.trace_id, "032x")
                observation.update(trace_id=trace_id, span_id=format(context.span_id, "016x"))
                if self.settings.google_cloud_project:
                    observation.fields["logging.googleapis.com/trace"] = f"projects/{self.settings.google_cloud_project}/traces/{trace_id}"

            async def observed_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    message = {**message, "headers": [*message.get("headers", []), (b"x-request-id", request_id.encode("ascii"))]}
                await send(message)

            await self.app(scope, receive, observed_send)
        except BaseException as exc:
            interrupted = not isinstance(exc, Exception)
            request_failed = not interrupted
            observation.failure = observation.failure or {"stage": "http.request", **error_details(exc)}
            if interrupted:
                observation.update(processing_may_continue=True)
                record_exception(trace.get_current_span(), exc)
            raise
        finally:
            try:
                duration = round((time.monotonic() - started) * 1000, 3)
                failed = status >= 400 or request_failed
                event = "request.failed" if status >= 500 or request_failed else "request.rejected" if failed else "request.completed"
                level = logging.ERROR if status >= 500 or request_failed else logging.WARNING if failed or observation.warnings else logging.INFO
                outcome = "success"
                if observation.fields.get("shortfall", 0) or observation.fields.get("failed_pages", 0):
                    outcome = "empty" if observation.fields.get("reviews_collected") == 0 else "partial"
                if failed:
                    outcome = "error"
                if interrupted:
                    event, level = "request.interrupted", logging.WARNING
                    outcome = "interrupted"
                if not interrupted and not failed and route in {"/api/health", "/static/*", "/docs", "/redoc", "/openapi.json"}:
                    level = logging.DEBUG
                emit(event, level, **observation.fields, status=status, duration_ms=duration,
                     outcome=outcome,
                     stages=observation.stages, warnings=observation.warnings, omitted_detail_count=observation.omitted,
                     **({"error": observation.failure} if (failed or interrupted) and observation.failure else {}))
            finally:
                observation.finished = True
                current_observation.reset(token)
