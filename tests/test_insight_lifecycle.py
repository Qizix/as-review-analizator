import hashlib
from unittest.mock import Mock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.observability import Observation, current_observation
from app.services.execution import AnalysisError, AnalysisRun
from app.services.insights import ModelReport, generate_insights
from app.services.review_batch import ReviewBatch
from app.services.validator import InsightValidationError, build_report
from app.settings import Settings
from app.telemetry import Telemetry


def test_repaired_report_records_actual_model_prompt_and_usage(monkeypatch):
    data = {"reviews": [{"id": "n1", "title": "Broken", "text": "Does not open 😡", "rating": 5, "sentiment": "negative"}]}
    invalid = ModelReport.model_validate({"issues": [{"category": "Startup", "action": "Investigate startup.", "priority": "high", "evidence_ids": ["unknown"]}]})
    valid = invalid.model_copy(deep=True)
    valid.issues[0].evidence_ids = ["n1"]

    def request(groups, prompt, usage, model, **kwargs):
        usage["total_token_count"] = usage.get("total_token_count", 0) + 20
        return invalid if "Regenerate" not in prompt else valid

    adapter = Mock(side_effect=request)
    monkeypatch.setattr("app.services.insights.request_insights", adapter)
    run = AnalysisRun(Settings())
    result = generate_insights(ReviewBatch(data), run, "configured-model")
    metadata = result["metadata"]["insight_generation"]
    assert metadata["model"] == "configured-model"
    assert metadata["prompt_sha256"] == hashlib.sha256(adapter.call_args.args[1].encode()).hexdigest()
    assert result["insights"]["high"]["single"][0]["evidence_quotes"][0]["quote"] == data["reviews"][0]["text"]
    assert adapter.call_count == 2
    assert run.usage == {"total_token_count": 40}
    assert set(run.durations) == {"gemini", "validation"}


@pytest.mark.parametrize("label", ["positive", "neutral"])
def test_caller_cannot_promote_context_reviews_to_evidence(monkeypatch, label):
    source = {"reviews": [{"id": "p1", "title": "Works", "text": "Everything works", "rating": 1, "sentiment": label}]}
    batch = ReviewBatch(source)
    groups = batch.groups
    groups["negative"] = groups[label]
    groups[label] = []
    source["reviews"][0]["sentiment"] = "negative"
    exposed_source = batch.source
    exposed_source["reviews"][0]["sentiment"] = "negative"
    invalid = ModelReport.model_validate({"issues": [{"category": "Startup", "action": "Investigate startup failures.", "priority": "high", "evidence_ids": ["p1"]}]})
    adapter = Mock(return_value=invalid)
    monkeypatch.setattr("app.services.insights.request_insights", adapter)
    with pytest.raises(InsightValidationError, match="nonnegative_or_unknown_evidence_ids"):
        generate_insights(batch, AnalysisRun(Settings()))
    assert adapter.call_count == 2
    assert adapter.call_args.args[0]["negative"] == []
    assert batch.source["reviews"][0]["text"] == "Everything works"


def test_report_validator_uses_source_sentiment_even_with_mismatched_groups():
    source = {"reviews": [{"id": "p1", "title": "Works", "text": "Everything works", "rating": 5, "sentiment": "positive"}]}
    groups = {"negative": [{"id": "p1", "title": "Broken", "text": "Does not open", "rating": 1}], "positive": [], "neutral": []}
    invalid = ModelReport.model_validate({"issues": [{"category": "Startup", "action": "Investigate startup failures.", "priority": "high", "evidence_ids": ["p1"]}]})
    with pytest.raises(InsightValidationError, match="nonnegative_or_unknown_evidence_ids"):
        build_report(source, groups, invalid, "Prompt")


def test_review_batch_preserves_originals_and_binds_inference_labels():
    text = "START 😡 " + "x" * 4500 + " END"
    data = {"reviews": [{"id": "n1", "title": "Broken", "text": text, "rating": 5}]}
    batch = ReviewBatch(data)
    classifier = Mock(return_value=[{"label": "negative", "score": .9}])
    batch.classify(classifier, Settings().sentiment_revision)
    assert len(classifier.call_args.args[0][0]) == 4000
    assert batch.groups["negative"][0]["text"] == classifier.call_args.args[0][0]
    assert batch.groups["negative"][0]["truncated_fields"] == ["text"]
    assert batch.source["reviews"][0]["text"] == text
    assert batch.source["reviews"][0]["rating"] == 5
    assert "sentiment" not in data["reviews"][0]


@pytest.mark.parametrize("failure", ["timeout", "unexpected"])
def test_non_evidence_failures_do_not_increment_evidence_counter(monkeypatch, failure):
    data = {"reviews": [{"id": "n1", "title": "Broken", "text": "Does not open", "rating": 1, "sentiment": "negative"}]}
    batch = ReviewBatch(data)
    response = ModelReport(issues=[])
    monkeypatch.setattr("app.services.insights.request_insights", Mock(return_value=response))
    clock = [100.0]
    monkeypatch.setattr("app.services.execution.time.monotonic", lambda: clock[0])
    original = build_report

    def validate(*args):
        if failure == "unexpected":
            raise RuntimeError("Internal validation failure")
        result = original(*args)
        clock[0] = 131
        return result

    monkeypatch.setattr("app.services.insights.build_report", validate)
    exporter = InMemorySpanExporter()
    telemetry = Telemetry(Settings(), exporter)
    telemetry.start()
    observation = Observation(telemetry)
    token = current_observation.set(observation)
    try:
        with pytest.raises(AnalysisError if failure == "timeout" else RuntimeError) as error:
            generate_insights(batch, AnalysisRun(Settings(analysis_timeout_seconds=30)))
        if failure == "timeout":
            assert error.value.status == 504
            assert error.value.stage == "validation"
        span = next(span for span in exporter.get_finished_spans() if span.name == "insights.validate")
        assert span.status.status_code == trace.StatusCode.ERROR
        assert span.attributes["app.validation.failures"] == 0
        assert any(event.name == "exception" for event in span.events)
        assert observation.warnings == []
    finally:
        current_observation.reset(token)
        telemetry.shutdown()
