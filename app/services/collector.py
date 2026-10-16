import json
import re
import time
from dataclasses import dataclass, field
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


@dataclass
class SourcePage:
    number: int
    entries: list = field(default_factory=list)
    offset: int | None = None
    error: str | None = None


def catalog_pages(client, app_id, country, deadline, max_pages=10):
    offset = 0
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(0.5 if deadline is None else min(0.5, max(0, deadline - time.monotonic())))
        if deadline is not None and time.monotonic() >= deadline:
            for skipped in range(page, max_pages + 1):
                yield SourcePage(skipped, error="Collection deadline exceeded")
            return
        try:
            payload = fetch_page(client, URL_TEMPLATE.format(country=country, app_id=app_id, offset=offset), deadline)
        except (httpx.HTTPError, ValueError) as exc:
            yield SourcePage(page, offset=offset, error=str(exc))
            if page == 1:
                return
            offset += PAGE_SIZE
            continue
        yield SourcePage(page, payload["data"], offset)
        if not payload["data"] or not payload.get("next"):
            return
        try:
            next_offset = int(parse_qs(urlsplit(payload["next"]).query)["offset"][0])
            if next_offset <= offset:
                raise ValueError("next offset must increase")
        except (ValueError, KeyError) as exc:
            yield SourcePage(page, offset=offset, error=f"Invalid pagination: {exc}")
            return
        offset = next_offset


class ReviewCollection:
    def __init__(self, app_id, country, count, seed, max_pages=10):
        self.app_id = app_id
        self.country = country
        self.count = count
        self.seed = seed
        self.max_pages = max_pages
        self.reviews = {}
        self.sources = []
        self.offsets = []

    @property
    def complete(self):
        return len(self.reviews) >= self.count

    def consume(self, source, pages):
        stats = {"source": source, "added_count": 0, "fetched_pages": [], "failed_pages": [], "invalid_records": 0, "duplicate_records": 0}
        self.sources.append(stats)
        for page in pages:
            if page.error is not None:
                failure = {"source": source, "page": page.number, "error": page.error}
                if page.offset is not None:
                    failure["offset"] = page.offset
                stats["failed_pages"].append(failure)
                continue
            stats["fetched_pages"].append(page.number)
            if page.offset is not None:
                self.offsets.append(page.offset)
            for entry in page.entries:
                review = extract_review(entry)
                if review is None:
                    stats["invalid_records"] += 1
                elif review["id"] in self.reviews:
                    stats["duplicate_records"] += 1
                else:
                    self.reviews[review["id"]] = review
                    stats["added_count"] += 1
                    if self.complete:
                        return

    def report(self):
        fallback = len(self.sources) > 1
        actual = len(self.reviews)
        return {
            "metadata": {
                "app_id": self.app_id, "country": self.country,
                "requested_count": self.count, "available_count": actual, "actual_count": actual,
                "shortfall": max(0, self.count - actual), "seed": self.seed, "selection": "first_unique",
                "catalog_page_limit": self.max_pages,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "source": "Apple App Store " + " → ".join(item["source"] for item in self.sources) if fallback else "Apple App Store catalog reviews JSON",
                "sampling_pool": "First unique valid reviews in catalog, RSS, then webpage response order in the requested storefront; not a random sample or all historical reviews" if fallback else f"First unique valid reviews in Apple catalog response order, stopping at the requested count or {self.max_pages} pages",
                "language": "en-US", "fetched_pages": list(self.sources[0]["fetched_pages"]), "fetched_offsets": self.offsets,
                "failed_pages": [failure for stats in self.sources for failure in stats["failed_pages"]],
                "invalid_records": sum(stats["invalid_records"] for stats in self.sources),
                "duplicate_records": sum(stats["duplicate_records"] for stats in self.sources),
                "sources": self.sources,
            },
            "reviews": list(self.reviews.values()),
        }


def collect_catalog_reviews(client, app_id, country="us", count=100, seed=42, deadline=None):
    collection = ReviewCollection(app_id, validate_inputs(app_id, country, count), count, seed)
    collection.consume("catalog", catalog_pages(client, app_id, collection.country, deadline))
    result = collection.report()
    result["metadata"].pop("sources")
    for failure in result["metadata"]["failed_pages"]:
        failure.pop("source")
    return result


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


def rss_pages(client, app_id, country, deadline):
    for page in range(1, 11):
        if page > 1:
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        try:
            if time.monotonic() >= deadline:
                raise httpx.TimeoutException("Collection deadline exceeded")
            url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
            payload = fetch_page(client, url, deadline, parse_rss_page)
        except (httpx.HTTPError, ValueError) as exc:
            yield SourcePage(page, error=str(exc))
            return
        yield SourcePage(page, payload["data"])
        if not payload["next"] or not payload["data"]:
            return


def web_pages(client, app_id, country, deadline):
    try:
        if time.monotonic() >= deadline:
            raise httpx.TimeoutException("Collection deadline exceeded")
        payload = fetch_web_reviews(client, app_id, country, deadline)
        yield SourcePage(1, payload["data"])
    except (httpx.HTTPError, ValueError) as exc:
        yield SourcePage(1, error=str(exc))


def collect_reviews(client, app_id, country="us", count=100, seed=42, deadline=None, *, max_pages=10):
    if type(max_pages) is not int or not 1 <= max_pages <= 30:
        raise ValueError("catalog page limit must be between 1 and 30")
    collection = ReviewCollection(app_id, validate_inputs(app_id, country, count), count, seed, max_pages)
    adapters = (("catalog", catalog_pages), ("rss", rss_pages), ("webpage", web_pages))
    for index, (source, pages) in enumerate(adapters):
        if collection.complete:
            break
        now = time.monotonic()
        source_deadline = now + 60 if deadline is None else min(now + 60, now + max(0, deadline - now) / (len(adapters) - index))
        iterator = pages(client, app_id, collection.country, source_deadline, max_pages) if source == "catalog" else pages(client, app_id, collection.country, source_deadline)
        collection.consume(source, iterator)
    return collection.report()
