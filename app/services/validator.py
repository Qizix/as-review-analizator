import hashlib
from datetime import datetime, timezone

from app.settings import GEMINI_MODEL as MODEL
from app.services.preprocessor import SENTIMENTS, group_reviews

PRIORITIES = ("high", "medium", "low")


class InsightValidationError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def build_report(data, groups, response, prompt, model=MODEL, *, allow_all_sentiments=False):
    negative_ids = {review["id"] for review in group_reviews(data)["negative"]}
    eligible_ids = {review["id"] for reviews in group_reviews(data).values() for review in reviews} if allow_all_sentiments else negative_ids
    categorized = set()
    categories = set()
    source_reviews = {review["id"]: review for review in data["reviews"]}
    insights = {priority: {"repeated": [], "single": []} for priority in PRIORITIES}
    for issue in response.issues:
        ids = set(issue.evidence_ids)
        if len(ids) != len(issue.evidence_ids):
            raise InsightValidationError("duplicate_evidence_ids")
        if not ids <= eligible_ids:
            raise InsightValidationError("nonnegative_or_unknown_evidence_ids")
        category = " ".join(issue.category.split())
        action = " ".join(issue.action.split())
        if not category or not action or category.casefold() in categories:
            raise InsightValidationError("empty_action_or_duplicate_category")
        categories.add(category.casefold())
        categorized.update(ids & negative_ids)
        count = len(ids)
        insights[issue.priority]["repeated" if count > 1 else "single"].append({
            "category": category,
            "action": action,
            "review_count": count,
            "negative_review_count": len(ids & negative_ids),
            "negative_review_fraction": len(ids & negative_ids) / len(negative_ids) if negative_ids else None,
            "evidence_ids": sorted(ids),
            "keywords": list(dict.fromkeys(word.strip() for word in issue.keywords if word.strip())),
            "evidence_quotes": [{"review_id": review_id, "quote": source_reviews[review_id]["text"]} for review_id in sorted(ids)],
        })
    for buckets in insights.values():
        for items in buckets.values():
            items.sort(key=lambda item: (-item["review_count"], item["category"].casefold()))
    return {
        "metadata": {
            **data.get("metadata", {}),
            "insight_generation": {
                "model": model,
                "model_called": any(groups.values()),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "preprocessing": {"emoji": "preserved" if allow_all_sentiments else "descriptive words", "title_limit": 200, "text_limit": 4000},
                "truncated_review_ids": [review["id"] for reviews in groups.values() for review in reviews if "truncated_fields" in review],
            },
        },
        "summary": {
            "total_reviews": sum(len(reviews) for reviews in groups.values()),
            "sentiments": {sentiment: len(groups[sentiment]) for sentiment in SENTIMENTS},
            "categorized_negative_reviews": len(categorized),
            "uncategorized_negative_reviews": len(negative_ids - categorized),
            "issue_count": len(response.issues),
        },
        "insights": insights,
    }


