import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from app.services.collector import collect_reviews, extract_review, parse_rss_page, parse_web_page


def catalog_entry(review_id):
    return {"id": str(review_id), "attributes": {"title": "Title", "review": "Original text\nwith lines", "rating": 2}}


def rss_entry(review_id):
    return {"id": {"label": str(review_id)}, "title": {"label": "Title"},
            "content": {"label": "Original text\nwith lines"}, "im:rating": {"label": "2"}}


def webpage(entries):
    return httpx.Response(200, text='<script data-extra="yes" id="serialized-server-data" type="application/json">'
                          + json.dumps({"data": [{"nested": entries}]}) + '</script>')


def lookup(country="us", url=None):
    return httpx.Response(200, json={"results": [{"trackId": 123, "trackViewUrl": url or f"https://apps.apple.com/{country}/app/example/id123?l=en"}]})


def test_catalog_success_skips_all_fallbacks():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [None, catalog_entry(1), catalog_entry(1), catalog_entry(2), catalog_entry(3)], "next": "?offset=10"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123", count=2)
    assert len(requests) == 1
    assert [review["id"] for review in result["reviews"]] == ["1", "2"]
    assert result["metadata"]["available_count"] == 2
    assert result["metadata"]["invalid_records"] == 1
    assert result["metadata"]["duplicate_records"] == 1
    assert [source["source"] for source in result["metadata"]["sources"]] == ["catalog"]
    assert result["metadata"]["failed_pages"] == []


@patch("app.services.collector.time.sleep")
def test_rss_recovers_from_catalog_failure(sleep):
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "apps.apple.com":
            return httpx.Response(503)
        return httpx.Response(200, json={"feed": {"entry": [rss_entry(1), rss_entry(2)]}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123", count=2)
    assert len(requests) == 4
    assert result["metadata"]["shortfall"] == 0
    assert result["metadata"]["failed_pages"][0]["source"] == "catalog"
    assert [source["source"] for source in result["metadata"]["sources"]] == ["catalog", "rss"]


@patch("app.services.collector.time.sleep")
def test_partial_results_merge_deduplicate_and_follow_rss_pages(sleep):
    requests = []

    def respond(request):
        requests.append(request)
        if "/api/" in request.url.path:
            return httpx.Response(200, json={"data": [catalog_entry(1)]})
        if "page=1" in request.url.path:
            return httpx.Response(200, json={"feed": {"entry": rss_entry(1), "link": [{"attributes": {"rel": "next", "href": "https://untrusted.example/"}}]}})
        assert "page=2" in request.url.path
        assert request.url.host == "itunes.apple.com"
        return httpx.Response(200, json={"feed": {"entry": [rss_entry(2), rss_entry(3), rss_entry(4)]}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123", count=3)
    assert len(requests) == 3
    assert [review["id"] for review in result["reviews"]] == ["1", "2", "3"]
    assert result["metadata"]["available_count"] == 3
    assert result["metadata"]["duplicate_records"] == 1
    assert result["metadata"]["sources"][1]["fetched_pages"] == [1, 2]


def test_webpage_is_last_fallback_and_keeps_requested_country():
    requests = []

    def respond(request):
        requests.append(request)
        if "/api/" in request.url.path:
            assert "/ua/" in request.url.path
            return httpx.Response(200, json={"data": [catalog_entry(1)]})
        if "/rss/" in request.url.path:
            assert "/ua/" in request.url.path
            return httpx.Response(200, json={"feed": {}})
        if request.url.path == "/lookup":
            assert request.url.params["country"] == "ua"
            return lookup("ua")
        assert str(request.url) == "https://apps.apple.com/ua/app/example/id123"
        return webpage([{"$kind": "Review", "id": review_id, "title": "Title", "contents": "Original text\nwith lines", "rating": 2} for review_id in (1, 2, 3, 4)])

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        first = collect_reviews(client, "123", country="UA", count=3)
        second = collect_reviews(client, "123", country="ua", count=3)
    assert first["reviews"] == second["reviews"]
    assert len(first["reviews"]) == 3
    assert [review["id"] for review in first["reviews"]] == ["1", "2", "3"]
    assert first["metadata"]["duplicate_records"] == 1
    assert first["metadata"]["sources"][2]["added_count"] == 2
    assert first["metadata"]["failed_pages"] == []
    assert len(requests) == 8
    sources = first["metadata"]["sources"]
    assert sum(source["added_count"] for source in sources) == len(first["reviews"])
    for field in ("invalid_records", "duplicate_records"):
        assert first["metadata"][field] == sum(source[field] for source in sources)
    assert first["metadata"]["fetched_pages"] == sources[0]["fetched_pages"]


@pytest.mark.parametrize("url", ["https://evil.example/us/app/example/id123", "https://apps.apple.com/gb/app/example/id123", "https://apps.apple.com/us/app/example/id456"])
@patch("app.services.collector.time.sleep")
def test_unexpected_lookup_url_is_never_fetched(sleep, url):
    requests = []

    def respond(request):
        requests.append(request)
        if "/api/" in request.url.path:
            return httpx.Response(200, json={"data": []})
        if "/rss/" in request.url.path:
            return httpx.Response(200, json={"feed": {}})
        assert request.url.path == "/lookup"
        return lookup(url=url)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = collect_reviews(client, "123")
    assert len(requests) == 5
    assert result["metadata"]["failed_pages"][0]["source"] == "webpage"


@patch("app.services.collector.time.sleep")
def test_all_sources_fail_without_fabricating_reviews(sleep):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        result = collect_reviews(client, "123")
    assert result["reviews"] == []
    assert result["metadata"]["shortfall"] == 100
    assert [failure["source"] for failure in result["metadata"]["failed_pages"]] == ["catalog", "rss", "webpage"]


def test_expired_deadline_makes_no_requests():
    def unexpected(request):
        pytest.fail("Request sent after deadline")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        result = collect_reviews(client, "123", deadline=time.monotonic() - 1)
    assert result["reviews"] == []
    assert {failure["source"] for failure in result["metadata"]["failed_pages"]} == {"catalog", "rss", "webpage"}


def test_rss_real_fixture_and_invalid_entries():
    fixture = json.loads((Path(__file__).parent / "fixtures/apple_reviews.json").read_text())
    parsed = parse_rss_page(httpx.Response(200, json=fixture["pages"]["1"]))
    assert len(parsed["data"]) == 50
    assert all(extract_review(entry) is not None for entry in parsed["data"])
    invalid = parse_rss_page(httpx.Response(200, json={"feed": {"entry": [None, {}, {**rss_entry(1), "im:rating": {"label": "6"}}]}}))
    assert invalid["data"] == [None, None, None]


@pytest.mark.parametrize("rating", [True, "5", 6, None])
def test_webpage_invalid_rating_is_not_coerced(rating):
    parsed = parse_web_page(webpage([{"$kind": "Review", "id": "1", "title": "Title", "contents": "Text", "rating": rating}]))
    assert extract_review(parsed["data"][0]) is None


@pytest.mark.parametrize("response", [httpx.Response(200, text="<html>No server state</html>"),
    httpx.Response(200, text='<script type="application/json" id="serialized-server-data">invalid</script>')])
def test_webpage_schema_changes_are_errors(response):
    with pytest.raises(ValueError):
        parse_web_page(response)
