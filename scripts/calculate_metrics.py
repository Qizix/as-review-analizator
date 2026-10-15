import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from app.services.metrics import calculate_metrics

def main():
    parser = argparse.ArgumentParser(description="Calculate rating, sentiment, and issue metrics locally")
    parser.add_argument("input", type=Path, help="JSON file containing sentiment-classified reviews")
    parser.add_argument("--insights", type=Path, required=True, help="JSON report produced by generate_insights.py")
    parser.add_argument("--output", type=Path, help="Defaults to INPUT_STEM_metrics.json")
    args = parser.parse_args()
    output = args.output or args.input.with_name(f"{args.input.stem}_metrics.json")
    if output.resolve() in {args.input.resolve(), args.insights.resolve()}:
        parser.error("output must differ from both input files")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        insights = json.loads(args.insights.read_text(encoding="utf-8"))
        metrics = calculate_metrics(data, insights)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Unable to calculate metrics: {exc}", file=sys.stderr)
        return 1
    average = metrics["ratings"]["average"]
    coverage = metrics["issues"]["coverage_percentage"]
    print(f"Average rating: {f'{average:.2f}/5' if average is not None else 'n/a'}")
    print(f"Rating counts: {metrics['ratings']['counts']}")
    print(f"Sentiment counts: {metrics['sentiments']['counts']}")
    print(f"Issue coverage: {f'{coverage:.2f}%' if coverage is not None else 'n/a'}")
    print(f"Saved metrics to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
