import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx
from opentelemetry import trace

from app.observability import increment, operation, warning


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


def fetch_page(client, url, deadline=None, parser=None):
    path = urlsplit(str(url)).path
    source = "catalog" if "/api/apps/" in path else "rss" if "/rss/" in path else "lookup" if path == "/lookup" else "webpage"
    with operation("appstore.request", {"http.request.method": "GET", "app.provider": "apple", "app.collection.source": source}, trace.SpanKind.CLIENT) as span:
        for attempt in range(3):
            span.set_attribute("app.retry_count", attempt)
            status_code = None
            try:
                remaining = 20.0 if deadline is None else min(20.0, deadline - time.monotonic())
                if remaining <= 0:
                    raise httpx.TimeoutException("Collection deadline exceeded")
                response = client.get(url, timeout=remaining)
                status_code = response.status_code
                span.set_attribute("http.response.status_code", status_code)
                response.raise_for_status()
                if parser is not None:
                    return parser(response)
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("response must contain a review data list")
                if payload.get("next") is not None and not isinstance(payload["next"], str):
                    raise ValueError("next page must be a URL string")
                return payload
            except httpx.HTTPStatusError as exc:
                error_type = type(exc).__name__
                if exc.response.status_code != 429 and exc.response.status_code < 500:
                    raise
                if attempt == 2:
                    raise
            except (httpx.RequestError, ValueError) as exc:
                error_type = type(exc).__name__
                if attempt == 2:
                    raise
            delay = 2**attempt if deadline is None else min(2**attempt, max(0, deadline - time.monotonic()))
            span.add_event("retry.scheduled", {"app.retry.attempt": attempt + 1, "app.retry.delay_ms": delay * 1000})
            increment("collection_retry_count")
            warning("UPSTREAM_RETRY", provider="apple", attempt=attempt + 1, status=status_code, error_type=error_type, delay_ms=delay * 1000)
            time.sleep(delay)


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


def collect_catalog_reviews(client, app_id, country="us", count=100, seed=42, deadline=None):
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
            if page == 1:
                break
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
                if len(reviews) >= count:
                    break
        if len(reviews) >= count or not entries or not payload.get("next"):
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
    selected = available
    return {
        "metadata": {
            "app_id": app_id,
            "country": country,
            "requested_count": count,
            "available_count": len(available),
            "actual_count": len(selected),
            "shortfall": max(0, count - len(selected)),
            "seed": seed,
            "selection": "first_unique",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "Apple App Store catalog reviews JSON",
            "sampling_pool": "First unique valid reviews in Apple catalog response order, stopping at the requested count or 10 pages",
            "language": "en-US",
            "fetched_pages": fetched_pages,
            "fetched_offsets": fetched_offsets,
            "failed_pages": failed_pages,
            "invalid_records": invalid_records,
            "duplicate_records": duplicate_records,
        },
        "reviews": selected,
    }


def parse_rss_page(response):
    payload = response.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("feed"), dict):
        raise ValueError("response must contain an RSS feed object")
    feed = payload["feed"]
    entries = feed.get("entry", [])
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise ValueError("RSS entries must be a list or object")
    data = []
    for entry in entries:
        try:
            rating = entry["im:rating"]["label"]
            if not isinstance(rating, str) or rating not in {"1", "2", "3", "4", "5"}:
                raise ValueError("invalid RSS rating")
            data.append({"id": entry["id"]["label"], "attributes": {
                "title": entry["title"]["label"], "review": entry["content"]["label"], "rating": int(rating),
            }})
        except (KeyError, TypeError, ValueError):
            data.append(None)
    links = feed.get("link", [])
    if not isinstance(links, list):
        raise ValueError("RSS links must be a list")
    has_next = any(isinstance(link, dict) and isinstance(link.get("attributes"), dict)
                   and link["attributes"].get("rel") == "next" for link in links)
    return {"data": data, "next": has_next}


class ServerDataParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.found = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("id") == "serialized-server-data" and attributes.get("type") == "application/json":
            self.active = True
            self.found = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def parse_web_page(response):
    parser = ServerDataParser()
    parser.feed(response.text)
    if not parser.found:
        raise ValueError("App Store page is missing serialized-server-data")
    state = json.loads("".join(parser.parts))
    if not isinstance(state, (dict, list)):
        raise ValueError("App Store server state must be an object or list")
    reviews = []

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("$kind") == "Review":
                review_id = obj.get("id")
                if type(review_id) is int and review_id > 0:
                    review_id = str(review_id)
                reviews.append({"id": review_id, "attributes": {
                    "title": obj.get("title"), "review": obj.get("contents"), "rating": obj.get("rating"),
                }})
                return
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(state)
    return {"data": reviews, "next": False}


def fetch_web_reviews(client, app_id, country, deadline):
    def parse_slug(response):
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise ValueError("App lookup returned no app for this storefront")
        item = results[0]
        url = item.get("trackViewUrl")
        if str(item.get("trackId")) != app_id or not isinstance(url, str):
            raise ValueError("App lookup did not return the requested app URL")
        parsed = urlsplit(url)
        match = re.fullmatch(rf"/{country}/app/([^/]+)/id{app_id}", parsed.path)
        if parsed.scheme != "https" or parsed.netloc != "apps.apple.com" or not match:
            raise ValueError("App lookup returned an unexpected storefront URL")
        return match[1]

    slug = fetch_page(client, f"https://itunes.apple.com/lookup?id={app_id}&country={country}", deadline, parse_slug)
    return fetch_page(client, f"https://apps.apple.com/{country}/app/{slug}/id{app_id}", deadline, parse_web_page)


def collect_reviews(client, app_id, country="us", count=100, seed=42, deadline=None):
    country = validate_inputs(app_id, country, count)
    started = time.monotonic()
    catalog_deadline = started + 60 if deadline is None else min(started + 60, started + max(0, deadline - started) / 3)
    result = collect_catalog_reviews(client, app_id, country, count, seed, catalog_deadline)
    metadata = result["metadata"]
    reviews = {review["id"]: review for review in result["reviews"]}
    metadata["sources"] = [{
        "source": "catalog", "added_count": metadata["available_count"],
        "fetched_pages": list(metadata["fetched_pages"]),
        "failed_pages": [{**failure, "source": "catalog"} for failure in metadata["failed_pages"]],
        "invalid_records": metadata["invalid_records"], "duplicate_records": metadata["duplicate_records"],
    }]
    metadata["failed_pages"] = list(metadata["sources"][0]["failed_pages"])
    for index, source in enumerate(("rss", "webpage")):
        if metadata["available_count"] >= count:
            break
        now = time.monotonic()
        source_deadline = now + 60 if deadline is None else min(now + 60, now + max(0, deadline - now) / (2 - index))
        stats = {"source": source, "added_count": 0, "fetched_pages": [], "failed_pages": [], "invalid_records": 0, "duplicate_records": 0}
        metadata["sources"].append(stats)
        for page in range(1, 11 if source == "rss" else 2):
            if page > 1:
                time.sleep(min(0.5, max(0, source_deadline - time.monotonic())))
            try:
                if time.monotonic() >= source_deadline:
                    raise httpx.TimeoutException("Collection deadline exceeded")
                if source == "rss":
                    url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
                    payload = fetch_page(client, url, source_deadline, parse_rss_page)
                else:
                    payload = fetch_web_reviews(client, app_id, country, source_deadline)
            except (httpx.HTTPError, ValueError) as exc:
                stats["failed_pages"].append({"source": source, "page": page, "error": str(exc)})
                break
            stats["fetched_pages"].append(page)
            added = 0
            for entry in payload["data"]:
                review = extract_review(entry)
                if review is None:
                    stats["invalid_records"] += 1
                elif review["id"] in reviews:
                    stats["duplicate_records"] += 1
                else:
                    reviews[review["id"]] = review
                    added += 1
                    if len(reviews) >= count:
                        break
            stats["added_count"] += added
            if len(reviews) >= count or not payload["next"] or not payload["data"]:
                break
        metadata["failed_pages"].extend(stats["failed_pages"])
        metadata["invalid_records"] += stats["invalid_records"]
        metadata["duplicate_records"] += stats["duplicate_records"]
        metadata["available_count"] = len(reviews)
    if len(metadata["sources"]) > 1:
        result["reviews"] = list(reviews.values())
        metadata.update(actual_count=len(result["reviews"]), shortfall=max(0, count - len(result["reviews"])))
        metadata["source"] = "Apple App Store " + " → ".join(item["source"] for item in metadata["sources"])
        metadata["sampling_pool"] = "First unique valid reviews in catalog, RSS, then webpage response order in the requested storefront; not a random sample or all historical reviews"
    return result

