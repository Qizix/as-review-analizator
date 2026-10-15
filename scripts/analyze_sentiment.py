import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from transformers import pipeline

from app.services.sentiment import MODEL, analyze_sentiment

def main():
    parser = argparse.ArgumentParser(description="Classify the sentiment of collected review texts")
    parser.add_argument("input", type=Path, help="JSON file produced by fetch_reviews.py")
    parser.add_argument("--output", type=Path, help="Defaults to INPUT_STEM_sentiment.json beside the input")
    args = parser.parse_args()
    output = args.output or args.input.with_name(f"{args.input.stem}_sentiment.json")
    if args.input.resolve() == output.resolve():
        parser.error("output must differ from the input path")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = analyze_sentiment(data)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Unable to analyze reviews: {exc}", file=sys.stderr)
        return 1
    print(f"Saved sentiment for {len(result['reviews'])} reviews to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
