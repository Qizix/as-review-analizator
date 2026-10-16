import json
import subprocess
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from google import genai
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.main import create_app
from app.schemas import AnalyzeRequest
from app.services.collector import collect_reviews
from app.services.execution import AnalysisRun
from app.services.interpretation import IssueAnalysis, SentimentPrediction, UnifiedAnalysis, interpret_reviews, validate_analysis
from app.services.metrics import calculate_metrics
from app.services.review_batch import ReviewBatch
from app.services.validator import InsightValidationError
from app.settings import Settings


def source():
    return {"metadata": {"app_id": "123", "country": "us", "requested_count": 3, "actual_count": 3, "shortfall": 0, "failed_pages": []}, "reviews": [
        {"id": "n", "title": "Broken", "text": "Crashes 😡\nafter updates", "rating": 5},
        {"id": "p", "title": "Love it", "text": "Love it but videos freeze", "rating": 1},
        {"id": "u", "title": "Okay", "text": "Works, but videos freeze", "rating": 3},
    ]}


def unified():
    return UnifiedAnalysis.model_validate({"sentiments": [
        {"review_id": "u", "sentiment": 0}, {"review_id": "n", "sentiment": -1}, {"review_id": "p", "sentiment": 1},
    ], "issues": [{"category": "  Playback   instability ", "action": "Investigate playback failures.", "priority": "high", "keywords": ["crashes", "videos freeze"], "evidence": [
        {"review_id": "n", "quote": "Crashes 😡"}, {"review_id": "p", "quote": "videos freeze"},
        {"review_id": "u", "quote": "videos freeze"}, {"review_id": "n", "quote": "after updates"},
    ]}]})


def validate(result=None, data=None):
    batch = ReviewBatch(data or source())
    report = validate_analysis(batch, batch.semantic_reviews, result or unified(), "prompt", "model")
    return batch, report


def test_unified_binds_by_id_preserves_sources_and_deduplicates_issue_support():
    data = source()
    original = deepcopy(data)
    batch, report = validate(data=data)
    reviews = batch.source["reviews"]
    assert [item["sentiment"] for item in reviews] == [{"label": "negative"}, {"label": "positive"}, {"label": "neutral"}]
    assert data == original
    assert reviews[0]["text"] == original["reviews"][0]["text"]
    issue = report["insights"]["high"]["repeated"][0]
    assert issue["review_count"] == 3
    assert issue["negative_review_count"] == 1
    assert issue["negative_review_fraction"] == 1
    assert len(issue["evidence_quotes"]) == 3
    metrics = calculate_metrics(batch.source, report)
    assert metrics["ratings"]["average"] == 3
    assert metrics["issues"]["items"][0]["frequency"] == 3
    assert metrics["issues"]["items"][0]["negative_review_count"] == 1
    assert metrics["issues"]["coverage"] == 1


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_incomplete_or_ambiguous_sentiment_predictions_fail(mutation):
    result = unified()
    if mutation == "missing":
        result.sentiments.pop()
    elif mutation == "duplicate":
        result.sentiments.append(result.sentiments[0])
    else:
        result.sentiments[0].review_id = "unknown"
    with pytest.raises(InsightValidationError, match="sentiment_ids"):
        validate(result)


@pytest.mark.parametrize("label", [True, False, "1", 1.0, -2, 2, None])
def test_sentiment_labels_are_strict_integers(label):
    with pytest.raises(ValueError):
        SentimentPrediction(review_id="n", sentiment=label)


@pytest.mark.parametrize("quote", ["fabricated", "Crashes after updates", " "])
def test_quotes_must_match_exact_visible_source_text(quote):
    result = unified()
    result.issues[0].evidence[0].quote = quote
    with pytest.raises(InsightValidationError, match="evidence_quote"):
        validate(result)


def test_cropped_gap_and_invisible_middle_cannot_supply_quotations():
    data = source()
    data["reviews"][0]["text"] = "a" * 3000 + "HIDDEN" + "z" * 1000
    for quote in ("HIDDEN", "az"):
        result = unified()
        result.issues[0].evidence = [result.issues[0].evidence[0]]
        result.issues[0].evidence[0].quote = quote
        with pytest.raises(InsightValidationError, match="evidence_quote"):
            validate(result, data)
    batch = ReviewBatch(data)
    assert batch.semantic_reviews[0]["text"] == "a" * 3000
    assert batch.semantic_reviews[0]["text_tail"] == "z" * 1000
    assert batch.source["reviews"][0]["text"] == data["reviews"][0]["text"]


def test_supported_issues_are_valid_without_negative_reviews():
    result = unified()
    for item in result.sentiments:
        item.sentiment = 1
    batch, report = validate(result)
    metrics = calculate_metrics(batch.source, report)
    issue = metrics["issues"]["items"][0]
    assert issue["frequency"] == 3
    assert issue["negative_review_count"] == 0
    assert issue["negative_review_fraction"] is None
    assert metrics["issues"]["coverage"] is None
    assert report["summary"]["categorized_negative_reviews"] == 0


def test_empty_issues_are_valid_without_fabricated_complaints():
    result = unified()
    result.issues = []
    batch, report = validate(result)
    assert report["summary"]["issue_count"] == 0
    assert calculate_metrics(batch.source, report)["issues"]["coverage"] == 0


@pytest.fixture
def fast_api(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-never-export")
    client_type = genai.Client
    requests = []
    response_data = unified().model_dump()
    responses = []
    finish = {"finishReason": "STOP"}

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": json.dumps(responses.pop(0) if responses else response_data)}]}, **finish}], "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 30, "totalTokenCount": 130}})

    def make_client(**kwargs):
        options = kwargs["http_options"]
        options.httpx_client = httpx.Client(transport=httpx.MockTransport(respond), event_hooks=options.httpx_client.event_hooks)
        return client_type(**kwargs)

    monkeypatch.setattr("app.services.insights.genai.Client", make_client)
    collect = Mock(return_value=source())
    monkeypatch.setattr("app.services.analyzer.collect_reviews", collect)
    monkeypatch.setattr("app.services.analyzer.lookup_app_info", lambda *args, **kwargs: {"app_id": "123", "country": "us", "name": "Example"})
    factory = Mock(side_effect=AssertionError("Fast-only startup must not load RoBERTa"))
    exporter = InMemorySpanExporter()
    app = create_app(Settings(quality_enabled=False, analyze_requests_per_minute=10), factory, exporter)
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, requests=requests, response=response_data, responses=responses, finish=finish, collect=collect, exporter=exporter, factory=factory)


def test_fast_request_uses_one_real_sdk_call_no_ratings_and_no_cpu_inference(fast_api, caplog):
    api = fast_api
    response = api.client.post("/api/analyze", json={"app_id": "123", "review_count": 3, "mode": "fast"}, headers={"x-request-id": "fast-request"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"]["mode"] == "fast"
    assert body["metadata"]["models"]["sentiment"] == "gemini-3.5-flash-lite"
    assert body["metadata"]["models"]["sentiment_revision"] is None
    assert body["metadata"]["gemini_usage"]["total_token_count"] == 130
    assert "sentiment" not in body["metadata"]["processing_time"]
    assert len(api.requests) == 1
    payload = api.requests[0]
    reviews = json.loads(payload["contents"][0]["parts"][0]["text"])["reviews"]
    assert all(set(review) == {"id", "title", "text"} for review in reviews)
    assert reviews[0]["text"] == source()["reviews"][0]["text"]
    config = payload["generationConfig"]
    assert config["thinkingConfig"].get("thinkingLevel", config["thinkingConfig"].get("thinking_level")).lower() == "minimal"
    assert "tools" not in payload
    assert "responseJsonSchema" in config and "responseSchema" not in config
    schema = config["responseJsonSchema"]
    assert "sentiments" in schema["required"]
    assert "enum" not in schema["$defs"]["Evidence"]["properties"]["review_id"]
    api.factory.assert_not_called()
    assert api.collect.call_args.kwargs["max_pages"] == 10
    api.client.app.state.telemetry.provider.force_flush()
    spans = api.exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    assert "sentiment.classify" not in by_name
    assert by_name["gemini.request"].parent.span_id == by_name["analysis.unified"].context.span_id
    assert by_name["analysis.unified"].attributes["app.analysis.mode"] == "fast"
    event = next(record.event_data for record in caplog.records if getattr(record, "event_data", {}).get("event") == "request.completed")
    assert event["analysis_mode"] == "fast"
    assert event["request_id"] == "fast-request"
    serialized = json.dumps(event) + "".join(span.to_json() for span in spans)
    assert "Crashes" not in serialized and "test-secret-never-export" not in serialized


@pytest.mark.parametrize("invalid", ["sentiments", "quote", "schema"])
def test_invalid_fast_outputs_fail_after_bounded_repair_without_fallback(fast_api, invalid):
    api = fast_api
    if invalid == "sentiments":
        api.response["sentiments"].pop()
    elif invalid == "quote":
        api.response["issues"][0]["evidence"][0]["quote"] = "invented"
    else:
        api.response["unexpected"] = True
    response = api.client.post("/api/analyze", json={"app_id": "123", "mode": "fast"})
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    assert len(api.requests) == (1 if invalid == "schema" else 2)
    api.factory.assert_not_called()


@pytest.mark.parametrize("invalid", ["quote", "sentiments"])
def test_fast_repairs_validation_failure_with_complete_verified_report(fast_api, caplog, invalid):
    api = fast_api
    broken = deepcopy(api.response)
    if invalid == "quote":
        broken["issues"][0]["evidence"][0]["quote"] = "invented"
    else:
        broken["sentiments"].pop()
    api.responses.append(broken)
    response = api.client.post("/api/analyze", json={"app_id": "123", "review_count": 3, "mode": "fast"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(api.requests) == 2
    assert body["metadata"]["mode"] == "fast"
    assert body["metadata"]["gemini_usage"]["total_token_count"] == 260
    assert body["metrics"]["sentiment"]["counts"] == {"negative": 1, "positive": 1, "neutral": 1}
    assert "sentiment" not in body["metadata"]["processing_time"]
    for issue in body["insights"]["issues"]:
        for evidence in issue["evidence_quotes"]:
            review = next(item for item in body["reviews"] if item["id"] == evidence["review_id"])
            assert evidence["quote"] in review["title"] or evidence["quote"] in review["text"]
    prompt = api.requests[1]["systemInstruction"]["parts"][0]["text"]
    assert "Validation failed:" in prompt
    assert "including every sentiment prediction" in prompt
    assert api.requests[0]["contents"] == api.requests[1]["contents"]
    event = next(record.event_data for record in caplog.records if getattr(record, "event_data", {}).get("event") == "request.completed")
    assert event["insight_repair_count"] == 1
    api.client.app.state.telemetry.provider.force_flush()
    spans = api.exporter.get_finished_spans()
    repair = next(span for span in spans if span.name == "insights.repair")
    assert any(span.name == "gemini.request" and span.parent.span_id == repair.context.span_id for span in spans)
    api.factory.assert_not_called()


def test_fast_only_mode_availability_and_compatibility(fast_api):
    api = fast_api
    assert api.client.get("/api/health").json()["available_modes"] == ["fast"]
    page = api.client.get("/").text
    assert 'id="mode-input"' in page
    assert '<option value="quality"' not in page
    assert api.client.post("/api/analyze", json={"app_id": "123"}).status_code == 503
    assert api.client.post("/api/analyze", json={"app_id": "123", "mode": "invalid"}).status_code == 422
    assert not api.requests
    assert AnalyzeRequest(app_id="123").mode == "quality"


def test_fast_only_imports_and_startup_do_not_touch_local_ml():
    code = '''
import importlib.abc, sys
class BlockML(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'transformers', 'huggingface_hub'}:
            raise AssertionError('Local ML imported: ' + fullname)
sys.meta_path.insert(0, BlockML())
from app.main import create_app
from app.settings import Settings
from fastapi.testclient import TestClient
with TestClient(create_app(Settings(quality_enabled=False, otel_enabled=False))) as client:
    assert client.get('/api/health').json()['sentiment_ready'] is True
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("limit,expected", [(10, 90), (30, 100)])
def test_deeper_collection_fills_failed_page_gap_without_exceeding_target(monkeypatch, limit, expected):
    monkeypatch.setattr("app.services.collector.time.sleep", lambda _: None)
    offsets = []

    def respond(request):
        if "/api/" not in request.url.path:
            if "/rss/" in request.url.path:
                return httpx.Response(200, json={"feed": {}})
            return httpx.Response(404)
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        if offset == 50:
            return httpx.Response(429)
        entries = [{"id": str(i), "attributes": {"title": "Review", "review": "Text", "rating": 3}} for i in range(offset + 1, offset + 11)]
        return httpx.Response(200, json={"data": entries, "next": f"?offset={offset+10}"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123", count=100, max_pages=limit)
    assert len(result["reviews"]) == len({v["id"] for v in result["reviews"]}) == expected
    assert result["metadata"]["catalog_page_limit"] == limit
    assert result["metadata"]["shortfall"] == 100 - expected
    assert max(offsets) == (90 if limit == 10 else 100)


def test_quality_checks_at_most_thirty_catalog_pages(monkeypatch):
    monkeypatch.setattr("app.services.collector.time.sleep", lambda _: None)
    offsets = []

    def respond(request):
        if "/api/" not in request.url.path:
            return httpx.Response(404)
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json={"data": [{"id": "same", "attributes": {"title": "T", "review": "Text", "rating": 3}}], "next": f"?offset={offset+10}"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123", count=100, max_pages=30)
    assert len(offsets) == 30
    assert result["metadata"]["shortfall"] == 99
    assert result["metadata"]["duplicate_records"] == 29


def test_semantic_preprocessing_describes_original_excerpts_not_emoji_expansion():
    data = source()
    data["reviews"][0]["text"] = "😡" * 3000
    batch = ReviewBatch(data)
    assert batch.preprocessing["truncated_review_ids"] == ["n"]
    assert batch.semantic_preprocessing["truncated_review_ids"] == []
    assert batch.semantic_preprocessing["emoji"] == "preserved"
    assert batch.semantic_reviews[0]["text"] == data["reviews"][0]["text"]


def test_caller_supplied_groups_cannot_expand_source_evidence_eligibility():
    from app.services.insights import ModelReport
    from app.services.validator import build_report

    data = source()
    for review in data["reviews"]:
        review["sentiment"] = "positive"
    groups = {"positive": [{"id": "fake"}], "neutral": [], "negative": []}
    report = ModelReport.model_validate({"issues": [{"category": "Fake", "action": "Investigate.", "priority": "high", "evidence_ids": ["fake"]}]})
    with pytest.raises(InsightValidationError):
        build_report(data, groups, report, "prompt", allow_all_sentiments=True)


def test_evaluation_scores_use_independent_gold_labels():
    from experiments.evaluate_analysis import classification_scores

    gold = {"a": "negative", "b": "neutral", "c": "positive"}
    assert classification_scores(gold, gold) == {"macro_f1": 1, "negative_recall": 1}
    wrong = {"a": "positive", "b": "neutral", "c": "positive"}
    scores = classification_scores(wrong, gold)
    assert scores["macro_f1"] == pytest.approx((0 + 1 + 2 / 3) / 3)
    assert scores["negative_recall"] == 0
    with pytest.raises(ValueError):
        classification_scores({"a": "negative"}, gold)


@pytest.mark.parametrize("mode", ["fast", "quality"])
def test_full_hundred_review_request_for_each_mode(fast_api, mode):
    api = fast_api
    data = source()
    data["reviews"] = [{"id": str(i), "title": "Feedback", "text": "Useful but videos freeze", "rating": 5 if i % 2 else 1} for i in range(100)]
    data["metadata"].update(requested_count=100, actual_count=100)
    api.collect.return_value = data
    result = {"issues": [{"category": "Playback", "action": "Investigate freezing videos.", "priority": "high", "keywords": ["videos freeze"], "evidence": [{"review_id": str(i), "quote": "videos freeze"} for i in range(100)]}]}
    if mode == "fast":
        result["sentiments"] = [{"review_id": str(i), "sentiment": -1 if i % 2 else 1} for i in reversed(range(100))]
    api.response.clear()
    api.response.update(result)
    classifier = Mock(return_value=[{"label": "negative" if i % 2 else "positive", "score": .9} for i in range(100)])
    with TestClient(create_app(Settings(quality_enabled=mode == "quality", otel_enabled=False), lambda _: classifier)) as client:
        response = client.post("/api/analyze", json={"app_id": "123", "review_count": 100, "mode": mode})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["reviews"]) == len({review["id"] for review in body["reviews"]}) == 100
    assert body["metrics"]["ratings"]["average"] == 3
    assert body["metrics"]["sentiment"]["counts"] == {"negative": 50, "positive": 50, "neutral": 0}
    issue = body["metrics"]["issues"]["items"][0]
    assert issue["frequency"] == 100
    assert issue["negative_review_count"] == 50
    assert issue["negative_review_fraction"] == 1
    assert body["metrics"]["issues"]["coverage"] == 1
    assert classifier.call_count == (1 if mode == "quality" else 0)
    assert len(api.requests) == 1


def test_quote_capitalization_is_restored_from_source_without_rewriting_words():
    result = unified()
    result.issues[0].evidence[0].quote = "crashes 😡"
    _, report = validate(result)
    issue = report["insights"]["high"]["repeated"][0]
    assert issue["evidence_quotes"][0] == {"review_id": "n", "quote": "Crashes 😡"}
    result.issues[0].evidence[0].quote = "Does not crash"
    with pytest.raises(InsightValidationError):
        validate(result)


def test_quote_whitespace_and_typographic_quotes_restore_original_source():
    data = source()
    data["reviews"][0]["text"] = "It doesn’t open\n  after updates"
    result = unified()
    result.issues[0].evidence = [result.issues[0].evidence[0]]
    result.issues[0].evidence[0].quote = "it doesn't open after updates"
    _, report = validate(result, data)
    assert report["insights"]["high"]["single"][0]["evidence_quotes"][0]["quote"] == data["reviews"][0]["text"]
    result.issues[0].evidence[0].quote = "It does open after updates"
    with pytest.raises(InsightValidationError):
        validate(result, data)


@pytest.mark.parametrize("reason", ["MAX_TOKENS", "SAFETY"])
def test_truncated_or_blocked_generation_fails_even_when_json_is_complete(fast_api, reason):
    api = fast_api
    api.finish["finishReason"] = reason
    response = api.client.post("/api/analyze", json={"app_id": "123", "mode": "fast"})
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    assert len(api.requests) == 1
