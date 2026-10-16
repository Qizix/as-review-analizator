import hashlib
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.observability import observe, operation, warning
from app.services.insights import InsightDecodeError, ModelReport, request_structured
from app.services.source_excerpts import reference_excerpts
from app.services.validator import InsightValidationError, build_report
from app.settings import SENTIMENT_BATCH_SIZE, SENTIMENT_MODEL


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts/analyze_reviews.txt"


class SentimentPrediction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1, max_length=100)
    sentiment: Literal[-1, 0, 1]

    @field_validator("sentiment", mode="before")
    @classmethod
    def integer_label(cls, value):
        if type(value) is not int:
            raise ValueError("sentiment must be an integer")
        return value


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=1, max_length=4000)


class GroundedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(min_length=1, max_length=80)
    action: str = Field(min_length=1, max_length=180)
    priority: Literal["high", "medium", "low"]
    keywords: list[str] = Field(max_length=8)
    evidence: list[Evidence] = Field(min_length=1)


class IssueAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issues: list[GroundedIssue]


class UnifiedAnalysis(IssueAnalysis):
    sentiments: list[SentimentPrediction]


class ReferencedIssue(GroundedIssue):
    evidence: list[Annotated[int, Field(ge=0, strict=True)]] = Field(min_length=1)


class ReferencedAnalysis(UnifiedAnalysis):
    issues: list[ReferencedIssue]


def request_analysis(reviews, prompt, usage, model, mode, *, deadline):
    result_type = ReferencedAnalysis if mode == "fast" else IssueAnalysis
    schema = result_type.model_json_schema()
    payload = reviews
    if mode == "fast":
        payload, references = reference_excerpts(reviews)
        if references:
            schema["$defs"]["ReferencedIssue"]["properties"]["evidence"]["items"]["maximum"] = len(references) - 1
        else:
            schema["properties"]["issues"]["maxItems"] = 0
    text = request_structured({"reviews": payload}, prompt, schema, usage, model, deadline=deadline)
    with operation("insights.decode"):
        try:
            return result_type.model_validate_json(text)
        except ValueError as exc:
            raise InsightDecodeError("Gemini returned an invalid analysis") from exc


def _source_quote(quote, source):
    if not quote.strip():
        return None
    equivalents = {"'": "['’]", "’": "['’]", '"': '["“”]', "“": '["“”]', "”": '["“”]'}
    parts = []
    for token in re.findall(r"\s+|[^\s]", quote.strip()):
        parts.append(r"\s+" if token.isspace() else equivalents.get(token, re.escape(token)))
    pattern = "".join(parts)
    for field in ("title", "text", "text_tail"):
        match = re.search(pattern, source.get(field, ""), re.IGNORECASE)
        if match:
            return match.group()
    return None


def validate_analysis(batch, reviews, result, prompt, model):
    visible = {review["id"]: review for review in reviews}
    issues, quotations = [], {}
    references = reference_excerpts(reviews)[1] if isinstance(result, ReferencedAnalysis) else {}
    for issue in result.issues:
        evidence = {}
        for item in issue.evidence:
            if isinstance(result, ReferencedAnalysis):
                reference = references.get(item)
                if reference is None:
                    raise InsightValidationError("unknown_evidence_excerpt")
                review_id, quote = reference
            else:
                review_id = item.review_id
                source = visible.get(review_id)
                if source is None:
                    raise InsightValidationError("unknown_evidence_ids")
                quote = _source_quote(item.quote, source)
                if quote is None:
                    raise InsightValidationError("evidence_quote_not_in_source_excerpt")
            evidence.setdefault(review_id, quote)
        issues.append({**issue.model_dump(exclude={"evidence"}), "evidence_ids": sorted(evidence)})
        quotations[" ".join(issue.category.split())] = evidence
    if isinstance(result, UnifiedAnalysis):
        batch.attach_sentiments(result.sentiments)
    report = build_report(batch.source, batch.groups, ModelReport(issues=issues), prompt, model, allow_all_sentiments=True)
    for buckets in report["insights"].values():
        for items in buckets.values():
            for issue in items:
                issue["evidence_quotes"] = [{"review_id": review_id, "quote": quote} for review_id, quote in sorted(quotations[issue["category"]].items())]
    return report


def interpret_reviews(batch, mode, classifier, run, settings):
    if mode == "quality":
        with run.stage("sentiment", "sentiment.classify", {"app.model": SENTIMENT_MODEL, "app.reviews.count": len(batch.source["reviews"]), "app.batch_size": SENTIMENT_BATCH_SIZE}):
            batch.classify(classifier, settings.sentiment_revision)
    reviews = batch.semantic_reviews
    if mode == "quality":
        labels = {review["id"]: review["sentiment"]["label"] for review in batch.source["reviews"]}
        reviews = [{**review, "sentiment": labels[review["id"]]} for review in reviews]
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    if mode == "fast":
        prompt = PROMPT_PATH.with_name("analyze_reviews_references.txt").read_text(encoding="utf-8")
        prompt += "\nClassify every review using -1=negative, 0=neutral, 1=positive. Return exactly one prediction per ID."
    else:
        prompt += "\nUse the supplied text-based sentiment labels; do not return new predictions."
    if not reviews:
        result = UnifiedAnalysis(sentiments=[], issues=[]) if mode == "fast" else IssueAnalysis(issues=[])
        return validate_analysis(batch, reviews, result, prompt, settings.gemini_model)
    for attempt in range(2):
        with run.stage("gemini", "insights.repair" if attempt else "analysis.unified" if mode == "fast" else "insights.generate", {"gen_ai.request.model": settings.gemini_model, "app.analysis.mode": mode, "app.prompt.version": hashlib.sha256(prompt.encode()).hexdigest()}):
            observe(gemini_model=settings.gemini_model, prompt_version=hashlib.sha256(prompt.encode()).hexdigest())
            result = request_analysis(reviews, prompt, run.usage, settings.gemini_model, mode, deadline=run.deadline)
        try:
            with run.stage("validation", "insights.validate") as span:
                span.set_attribute("app.validation.failures", 0)
                try:
                    report = validate_analysis(batch, reviews, result, prompt, settings.gemini_model)
                except InsightValidationError:
                    span.set_attribute("app.validation.failures", 1)
                    raise
                span.set_attribute("app.issues.count", report["summary"]["issue_count"])
            return report
        except InsightValidationError as exc:
            observe(insight_validation_reason=exc.reason)
            warning("INSIGHTS_INVALID", reason=exc.reason)
            if attempt:
                raise
            observe(insight_repair_count=1)
            warning("INSIGHTS_RETRY", reason=exc.reason)
            prompt += f"\nRegenerate the complete structured report. Validation failed: {exc.reason}. Use existing IDs and keep categories unique."
            if mode == "fast":
                prompt += " Evidence must be a list of existing integer excerpt IDs from the supplied reviews. Do not generate quote text. Return every sentiment prediction."
            else:
                prompt += " Use exact nonempty source excerpts. Copy short, contiguous quotations directly from one source field without paraphrasing, correcting spelling, or adding ellipses."
