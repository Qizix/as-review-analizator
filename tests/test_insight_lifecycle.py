import hashlib
from unittest.mock import Mock

from app.services.execution import AnalysisRun
from app.services.insights import ModelReport, generate_insights
from app.services.preprocessor import prepare_reviews
from app.settings import Settings


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
    result = generate_insights(data, prepare_reviews(data), run, "configured-model")
    metadata = result["metadata"]["insight_generation"]
    assert metadata["model"] == "configured-model"
    assert metadata["prompt_sha256"] == hashlib.sha256(adapter.call_args.args[1].encode()).hexdigest()
    assert result["insights"]["high"]["single"][0]["evidence_quotes"][0]["quote"] == data["reviews"][0]["text"]
    assert adapter.call_count == 2
    assert run.usage == {"total_token_count": 40}
    assert set(run.durations) == {"gemini", "validation"}
