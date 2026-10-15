import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

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
    "https://apps.apple.com/api/apps/v1/catalog/{country}/apps/{app_id}/reviews"
    "?l=en-US&offset={offset}&platform=web&additionalPlatforms=appletv,ipad,iphone,mac"
)
PAGE_SIZE = 10


def validate_inputs(app_id, country, count):
    if not re.fullmatch(r"[0-9]+", app_id) or int(app_id) == 0:
        raise ValueError("app_id must be a positive numeric Apple app ID")
    country = country.lower()
    if country not in COUNTRIES:
        raise ValueError("country must be an ISO 3166-1 alpha-2 code, such as us")
    if count < 1:
        raise ValueError("count must be positive")
    return country


def fetch_page(client, url, deadline=None):
    for attempt in range(3):
        try:
            remaining = 20.0 if deadline is None else min(20.0, deadline - time.monotonic())
            if remaining <= 0:
                raise httpx.TimeoutException("Collection deadline exceeded")
            response = client.get(url, timeout=remaining)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError("response must contain a review data list")
            if payload.get("next") is not None and not isinstance(payload["next"], str):
                raise ValueError("next page must be a URL string")
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 and exc.response.status_code < 500:
                raise
            if attempt == 2:
                raise
        except (httpx.RequestError, ValueError):
            if attempt == 2:
                raise
        time.sleep(2**attempt if deadline is None else min(2**attempt, max(0, deadline - time.monotonic())))


def extract_review(entry):
    if not isinstance(entry, dict):
        return None
    try:
        review_id = entry["id"]
        attributes = entry["attributes"]
        title = attributes["title"]
        text = attributes["review"]
        rating = attributes["rating"]
    except (KeyError, TypeError):
        return None
    if not all(isinstance(value, str) and value.strip() for value in (review_id, title, text)):
        return None
    if type(rating) is not int or not 1 <= rating <= 5:
        return None
    return {"id": review_id, "title": title, "text": text, "rating": rating}


def collect_reviews(client, app_id, country="us", count=100, seed=42, deadline=None):
    country = validate_inputs(app_id, country, count)
    reviews = {}
    fetched_pages = []
    failed_pages = []
    invalid_records = 0
    duplicate_records = 0
    offset = 0
    fetched_offsets = []
    for page in range(1, 11):
        if page > 1:
            time.sleep(0.5 if deadline is None else min(0.5, max(0, deadline - time.monotonic())))
        if deadline is not None and time.monotonic() >= deadline:
            failed_pages.extend({"page": skipped, "error": "Collection deadline exceeded"} for skipped in range(page, 11))
            break
        url = URL_TEMPLATE.format(country=country, app_id=app_id, offset=offset)
        try:
            payload = fetch_page(client, url, deadline)
        except (httpx.HTTPError, ValueError) as exc:
            failed_pages.append({"page": page, "offset": offset, "error": str(exc)})
            offset += PAGE_SIZE
            continue
        fetched_pages.append(page)
        fetched_offsets.append(offset)
        entries = payload["data"]
        for entry in entries:
            review = extract_review(entry)
            if review is None:
                invalid_records += 1
            elif review["id"] in reviews:
                duplicate_records += 1
            else:
                reviews[review["id"]] = review
        if not entries or not payload.get("next"):
            break
        try:
            next_offset = int(parse_qs(urlsplit(payload["next"]).query)["offset"][0])
            if next_offset <= offset:
                raise ValueError("next offset must increase")
        except (ValueError, KeyError) as exc:
            failed_pages.append({"page": page, "offset": offset, "error": f"Invalid pagination: {exc}"})
            break
        offset = next_offset
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
            "source": "Apple App Store catalog reviews JSON",
            "sampling_pool": "Unique valid reviews from up to 10 catalog pages in Apple response order",
            "language": "en-US",
            "fetched_pages": fetched_pages,
            "fetched_offsets": fetched_offsets,
            "failed_pages": failed_pages,
            "invalid_records": invalid_records,
            "duplicate_records": duplicate_records,
        },
        "reviews": selected,
    }

