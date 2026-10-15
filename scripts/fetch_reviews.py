import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.collector import COUNTRIES, URL_TEMPLATE, validate_inputs, fetch_page, extract_review, collect_reviews

def main():
    parser = argparse.ArgumentParser(description="Sample Apple App Store reviews from RSS JSON")
    parser.add_argument("app_id")
    parser.add_argument("--country", default="us")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_inputs(args.app_id, args.country, args.count)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        with httpx.Client(follow_redirects=True) as client:
            result = collect_reviews(client, args.app_id, args.country, args.count, args.seed)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"Unable to save reviews: {exc}", file=sys.stderr)
        return 1
    metadata = result["metadata"]
    print(f"Saved {metadata['actual_count']} of {args.count} requested reviews to {args.output}")
    if metadata["shortfall"]:
        print(f"Shortfall: {metadata['shortfall']} reviews; only {metadata['available_count']} unique valid reviews available.", file=sys.stderr)
    if metadata["failed_pages"]:
        print(f"Collection incomplete: {len(metadata['failed_pages'])} pages failed; see metadata.failed_pages.", file=sys.stderr)
    return 1 if metadata["failed_pages"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
