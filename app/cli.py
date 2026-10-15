import argparse
import json
import sys
from pathlib import Path

from app.schemas import AnalyzeRequest
from app.services.analyzer import Analyzer
from app.services.sentiment import load_classifier
from app.settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Run the complete App Store review analysis pipeline")
    parser.add_argument("app_id")
    parser.add_argument("--country", default="us")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42, help="Deprecated compatibility option; ignored")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        request = AnalyzeRequest(app_id=args.app_id, country=args.country, review_count=args.count, seed=args.seed)
        settings = Settings.from_env()
        result = Analyzer(settings, load_classifier(settings)).analyze(request)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved analysis for {len(result['reviews'])} reviews to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
