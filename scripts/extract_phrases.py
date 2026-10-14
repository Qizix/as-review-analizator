import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer


GENERIC_WORDS = ENGLISH_STOP_WORDS | set(
    "app apps application applications just really actually literally basically "
    "please thanks thank hello hi it's i'm i've i'd i'll you're you've "
    "you'll you'd we're we've we'll we'd they're they've they'll they'd "
    "he's she's that's there's what's let's".split()
)
NEGATIONS = set(
    "no not never neither nor cannot without nothing nowhere nobody none hardly barely".split()
)
TOKEN_PATTERN = r"(?u)\b\w\w+(?:['’]\w+)?\b"


def is_generic(phrase):
    tokens = phrase.replace("’", "'").split()
    if any(token in NEGATIONS or token.endswith("n't") for token in tokens):
        return False
    return all(token in GENERIC_WORDS for token in tokens)


def extract_phrases(data):
    if not isinstance(data, dict) or not isinstance(data.get("reviews"), list):
        raise ValueError("input must be a JSON object containing a reviews list")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a JSON object")
    texts = []
    for index, review in enumerate(data["reviews"]):
        if not isinstance(review, dict) or not isinstance(review.get("text"), str) or not review["text"].strip():
            raise ValueError(f"review at index {index} must contain nonempty text")
        sentiment = review.get("sentiment")
        if not isinstance(sentiment, dict) or sentiment.get("label") not in ("positive", "negative", "neutral"):
            raise ValueError(f"review at index {index} must have a sentiment label; run analyze_sentiment.py first")
        if sentiment["label"] == "negative":
            texts.append(review["text"])
    vectorizer = CountVectorizer(
        ngram_range=(1, 3), binary=True, min_df=1, lowercase=True,
        stop_words=None, token_pattern=TOKEN_PATTERN,
    )
    phrases = []
    candidate_count = 0
    analyzer = vectorizer.build_analyzer()
    if any(analyzer(text) for text in texts):
        matrix = vectorizer.fit_transform(texts)
        candidates = vectorizer.get_feature_names_out()
        frequencies = matrix.sum(axis=0).A1
        candidate_count = len(candidates)
        for phrase, frequency in zip(candidates, frequencies):
            if not is_generic(phrase):
                phrases.append({
                    "phrase": str(phrase),
                    "document_frequency": int(frequency),
                    "negative_review_fraction": int(frequency) / len(texts),
                })
    phrases.sort(key=lambda item: (-item["document_frequency"], item["phrase"]))
    return {
        "metadata": {
            **metadata,
            "phrase_extraction": {
                "method": "CountVectorizer document frequency",
                "review_count": len(data["reviews"]),
                "negative_review_count": len(texts),
                "candidate_count": candidate_count,
                "filtered_count": candidate_count - len(phrases),
                "returned_count": len(phrases),
                "ngram_range": [1, 3],
                "binary": True,
                "min_df": 1,
                "lowercase": True,
                "stop_words": None,
                "token_pattern": TOKEN_PATTERN,
                "generic_filter": "All tokens generic, unless a negation is present",
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            },
        },
        "phrases": phrases,
    }


def main():
    parser = argparse.ArgumentParser(description="Rank phrases mentioned in negative reviews")
    parser.add_argument("input", type=Path, help="JSON file produced by analyze_sentiment.py")
    parser.add_argument("--output", type=Path, help="Defaults to INPUT_STEM_phrases.json beside the input")
    args = parser.parse_args()
    output = args.output or args.input.with_name(f"{args.input.stem}_phrases.json")
    if args.input.resolve() == output.resolve():
        parser.error("output must differ from the input path")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        result = extract_phrases(data)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Unable to extract phrases: {exc}", file=sys.stderr)
        return 1
    count = result["metadata"]["phrase_extraction"]["negative_review_count"]
    print(f"Saved {len(result['phrases'])} phrases from {count} negative reviews to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
