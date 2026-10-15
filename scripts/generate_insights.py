import argparse
import json
import sys
from pathlib import Path

import httpx
from google.genai import errors

from app.services.execution import AnalysisError, AnalysisRun
from app.services.insights import MODEL, PROMPT_PATH, ModelReport, prepare_reviews, preprocess_text, request_insights, build_report, render_report, generate_insights
from app.services.review_batch import ReviewBatch
from app.settings import Settings

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
        batch = ReviewBatch(data)
        settings = Settings.from_env()
        report = generate_insights(batch, AnalysisRun(settings), settings.gemini_model)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown.write_text(render_report(report), encoding="utf-8")
    except (OSError, ValueError, AnalysisError, errors.APIError, httpx.HTTPError) as exc:
        print(f"Unable to generate insights: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {report['summary']['issue_count']} issues to {output} and {markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
