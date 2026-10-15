from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.insights import ModelReport
from app.services.validator import build_report
from app.settings import Settings


def collected():
    return {
        "metadata": {"app_id": "123", "country": "us", "requested_count": 3, "available_count": 3, "actual_count": 3, "shortfall": 0, "seed": 42, "failed_pages": []},
        "reviews": [
            {"id": "n1", "title": "Broken", "text": "Does not open 😡", "rating": 5},
            {"id": "p1", "title": "Useful", "text": "I love it", "rating": 3},
            {"id": "n2", "title": "Broken", "text": "Cannot open", "rating": 1},
        ],
    }


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-only")
    classifier = Mock(return_value=[{"label": "negative", "score": .9}, {"label": "positive", "score": .8}, {"label": "negative", "score": .9}])
    factory = Mock(return_value=classifier)
    monkeypatch.setattr("app.services.analyzer.collect_reviews", lambda *args: collected())
    monkeypatch.setattr("app.services.analyzer.lookup_app_info", lambda app_id, country, **kwargs: {"app_id": app_id, "country": country, "name": "Example App"})
    report = ModelReport.model_validate({"issues": [{"category": "Startup failures", "action": "Investigate startup failures.", "priority": "high", "evidence_ids": ["n1", "n2"], "keywords": ["startup", "access"]}]})

    def insights(groups, prompt, usage, model, **kwargs):
        assert set(groups) == {"negative", "positive", "neutral"}
        assert groups["negative"][0]["text"] != collected()["reviews"][0]["text"]
        usage.update({"prompt_token_count": 100, "candidates_token_count": 20})
        return report

    monkeypatch.setattr("app.services.insights.request_insights", insights)
    with TestClient(create_app(Settings(analyze_requests_per_minute=10), factory)) as client:
        yield client, classifier, factory


def test_complete_analysis_reuses_model_and_returns_grounded_report(api):
    client, classifier, factory = api
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["metrics"]["ratings"]["average"] == 3
    assert body["metrics"]["sentiment"]["counts"]["negative"] == 2
    assert body["metrics"]["issues"]["coverage"] == 1
    assert body["insights"]["issues"][0]["evidence_quotes"][0]["quote"] == "Does not open 😡"
    assert body["reviews"][0]["rating"] == 5
    assert body["reviews"][0]["sentiment"]["label"] == "negative"
    assert body["metadata"]["gemini_usage"]["prompt_token_count"] == 100
    assert body["metadata"]["peak_memory_mb"] > 0
    assert "sentiment" in body["metadata"]["processing_time"]
    assert client.post("/api/analyze", json={"app_id": "123", "review_count": 3}).status_code == 200
    factory.assert_called_once()
    assert classifier.call_count == 2


def test_analysis_normalizes_once_and_computes_metrics_once(api, monkeypatch):
    from app.services.metrics import calculate_metrics
    from app.services.preprocessor import preprocess_text

    client, _, _ = api
    normalize = Mock(wraps=preprocess_text)
    metrics = Mock(wraps=calculate_metrics)
    monkeypatch.setattr("app.services.preprocessor.preprocess_text", normalize)
    monkeypatch.setattr("app.services.analyzer.calculate_metrics", metrics)
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 200
    assert normalize.call_count == 6
    metrics.assert_called_once()
    assert all("truncated_fields" not in review for review in response.json()["reviews"])


def test_sentiment_value_error_is_not_misreported_as_model_evidence(api):
    client, classifier, _ = api
    classifier.side_effect = ValueError("Invalid inference input")
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 502
    assert "evidence validation" not in response.json()["detail"]


def test_dashboard_assets_health_and_openapi(api):
    client, _, _ = api
    assert client.get("/").status_code == 200
    assert 'max="100"' in client.get("/").text
    assert client.get("/static/app.mjs").status_code == 200
    assert client.get("/api/health").json()["sentiment_ready"] is True
    assert client.get("/docs").status_code == 200
    assert client.get("/.env").status_code == 404
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/api/analyze", "/api/reviews/collect", "/api/health"}


def test_validation_and_collection(api):
    client, _, _ = api
    for body in [{"app_id": "abc"}, {"app_id": "0"}, {"app_id": "123", "country": "xx"}, {"app_id": "123", "review_count": True}, {"app_id": "123", "review_count": 101}]:
        assert client.post("/api/reviews/collect", json=body).status_code == 422
    assert len(client.post("/api/reviews/collect", json={"app_id": "123", "review_count": 3}).json()["reviews"]) == 3


def test_metrics_endpoint_is_removed(api):
    client, _, _ = api
    assert client.post("/api/metrics", json={"reviews": []}).status_code == 404


def test_export_endpoint_is_removed(api):
    client, _, _ = api
    assert client.post("/api/reviews/export", json={"reviews": [], "format": "csv"}).status_code == 404
    assert 'id="export-csv"' not in client.get("/").text


def test_untrusted_model_evidence_is_rejected_without_a_report(api, monkeypatch):
    client, _, _ = api
    invalid = ModelReport.model_validate({"issues": [{"category": "Fake", "action": "Fix a fabricated problem.", "priority": "high", "evidence_ids": ["p1"]}]})
    generate = Mock(return_value=invalid)
    monkeypatch.setattr("app.services.insights.request_insights", generate)
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 502
    assert "evidence validation" in response.json()["detail"]
    assert "reviews" not in response.json()
    assert generate.call_count == 2


@pytest.mark.parametrize("evidence", [["p1"], ["unknown"], ["n1", "n1"]])
def test_invalid_evidence_is_regenerated_and_validated(api, monkeypatch, evidence, caplog):
    client, classifier, _ = api
    invalid = ModelReport.model_validate({"issues": [{"category": "Startup", "action": "Investigate startup failures.", "priority": "high", "evidence_ids": evidence}]})
    valid = invalid.model_copy(deep=True)
    valid.issues[0].evidence_ids = ["n1", "n2"]
    generate = Mock(side_effect=[invalid, valid])
    monkeypatch.setattr("app.services.insights.request_insights", generate)
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 200, response.text
    assert response.json()["insights"]["issues"][0]["evidence_ids"] == ["n1", "n2"]
    assert generate.call_count == 2
    assert classifier.call_count == 1
    prompt = generate.call_args.args[1]
    assert 'only IDs from this list: ["n1", "n2"]' in prompt
    event = next(record.event_data for record in caplog.records if getattr(record, "event_data", {}).get("event") == "request.completed")
    assert event["insight_repair_count"] == 1
    assert event["insight_validation_reason"] in {"duplicate_evidence_ids", "nonnegative_or_unknown_evidence_ids"}
    assert "error" not in event


def test_repair_respects_analysis_deadline(api, monkeypatch):
    client, _, _ = api
    invalid = ModelReport.model_validate({"issues": [{"category": "Startup", "action": "Investigate startup failures.", "priority": "high", "evidence_ids": ["unknown"]}]})
    generate = Mock(return_value=invalid)
    monkeypatch.setattr("app.services.insights.request_insights", generate)
    def expired(*args):
        client.app.state.analyzer.settings.analysis_timeout_seconds = 0
        return build_report(*args)

    monkeypatch.setattr("app.services.insights.build_report", expired)
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 504
    assert generate.call_count == 1


def test_external_deadline_exhaustion_returns_stage_specific_timeout(api, monkeypatch, caplog):
    from app.services.execution import DeadlineExceeded

    client, _, _ = api
    monkeypatch.setattr("app.services.insights.request_insights", Mock(side_effect=DeadlineExceeded("Budget exhausted")))
    response = client.post("/api/analyze", json={"app_id": "123", "review_count": 3})
    assert response.status_code == 504
    assert response.json() == {"detail": "Analysis exceeded its processing deadline"}
    event = next(record.event_data for record in caplog.records if getattr(record, "event_data", {}).get("event") == "request.failed")
    assert event["error_stage"] == "gemini"
    assert event["error_type"] == "ANALYSIS_TIMEOUT"


def test_missing_credentials_and_rate_limit(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    app = create_app(Settings(analyze_requests_per_minute=1), lambda _: Mock())
    with TestClient(app) as client:
        assert client.post("/api/analyze", json={"app_id": "123"}).status_code == 503
        monkeypatch.setenv("GEMINI_API_KEY", "test-only")
        app.state.analyzer.analyze = lambda _: {"app": {}, "collection": {}, "metrics": {}, "insights": {}, "reviews": [], "metadata": {}}
        assert client.post("/api/analyze", json={"app_id": "123"}).status_code == 200
        response = client.post("/api/analyze", json={"app_id": "123"})
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
