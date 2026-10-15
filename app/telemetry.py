import json
import logging
import re
import sys

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ALWAYS_ON


logger = logging.getLogger("review_analyzer")
HTTP_ATTRIBUTES = {
    "http.method", "http.request.method", "http.route", "http.status_code",
    "http.response.status_code", "http.scheme", "url.scheme", "network.protocol.version",
}
SPAN_ATTRIBUTES = HTTP_ATTRIBUTES | {
    "error.type", "app.reviews.requested_count", "app.country", "app.reviews.collected_count",
    "app.collection.failed_pages", "app.collection.source", "app.reviews.count", "app.metrics.phase", "app.model",
    "app.batch_size", "app.prompt.version", "app.issues.count", "app.validation.failures",
    "app.provider", "app.retry_count", "app.retry_count_available", "app.gen_ai.usage_available",
    "gen_ai.request.model", "gen_ai.provider.name", "gen_ai.operation.name",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "app.gen_ai.thinking_tokens",
    "app.gen_ai.total_tokens", "gen_ai.usage.cache_read.input_tokens",
}


def diagnostic(event, exc=None):
    fields = {"event": event, "message": event.replace(".", " ")}
    if exc is not None:
        fields["error_type"] = type(exc).__name__
    logger.warning(event, extra={"event_data": fields})


def safe_span(span):
    def context_without_state(context):
        if context is None:
            return None
        return trace.SpanContext(context.trace_id, context.span_id, context.is_remote, context.trace_flags, trace.TraceState())

    attributes = {
        key: value for key, value in span.attributes.items()
        if key in SPAN_ATTRIBUTES
    }
    events = []
    for event in span.events:
        if event.name == "exception":
            frames = re.findall(r'^\s*File "[^"\n]+", line \d+, in [\w<>]+', event.attributes.get("exception.stacktrace", ""), re.MULTILINE)
            events.append(Event("exception", {
                "exception.type": event.attributes.get("exception.type", "Exception"),
                "exception.message": "Operation failed; exception details withheld",
                "exception.stacktrace": "\n".join(frames[-20:]),
            }, event.timestamp))
        elif event.name == "retry.scheduled":
            events.append(event)
    name = span.name if span.kind != trace.SpanKind.SERVER or span.attributes.get("http.route") else "HTTP request"
    return ReadableSpan(
        name=name, context=context_without_state(span.context), parent=context_without_state(span.parent), resource=span.resource,
        attributes=attributes, events=events, kind=span.kind,
        status=trace.Status(span.status.status_code), start_time=span.start_time,
        end_time=span.end_time, instrumentation_scope=span.instrumentation_scope,
    )


class SafeExporter(SpanExporter):
    def __init__(self, delegate):
        self.delegate = delegate
        self.failure_reported = False

    def export(self, spans):
        try:
            result = self.delegate.export(tuple(safe_span(span) for span in spans))
            if result != SpanExportResult.SUCCESS and not self.failure_reported:
                diagnostic("telemetry.export_failed")
                self.failure_reported = True
            elif result == SpanExportResult.SUCCESS:
                self.failure_reported = False
            return result
        except Exception as exc:
            if not self.failure_reported:
                diagnostic("telemetry.export_failed", exc)
                self.failure_reported = True
            return SpanExportResult.FAILURE

    def shutdown(self):
        try:
            self.delegate.shutdown()
        except Exception as exc:
            diagnostic("telemetry.shutdown_failed", exc)


class Telemetry:
    def __init__(self, settings, exporter=None):
        self.settings = settings
        self.exporter = exporter
        self.started = False
        self.export_ready = False
        self.enabled = settings.otel_enabled and settings.otel_exporter != "none"
        attributes = {"service.name": settings.otel_service_name}
        if settings.google_cloud_project:
            attributes["gcp.project_id"] = settings.google_cloud_project
        self.provider = TracerProvider(resource=Resource(attributes), sampler=ALWAYS_ON, shutdown_on_exit=False) if self.enabled else trace.NoOpTracerProvider()
        self.tracer = self.provider.get_tracer("appstore-review-analyzer")

    def start(self):
        if self.started or not self.enabled:
            return
        self.started = True
        try:
            exporter = self.exporter
            if exporter is None and self.settings.otel_exporter == "console":
                exporter = ConsoleSpanExporter(out=sys.stdout, formatter=lambda span: json.dumps({
                    "severity": "DEBUG", "event": "trace.span", "message": span.name,
                    "span": json.loads(span.to_json(indent=None)),
                }) + "\n")
            elif exporter is None:
                import google.auth
                from google.auth.transport.requests import AuthorizedSession

                if not self.settings.google_cloud_project:
                    raise ValueError("GOOGLE_CLOUD_PROJECT is required for Google export")
                credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                session = AuthorizedSession(credentials, refresh_timeout=self.settings.otel_export_timeout_seconds, max_refresh_attempts=1)
                exporter = OTLPSpanExporter(
                    endpoint="https://telemetry.googleapis.com/v1/traces", session=session,
                    headers={"x-goog-user-project": self.settings.google_cloud_project},
                    timeout=self.settings.otel_export_timeout_seconds, meter_provider=NoOpMeterProvider(),
                )
            wrapped = SafeExporter(exporter)
            processor = SimpleSpanProcessor(wrapped) if self.exporter is not None else BatchSpanProcessor(
                wrapped, max_queue_size=256, max_export_batch_size=64,
                schedule_delay_millis=1000, export_timeout_millis=5000,
                meter_provider=NoOpMeterProvider(),
            )
            self.provider.add_span_processor(processor)
            self.export_ready = True
        except Exception as exc:
            diagnostic("telemetry.initialization_failed", exc)

    def shutdown(self):
        if not self.enabled:
            return
        try:
            if not self.provider.force_flush(timeout_millis=5000):
                diagnostic("telemetry.flush_timeout")
            self.provider.shutdown()
        except Exception as exc:
            diagnostic("telemetry.shutdown_failed", exc)
