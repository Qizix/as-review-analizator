import json
import os
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from app.settings import GEMINI_MODEL as MODEL
from app.services.preprocessor import prepare_reviews, preprocess_text
from app.services.validator import build_report


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/generate_insights.txt"
SENTIMENTS = ("negative", "positive", "neutral")
PRIORITIES = ("high", "medium", "low")


class Issue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=180)
    priority: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list, max_length=8)


class ModelReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[Issue]



def request_insights(groups, prompt, usage=None, model=MODEL):
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
            model=model,
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
    if usage is not None and response.usage_metadata is not None:
        usage.update(response.usage_metadata.model_dump(mode="json", exclude_none=True))
    return ModelReport.model_validate_json(response.text)




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

