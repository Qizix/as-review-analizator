import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx


COUNTRIES = set(
    "ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj "
    "bl bm bn bo bq br bs bt bv bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr "
    "cu cv cw cx cy cz de dj dk dm do dz ec ee eg eh er es et fi fj fk fm fo fr "
    "ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu "
    "id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz "
    "la lb lc li lk lr ls lt lu lv ly ma mc md me mf mg mh mk ml mm mn mo mp mq "
    "mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf "
    "pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si "
    "sj sk sl sm sn so sr ss st sv sx sy sz tc td tf tg th tj tk tl tm tn to tr "
    "tt tv tw tz ua ug um us uy uz va vc ve vg vi vn vu wf ws ye yt za zm zw".split()
)
URL_TEMPLATE = (
    "https://itunes.apple.com/{country}/rss/customerreviews/"
    "page={page}/id={app_id}/sortby=mostrecent/json"
)


def validate_inputs(app_id, country, count):
    if not re.fullmatch(r"[0-9]+", app_id) or int(app_id) == 0:
        raise ValueError("app_id must be a positive numeric Apple app ID")
    country = country.lower()
    if country not in COUNTRIES:
        raise ValueError("country must be an ISO 3166-1 alpha-2 code, such as us")
    if count < 1:
        raise ValueError("count must be positive")
    return country


def fetch_page(client, url):
    for attempt in range(3):
        try:
            response = client.get(url, timeout=20.0)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("feed"), dict):
                raise ValueError("response does not contain a review feed")
            entries = payload["feed"].get("entry", [])
            if not isinstance(entries, list):
                raise ValueError("feed entries must be a list")
            return entries
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 and exc.response.status_code < 500:
                raise
            if attempt == 2:
                raise
        except (httpx.RequestError, ValueError):
            if attempt == 2:
                raise
        time.sleep(2**attempt)


def extract_review(entry):
    if not isinstance(entry, dict):
        return None
    try:
        review_id = entry["id"]["label"]
        title = entry["title"]["label"]
        text = entry["content"]["label"]
        rating = entry["im:rating"]["label"]
    except (KeyError, TypeError):
        return None
    if not all(isinstance(value, str) and value.strip() for value in (review_id, title, text)):
        return None
    if not isinstance(rating, str) or rating not in {"1", "2", "3", "4", "5"}:
        return None
    return {"id": review_id, "title": title, "text": text, "rating": int(rating)}


def collect_reviews(client, app_id, country="us", count=100, seed=42):
    country = validate_inputs(app_id, country, count)
    reviews = {}
    fetched_pages = []
    failed_pages = []
    invalid_records = 0
    duplicate_records = 0
    for page in range(1, 11):
        url = URL_TEMPLATE.format(country=country, app_id=app_id, page=page)
        try:
            entries = fetch_page(client, url)
        except (httpx.HTTPError, ValueError) as exc:
            failed_pages.append({"page": page, "error": str(exc)})
            continue
        fetched_pages.append(page)
        for entry in entries:
            review = extract_review(entry)
            if review is None:
                invalid_records += 1
            elif review["id"] in reviews:
                duplicate_records += 1
            else:
                reviews[review["id"]] = review
    available = list(reviews.values())
    selected = random.Random(seed).sample(available, min(count, len(available)))
    return {
        "metadata": {
            "app_id": app_id,
            "country": country,
            "requested_count": count,
            "available_count": len(available),
            "actual_count": len(selected),
            "shortfall": max(0, count - len(selected)),
            "seed": seed,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "Apple customer reviews RSS JSON",
            "sampling_pool": "Unique valid reviews from pages 1–10 sorted by most recent",
            "fetched_pages": fetched_pages,
            "failed_pages": failed_pages,
            "invalid_records": invalid_records,
            "duplicate_records": duplicate_records,
        },
        "reviews": selected,
    }


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
