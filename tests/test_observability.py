import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from google import genai
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app
from app.observability import JsonFormatter, RequestObservationMiddleware, configure_logging
from app.services.collector import collect_reviews
from app.settings import Settings
from app.telemetry import Telemetry


SECRET = "NEVER_EXPORT_REVIEW_OR_KEY_7AC"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"
PREDICTIONS = [{"label": "negative", "score": .9}, {"label": "positive", "score": .8}]


def model_response(evidence="n1"):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps({"issues": [{
        "category": "Startup", "action": "Investigate startup failures.", "priority": "high", "keywords": [], "evidence": [{"review_id": evidence, "quote": SECRET}],
    }]})}]}, "finishReason": "STOP"}], "usageMetadata": {
        "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5,
        "totalTokenCount": 125, "cachedContentTokenCount": 30,
    }}


@pytest.fixture
def instrumented(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    client_type = genai.Client

    def collect(client, app_id, country, count, seed, deadline, **kwargs):
        entries = [{"id": review_id, "attributes": {"title": SECRET, "review": SECRET, "rating": rating}} for review_id, rating in (("n1", 5), ("p1", 1))]
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": entries}))) as fixture_client:
            return collect_reviews(fixture_client, app_id, country, count, seed, deadline)

    monkeypatch.setattr("app.services.analyzer.collect_reviews", collect)
    monkeypatch.setattr("app.services.analyzer.lookup_app_info", lambda app_id, country, **kwargs: {"app_id": app_id, "country": country})

    def make(handler=None, exporter=None, **settings):
        def make_client(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(transport=httpx.MockTransport(handler or (lambda request: httpx.Response(200, json=model_response()))), event_hooks=kwargs["http_options"].httpx_client.event_hooks)
            return client_type(**kwargs)

        monkeypatch.setattr("app.services.insights.genai.Client", make_client)
        classifier = Mock(return_value=PREDICTIONS)
        exporter = exporter if exporter is not None else InMemorySpanExporter()
        app = create_app(Settings(analyze_requests_per_minute=10, google_cloud_project="test-project", **settings), lambda _: classifier, exporter)
        return app, exporter, classifier

    return make


def request_logs(capsys):
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return [record for record in records if record["event"].startswith("request.")]


def test_complete_trace_hierarchy_usage_and_log_correlation(instrumented, capsys):
    attempts = []

    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, json={"error": {"code": 503, "message": SECRET, "status": "UNAVAILABLE"}})
        return httpx.Response(200, json=model_response())

    app, exporter, _ = instrumented(respond)
    with TestClient(app) as client:
        response = client.post(f"/api/analyze?token={SECRET}", json={"app_id": "123", "review_count": 2}, headers={
            "x-request-id": "request-1", "traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01",
            "tracestate": f"vendor={SECRET}",
        })
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"app", "collection", "metrics", "insights", "reviews", "metadata"}
    assert response.headers["x-request-id"] == "request-1"
    assert response.json()["reviews"][0]["text"] == SECRET
    spans = exporter.get_finished_spans()
    roots = [span for span in spans if span.kind == trace.SpanKind.SERVER]
    assert len(roots) == 1
    root = roots[0]
    assert root.name == "POST /api/analyze"
    assert root.parent.span_id == int(PARENT_ID, 16)
    assert all(span.context.trace_id == int(TRACE_ID, 16) for span in spans)
    by_name = {span.name: span for span in spans}
    assert {"reviews.collect", "appstore.request", "reviews.preprocess", "sentiment.classify", "metrics.calculate", "insights.generate", "gemini.request", "insights.validate", "report.assemble"} <= by_name.keys()
    assert by_name["reviews.collect"].parent.span_id == root.context.span_id
    assert by_name["appstore.request"].parent.span_id == by_name["reviews.collect"].context.span_id
    assert by_name["gemini.request"].parent.span_id == by_name["insights.generate"].context.span_id
    assert by_name["reviews.collect"].attributes["app.reviews.collected_count"] == 2
    assert by_name["sentiment.classify"].attributes["app.batch_size"] == 1
    assert by_name["insights.validate"].attributes["app.issues.count"] == 1
    gemini = by_name["gemini.request"]
    assert gemini.attributes["app.retry_count"] == 1
    assert gemini.attributes["gen_ai.usage.input_tokens"] == 100
    assert gemini.attributes["gen_ai.usage.output_tokens"] == 20
    assert gemini.attributes["app.gen_ai.thinking_tokens"] == 5
    assert gemini.attributes["app.gen_ai.total_tokens"] == 125
    assert gemini.attributes["gen_ai.usage.cache_read.input_tokens"] == 30
    assert len(by_name["insights.generate"].attributes["app.prompt.version"]) == 64
    assert all(span.end_time >= span.start_time for span in spans)
    logs = request_logs(capsys)
    assert len(logs) == 1
    log = logs[0]
    assert log["trace_id"] == TRACE_ID
    assert log["logging.googleapis.com/trace"] == f"projects/test-project/traces/{TRACE_ID}"
    assert log["request_id"] == "request-1"
    assert log["gemini_retry_count"] == 1
    for field in ("analysis_duration_ms", "collection_duration_ms", "preprocessing_duration_ms", "sentiment_duration_ms", "metrics_duration_ms", "gemini_duration_ms", "validation_duration_ms"):
        assert log[field] >= 0
    serialized = json.dumps(logs) + "".join(span.to_json() for span in spans)
    assert SECRET not in serialized
    assert "System instructions" not in serialized
    assert "http.url" not in root.attributes


def test_validation_failure_is_attributed_and_redacted(instrumented, capsys):
    app, exporter, _ = instrumented(lambda request: httpx.Response(200, json=model_response("unknown")))
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    failed = next(span for span in exporter.get_finished_spans() if span.name == "insights.validate")
    assert failed.status.status_code == trace.StatusCode.ERROR
    assert failed.attributes["app.validation.failures"] == 1
    assert any(event.name == "exception" for event in failed.events)
    logs = request_logs(capsys)
    assert len(logs) == 1
    assert logs[0]["event"] == "request.failed"
    assert logs[0]["error_stage"] == "validation"
    assert SECRET not in json.dumps(logs)


def test_sentiment_exception_preserves_recovery_and_request_isolation(instrumented, capsys):
    app, exporter, classifier = instrumented()
    classifier.side_effect = [RuntimeError(SECRET), PREDICTIONS]
    with TestClient(app) as client:
        first = client.post("/api/analyze", json={"app_id": "123", "review_count": 2}, headers={"x-request-id": "failure"})
        second = client.post("/api/analyze", json={"app_id": "456", "review_count": 2}, headers={"x-request-id": "recovery"})
    assert first.status_code == 502
    assert second.status_code == 200
    failed = next(span for span in exporter.get_finished_spans() if span.name == "sentiment.classify" and span.status.status_code == trace.StatusCode.ERROR)
    assert failed.events[0].attributes["exception.type"] == "RuntimeError"
    logs = request_logs(capsys)
    assert [log["request_id"] for log in logs] == ["failure", "recovery"]
    assert logs[0]["error_stage"] == "sentiment"
    assert logs[1]["app_id"] == "456"
    assert "error" not in logs[1]
    assert SECRET not in json.dumps(logs) + "".join(span.to_json() for span in exporter.get_finished_spans())


@pytest.mark.parametrize("raises", [True, False])
def test_export_failure_never_breaks_analysis(instrumented, capsys, raises):
    exporter = Mock()
    if raises:
        exporter.export.side_effect = RuntimeError(SECRET)
    else:
        exporter.export.return_value = SpanExportResult.FAILURE
    app, _, _ = instrumented(exporter=exporter)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert len([record for record in records if record["event"] == "telemetry.export_failed"]) == 1
    assert SECRET not in output
    exporter.shutdown.assert_called_once()


def test_health_excluded_and_concurrent_collection_contexts(instrumented, capsys):
    app, exporter, _ = instrumented()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert not exporter.get_finished_spans()

        def collect(app_id):
            return client.post("/api/reviews/collect", json={"app_id": app_id, "review_count": 2}, headers={"x-request-id": f"request-{app_id}"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(collect, ("123", "456")))
    assert all(response.status_code in {200, 409} for response in responses)
    logs = request_logs(capsys)
    assert len(logs) == 2
    assert {log["request_id"]: log["app_id"] for log in logs} == {"request-123": "123", "request-456": "456"}
    assert len({log["trace_id"] for log in logs}) == 2


def test_tracing_disabled_keeps_json_logs_and_api_functionality(instrumented, capsys):
    app, exporter, _ = instrumented(otel_enabled=False)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    assert not exporter.get_finished_spans()
    log = request_logs(capsys)[0]
    assert "trace_id" not in log
    assert log["request_id"] == response.headers["x-request-id"]


def test_json_formatter_never_renders_raw_dependency_exception():
    record = logging.LogRecord("httpx", logging.ERROR, __file__, 1, SECRET, (), (RuntimeError, RuntimeError(SECRET), None))
    output = JsonFormatter().format(record)
    assert SECRET not in output
    assert json.loads(output)["severity"] == "ERROR"
    assert "\n" not in output


def test_console_request_is_one_block_without_raw_review_content(instrumented, capsys):
    app, _, _ = instrumented(log_format="console")
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    output = capsys.readouterr().out
    assert output.count("POST /api/analyze: success") == 1
    assert "Reviews  2/2" in output
    assert "sentiment.classify" in output
    assert "Tokens" in output
    assert SECRET not in output


@pytest.mark.parametrize("level,expected", [("INFO", False), ("DEBUG", True)])
def test_console_span_export_respects_log_level(capsys, level, expected):
    settings = Settings(log_format="json", log_level=level)
    configure_logging(settings)
    telemetry = Telemetry(settings)
    telemetry.start()
    with telemetry.tracer.start_as_current_span("diagnostic.operation"):
        pass
    telemetry.shutdown()
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert bool([record for record in records if record["event"] == "trace.span"]) is expected


def test_cloud_run_always_uses_json_even_with_console_override(monkeypatch, capsys):
    monkeypatch.setenv("K_SERVICE", "review-analyzer")
    configure_logging(Settings(log_format="console"))
    logging.getLogger("review_analyzer").info("startup", extra={"event_data": {"event": "application.ready", "message": "Ready"}})
    assert json.loads(capsys.readouterr().out)["event"] == "application.ready"


def test_repair_usage_is_aggregated_in_logs_but_individual_in_spans(instrumented, capsys):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=model_response("unknown" if len(calls) == 1 else "n1"))

    app, exporter, _ = instrumented(respond)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    assert response.json()["metadata"]["gemini_usage"]["total_token_count"] == 250
    summary = request_logs(capsys)[0]
    assert summary["gemini_usage"]["total_token_count"] == 250
    spans = [span for span in exporter.get_finished_spans() if span.name == "gemini.request"]
    assert len(spans) == 2
    assert all(span.attributes["app.gen_ai.total_tokens"] == 125 for span in spans)


def test_missing_repair_usage_keeps_known_counts_and_marks_incomplete(instrumented, capsys):
    calls = []

    def respond(request):
        calls.append(request)
        payload = model_response("unknown" if len(calls) == 1 else "n1")
        if len(calls) == 2:
            del payload["usageMetadata"]
        return httpx.Response(200, json=payload)

    app, _, _ = instrumented(respond)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    summary = request_logs(capsys)[0]
    assert summary["gemini_usage_available"] is False
    assert summary["gemini_usage"]["total_token_count"] == 125
    assert summary["gemini_usage_missing_count"] == 1


def test_initialization_is_idempotent_and_shutdown_flushes():
    exporter = InMemorySpanExporter()
    telemetry = Telemetry(Settings(), exporter)
    telemetry.start()
    telemetry.start()
    with telemetry.tracer.start_as_current_span("test.operation"):
        pass
    telemetry.shutdown()
    assert len(exporter.get_finished_spans()) == 1


def test_google_initialization_failure_is_nonfatal(monkeypatch, instrumented, capsys):
    monkeypatch.setattr("google.auth.default", Mock(side_effect=RuntimeError(SECRET)))
    app, _, _ = instrumented(otel_exporter="google")
    app.state.telemetry.exporter = None
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    output = capsys.readouterr().out
    assert '"event": "telemetry.initialization_failed"' in output
    assert SECRET not in output


def test_google_adc_export_configuration_and_batch_shutdown(monkeypatch):
    exporter = InMemorySpanExporter()
    adc = Mock(return_value=(Mock(), "test-project"))
    session = Mock()
    constructor = Mock(return_value=exporter)
    monkeypatch.setattr("google.auth.default", adc)
    monkeypatch.setattr("google.auth.transport.requests.AuthorizedSession", session)
    monkeypatch.setattr("app.telemetry.OTLPSpanExporter", constructor)
    telemetry = Telemetry(Settings(otel_exporter="google", google_cloud_project="test-project"))
    telemetry.start()
    with telemetry.tracer.start_as_current_span("completed.server.span"):
        pass
    telemetry.shutdown()
    assert telemetry.export_ready is True
    assert len(exporter.get_finished_spans()) == 1
    assert constructor.call_args.kwargs["endpoint"] == "https://telemetry.googleapis.com/v1/traces"
    assert constructor.call_args.kwargs["headers"] == {"x-goog-user-project": "test-project"}
    assert constructor.call_args.kwargs["session"] is session.return_value
    adc.assert_called_once_with(scopes=["https://www.googleapis.com/auth/cloud-platform"])


def test_absent_provider_usage_is_explicit(instrumented, capsys):
    payload = model_response()
    del payload["usageMetadata"]
    app, exporter, _ = instrumented(lambda request: httpx.Response(200, json=payload))
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200
    assert response.json()["metadata"]["gemini_usage"] == {}
    gemini = next(span for span in exporter.get_finished_spans() if span.name == "gemini.request")
    assert gemini.attributes["app.gen_ai.usage_available"] is False
    assert "gen_ai.usage.input_tokens" not in gemini.attributes
    assert request_logs(capsys)[0]["gemini_usage_available"] is False


def test_gemini_timeout_is_traced_and_does_not_leak_provider_details(instrumented, capsys):
    def timeout(request):
        raise httpx.ReadTimeout(SECRET, request=request)

    app, exporter, _ = instrumented(timeout)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 502
    gemini = next(span for span in exporter.get_finished_spans() if span.name == "gemini.request")
    assert gemini.status.status_code == trace.StatusCode.ERROR
    assert gemini.attributes["app.retry_count"] >= 0
    logs = request_logs(capsys)
    assert logs[0]["error_stage"] == "gemini"
    assert SECRET not in json.dumps(logs) + gemini.to_json()


def test_invalid_apple_response_is_a_collection_failure(monkeypatch, instrumented, capsys):
    app, exporter, _ = instrumented()
    monkeypatch.setattr("app.services.collector.time.sleep", lambda _: None)

    def bad_collection(client, app_id, country, count, seed, deadline, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"error": SECRET}))) as fixture_client:
            return collect_reviews(fixture_client, app_id, country, count, seed, deadline)

    monkeypatch.setattr("app.services.analyzer.collect_reviews", bad_collection)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 502
    pages = [span for span in exporter.get_finished_spans() if span.name == "appstore.request"]
    assert len(pages) >= 1
    assert all(span.status.status_code == trace.StatusCode.ERROR for span in pages)
    assert all(span.attributes["app.retry_count"] == 2 for span in pages)
    logs = request_logs(capsys)
    assert len(logs) == 1
    assert logs[0]["error_stage"] == "reviews.collect"
    assert logs[0]["collection_retry_count"] == 2 * len(pages)
    assert SECRET not in json.dumps(logs) + "".join(span.to_json() for span in pages)


def test_unhandled_exception_is_safely_exported(instrumented, capsys):
    app, exporter, _ = instrumented()
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.analyzer.analyze = Mock(side_effect=RuntimeError(SECRET))
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 500
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.kind == trace.SpanKind.SERVER)
    assert root.status.status_code == trace.StatusCode.ERROR
    assert any(event.name == "exception" for event in root.events)
    logs = request_logs(capsys)
    assert len(logs) == 1
    assert SECRET not in json.dumps(logs) + root.to_json()


def test_batch_shutdown_includes_completed_http_server_span(monkeypatch, instrumented):
    app, exporter, _ = instrumented()
    app.state.telemetry.exporter = None
    monkeypatch.setattr("app.telemetry.ConsoleSpanExporter", lambda **kwargs: exporter)
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
        assert response.status_code == 200
    assert any(span.name == "POST /api/analyze" and span.end_time is not None for span in exporter.get_finished_spans())


def test_instrumentation_initialization_failure_is_nonfatal(monkeypatch, instrumented):
    monkeypatch.setattr("app.main.FastAPIInstrumentor.instrument_app", Mock(side_effect=RuntimeError(SECRET)))
    app, _, _ = instrumented()
    with TestClient(app) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 2})
    assert response.status_code == 200


@pytest.mark.parametrize("exception_type", [asyncio.CancelledError, RuntimeError])
def test_interrupted_or_failed_request_never_logs_success_after_headers(capsys, exception_type):
    settings = Settings(otel_enabled=False)
    configure_logging(settings)

    async def cancelled(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise exception_type()

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        pass

    middleware = RequestObservationMiddleware(cancelled, Telemetry(settings), settings)
    with pytest.raises(exception_type):
        asyncio.run(middleware({"type": "http", "method": "POST", "path": "/api/analyze", "headers": []}, receive, send))
    logs = request_logs(capsys)
    assert len(logs) == 1
    if exception_type is asyncio.CancelledError:
        assert logs[0]["event"] == "request.interrupted"
        assert logs[0]["outcome"] == "interrupted"
        assert logs[0]["processing_may_continue"] is True
    else:
        assert logs[0]["event"] == "request.failed"
        assert logs[0]["outcome"] == "error"
