import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import emoji
import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ConfigDict, Field


MODEL = "gemini-3.5-flash-lite"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts/generate_insights.txt"
SENTIMENTS = ("negative", "positive", "neutral")
PRIORITIES = ("high", "medium", "low")


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=180)
    priority: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(min_length=1)


class ModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[Issue]


def preprocess_text(text, limit):
    text = emoji.replace_emoji(
        text, replace=lambda value, _: "[" + emoji.demojize(value).strip(":").replace("_", " ") + "]",
    )
    text = " ".join(text.split())
    if len(text) <= limit:
        return text, False
    marker = " [truncated] "
    head = limit * 3 // 4
    tail = limit - head - len(marker)
    return text[:head] + marker + text[-tail:], True


def prepare_reviews(data):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("input must be a JSON object containing a reviews list")
    if not isinstance(data.get("metadata", {}), dict):
        raise ValueError("metadata must be a JSON object")
    groups = {sentiment: [] for sentiment in SENTIMENTS}
    seen = set()
    for index, review in enumerate(data["reviews"]):
        if not isinstance(review, dict):
            raise ValueError(f"review at index {index} must be an object")
        review_id = review.get("id")
        if not isinstance(review_id, str) or not review_id.strip() or review_id in seen:
            raise ValueError(f"review at index {index} must have a unique nonempty string ID")
        title, text, rating = review.get("title"), review.get("text"), review.get("rating")
        if not isinstance(title, str) or not isinstance(text, str) or not text.strip():
            raise ValueError(f"review {review_id} must have a string title and nonempty text")
        if type(rating) is not int or not 1 <= rating <= 5:
            raise ValueError(f"review {review_id} must have an integer rating from 1 to 5")
        sentiment = review.get("sentiment")
        label = sentiment.get("label") if isinstance(sentiment, dict) else sentiment
        if label not in SENTIMENTS:
            raise ValueError(f"review {review_id} must have a valid sentiment; run analyze_sentiment.py first")
        seen.add(review_id)
        title, title_cut = preprocess_text(title, 200)
        text, text_cut = preprocess_text(text, 4000)
        prepared = {"id": review_id, "title": title, "text": text, "rating": rating}
        truncated = [field for field, cut in (("title", title_cut), ("text", text_cut)) if cut]
        if truncated:
            prepared["truncated_fields"] = truncated
        groups[label].append(prepared)
    return groups


def request_insights(groups, prompt):
    if not any(groups.values()):
        return ModelReport(issues=[])
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("set GEMINI_API_KEY or GOOGLE_API_KEY before generating insights")
    with genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=60000,
            retry_options=types.HttpRetryOptions(attempts=3, initial_delay=1, max_delay=8),
        ),
    ) as client:
        response = client.models.generate_content(
            model=MODEL,
            contents=json.dumps(groups, ensure_ascii=False, separators=(",", ":")),
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                response_mime_type="application/json",
                response_json_schema=ModelReport.model_json_schema(),
                max_output_tokens=8192,
            ),
        )
    if not response.text:
        raise ValueError("Gemini returned no report; no insights were saved")
    return ModelReport.model_validate_json(response.text)


def build_report(data, groups, response, prompt):
    negative_ids = {review["id"] for review in groups["negative"]}
    categorized = set()
    categories = set()
    insights = {priority: {"repeated": [], "single": []} for priority in PRIORITIES}
    for issue in response.issues:
        ids = set(issue.evidence_ids)
        if len(ids) != len(issue.evidence_ids) or not ids <= negative_ids:
            raise ValueError("issue evidence must contain unique IDs of negative input reviews")
        category = " ".join(issue.category.split())
        action = " ".join(issue.action.split())
        if not category or not action or category.casefold() in categories:
            raise ValueError("issues must have nonempty actions and unique categories")
        categories.add(category.casefold())
        categorized.update(ids)
        count = len(ids)
        insights[issue.priority]["repeated" if count > 1 else "single"].append({
            "category": category,
            "action": action,
            "review_count": count,
            "negative_review_fraction": count / len(negative_ids),
            "evidence_ids": sorted(ids),
        })
    for buckets in insights.values():
        for items in buckets.values():
            items.sort(key=lambda item: (-item["review_count"], item["category"].casefold()))
    return {
        "metadata": {
            **data.get("metadata", {}),
            "insight_generation": {
                "model": MODEL,
                "model_called": any(groups.values()),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "preprocessing": {"emoji": "descriptive words", "title_limit": 200, "text_limit": 4000},
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


def render_report(report):
    summary = report["summary"]
    counts = summary["sentiments"]
    lines = [
        "# Review insights", "",
        f"Reviews analyzed: {summary['total_reviews']}",
        f"Sentiment: {counts['negative']} negative · {counts['positive']} positive · {counts['neutral']} neutral",
        f"Negative reviews categorized: {summary['categorized_negative_reviews']}/{counts['negative']}",
    ]
    for priority, buckets in report["insights"].items():
        for recurrence, items in buckets.items():
            if items:
                lines.extend(["", f"## {priority.title()} priority — {recurrence} reports", ""])
                for item in items:
                    action = item["action"].replace("<", "&lt;").replace(">", "&gt;")
                    count = item["review_count"]
                    lines.append(f"- {action} — {count} {'review' if count == 1 else 'reviews'}.")
    if not summary["issue_count"]:
        lines.extend(["", "No supported actionable complaints were identified."])
    lines.extend(["", "Counts refer to unique reviews per issue; a review can support several issues."])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate evidence-based app review insights with Gemini")
    parser.add_argument("input", type=Path, help="JSON file produced by analyze_sentiment.py")
    parser.add_argument("--output", type=Path, help="Defaults to INPUT_STEM_insights.json")
    args = parser.parse_args()
    output = args.output or args.input.with_name(f"{args.input.stem}_insights.json")
    markdown = output.with_suffix(".md")
    if output.suffix.lower() != ".json" or args.input.resolve() in {output.resolve(), markdown.resolve()}:
        parser.error("output must be a .json path distinct from the input")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        groups = prepare_reviews(data)
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        response = request_insights(groups, prompt)
        report = build_report(data, groups, response, prompt)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown.write_text(render_report(report), encoding="utf-8")
    except (OSError, ValueError, errors.APIError, httpx.HTTPError) as exc:
        print(f"Unable to generate insights: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {report['summary']['issue_count']} issues to {output} and {markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
