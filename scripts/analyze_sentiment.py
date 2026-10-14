import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from transformers import pipeline


MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"


def analyze_sentiment(data):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("input must be a JSON object containing a reviews list")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    reviews = data["reviews"]
    texts = []
    for index, review in enumerate(reviews):
        if not isinstance(review, dict) or not isinstance(review.get("text"), str) or not review["text"].strip():
            raise ValueError(f"review at index {index} must contain nonempty text")
        texts.append(review["text"])
    predictions = []
    if texts:
        classifier = pipeline(task="text-classification", model=MODEL, device="cpu")
        predictions = classifier(texts, batch_size=8, truncation=True, max_length=512)
    if len(predictions) != len(reviews):
        raise ValueError("classifier returned an unexpected number of predictions")
    analyzed = []
    for review, prediction in zip(reviews, predictions):
        label = prediction["label"].lower()
        score = float(prediction["score"])
        if label not in {"positive", "negative", "neutral"} or not 0 <= score <= 1:
            raise ValueError("classifier returned an invalid sentiment prediction")
        analyzed.append({**review, "sentiment": {"label": label, "score": score}})
    return {
        **data,
        "metadata": {
            **metadata,
            "sentiment_analysis": {
                "model": MODEL,
                "device": "cpu",
                "batch_size": 8,
                "truncation": True,
                "max_length": 512,
                "analyzed_count": len(analyzed),
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        "reviews": analyzed,
    }


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
