from unittest.mock import Mock

import httpx
import pytest
from google import genai

from app.services.app_info import lookup_app_info
from app.services.execution import AnalysisError, AnalysisRun, DeadlineExceeded
from app.services.insights import request_insights
from app.settings import Settings


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("app.services.execution.time.monotonic", clock)
    monkeypatch.setattr("app.services.insights.time.sleep", clock.advance)
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    return clock


def gemini_adapter(monkeypatch, handler):
    client_type = genai.Client

    def make_client(**kwargs):
        options = kwargs["http_options"]
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(handler), event_hooks=options.httpx_client.event_hooks)
        return client_type(**kwargs)

    monkeypatch.setattr("app.services.insights.genai.Client", make_client)


def model_response():
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": '{"issues": []}'}]}}], "usageMetadata": {"totalTokenCount": 10}})


GROUPS = {"negative": [{"id": "n1", "title": "Broken", "text": "Does not open", "rating": 1}], "positive": [], "neutral": []}


def test_retry_recalculates_transport_and_server_timeout(monkeypatch, clock):
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.extensions["timeout"]["read"] == 5
            assert request.headers["X-Server-Timeout"] == "5"
            clock.advance(3)
            return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable"}})
        assert request.extensions["timeout"]["read"] == 1
        assert request.headers["X-Server-Timeout"] == "1"
        clock.advance(.25)
        return model_response()

    gemini_adapter(monkeypatch, respond)
    usage = {}
    assert request_insights(GROUPS, "Prompt", usage, deadline=105).issues == []
    assert len(requests) == 2
    assert clock.now == 104.25
    assert usage["total_token_count"] == 10


@pytest.mark.parametrize("spent", [4.75, 5])
def test_exhausted_retry_budget_never_starts_another_request(monkeypatch, clock, spent):
    requests = []

    def respond(request):
        requests.append(request)
        clock.advance(spent)
        return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable"}})

    gemini_adapter(monkeypatch, respond)
    with pytest.raises(DeadlineExceeded):
        request_insights(GROUPS, "Prompt", deadline=105)
    assert len(requests) == 1
    assert clock.now == 105


def test_expired_budget_makes_no_gemini_request(monkeypatch, clock):
    handler = Mock(side_effect=AssertionError("Unexpected network request"))
    gemini_adapter(monkeypatch, handler)
    with pytest.raises(DeadlineExceeded):
        request_insights(GROUPS, "Prompt", deadline=100)
    handler.assert_not_called()


@pytest.mark.parametrize("status,expected", [(400, 1), (429, 3), (503, 3)])
def test_provider_retries_are_bounded_and_skip_permanent_errors(monkeypatch, clock, status, expected):
    from google.genai import errors

    handler = Mock(side_effect=lambda request: httpx.Response(status, json={"error": {"code": status, "message": "Provider failure"}}))
    gemini_adapter(monkeypatch, handler)
    with pytest.raises(errors.APIError):
        request_insights(GROUPS, "Prompt", deadline=130)
    assert handler.call_count == expected
    assert clock.now == (103 if expected == 3 else 100)


def test_last_attempt_timeout_at_deadline_is_budget_exhaustion(monkeypatch, clock):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 3:
            clock.advance(2)
            raise httpx.ReadTimeout("Request timed out", request=request)
        return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable"}})

    gemini_adapter(monkeypatch, respond)
    with pytest.raises(DeadlineExceeded):
        request_insights(GROUPS, "Prompt", deadline=105)
    assert len(calls) == 3


def test_late_success_is_rejected_but_actual_usage_is_preserved(monkeypatch, clock):
    def respond(request):
        clock.advance(6)
        return model_response()

    gemini_adapter(monkeypatch, respond)
    usage = {}
    with pytest.raises(DeadlineExceeded):
        request_insights(GROUPS, "Prompt", usage, deadline=105)
    assert usage["total_token_count"] == 10


def test_run_checks_completion_and_retains_stage(clock):
    run = AnalysisRun(Settings(analysis_timeout_seconds=30))
    with pytest.raises(AnalysisError) as error:
        with run.stage("sentiment", "sentiment.classify"):
            clock.advance(31)
    assert error.value.status == 504
    assert error.value.stage == "sentiment"
    assert run.durations["sentiment"] == 31


def test_lookup_uses_remaining_budget_and_rejects_late_result(monkeypatch, clock):
    client_type = httpx.Client

    def respond(request):
        assert request.extensions["timeout"]["read"] == 2
        clock.advance(3)
        return httpx.Response(200, json={"results": []})

    def make_client(**kwargs):
        return client_type(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr("app.services.app_info.httpx.Client", make_client)
    with pytest.raises(DeadlineExceeded):
        lookup_app_info("123", "us", deadline=102)


def test_expired_lookup_budget_starts_no_client(monkeypatch, clock):
    client = Mock()
    monkeypatch.setattr("app.services.app_info.httpx.Client", client)
    with pytest.raises(DeadlineExceeded):
        lookup_app_info("123", "us", deadline=100)
    client.assert_not_called()
