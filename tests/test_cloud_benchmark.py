from copy import deepcopy

import pytest

from experiments.benchmark_cloud import verify_report
from app.services.metrics import calculate_metrics


def report():
    reviews = [{"id": "a", "title": "Useful", "text": "I like it but videos freeze", "rating": 5, "sentiment": {"label": "positive"}}, {"id": "b", "title": "Broken", "text": "Videos freeze", "rating": 1, "sentiment": {"label": "negative"}}]
    issue = {"category": "Playback", "priority": "high", "review_count": 2, "evidence_ids": ["a", "b"], "evidence_quotes": [{"review_id": "a", "quote": "videos freeze"}, {"review_id": "b", "quote": "Videos freeze"}]}
    grouped = {priority: {"single": [], "repeated": [issue] if priority == "high" else []} for priority in ("high", "medium", "low")}
    metrics = calculate_metrics({"reviews": reviews}, {"insights": grouped})
    return {"app": {}, "collection": {"requested_count": 2, "actual_count": 2, "shortfall": 0}, "reviews": reviews, "insights": {"issues": [issue], "recommendations": []}, "metrics": {"total_reviews": metrics["total_reviews"], "ratings": metrics["ratings"], "sentiment": metrics["sentiments"], "issues": metrics["issues"]}, "metadata": {"mode": "fast"}}


def test_benchmark_recomputes_metrics_and_compares_original_inputs():
    data = report()
    digest = verify_report(data)
    reordered = deepcopy(data)
    reordered["reviews"].reverse()
    assert verify_report(reordered) == digest
    different = deepcopy(data)
    different["reviews"][0]["title"] = "Changed review"
    assert verify_report(different) != digest


@pytest.mark.parametrize("change", ["quote", "frequency", "coverage", "shortfall", "duplicate"])
def test_benchmark_rejects_inconsistent_success_responses(change):
    data = report()
    if change == "quote":
        data["insights"]["issues"][0]["evidence_quotes"][0]["quote"] = "fabricated"
    elif change == "frequency":
        data["metrics"]["issues"]["items"][0]["frequency"] = 200
    elif change == "coverage":
        data["metrics"]["issues"]["covered_negative_reviews"] = 0
    elif change == "shortfall":
        data["collection"]["shortfall"] = 1
    else:
        data["reviews"][1]["id"] = "a"
    with pytest.raises(ValueError):
        verify_report(data)
