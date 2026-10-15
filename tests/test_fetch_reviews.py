import json
import io
import random
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.fetch_reviews import collect_reviews, extract_review, fetch_page, main, validate_inputs


FIXTURES = Path(__file__).parent / "fixtures"


class FetchReviewsTests(unittest.TestCase):
    @patch("app.services.collector.time.sleep")
    def test_live_fixture_sampling(self, sleep):
        fixture = json.loads((FIXTURES / "apple_catalog_reviews.json").read_text())
        requested_pages = []

        def respond(request):
            self.assertEqual(request.url.host, "apps.apple.com")
            self.assertEqual(request.url.params["l"], "en-US")
            self.assertEqual(request.url.params["platform"], "web")
            self.assertEqual(request.url.params["additionalPlatforms"], "appletv,ipad,iphone,mac")
            page = int(request.url.params["offset"]) // 10 + 1
            requested_pages.append(page)
            return httpx.Response(200, json=fixture["pages"][str((page - 1) * 10)])

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            first = collect_reviews(client, fixture["app_id"])
            second = collect_reviews(client, fixture["app_id"])
            different = collect_reviews(client, fixture["app_id"], seed=43)
        self.assertEqual(requested_pages, list(range(1, 11)) * 3)
        self.assertEqual(first["reviews"], second["reviews"])
        self.assertNotEqual(first["reviews"], different["reviews"])
        ids = [review["id"] for review in first["reviews"]]
        self.assertEqual(ids, fixture["expected_seed_42_ids"])
        self.assertEqual(len(ids), 100)
        self.assertEqual(len(set(ids)), 100)
        self.assertTrue(all(1 <= review["rating"] <= 5 for review in first["reviews"]))
        self.assertEqual(first["metadata"]["shortfall"], 0)
        self.assertEqual(first["metadata"]["failed_pages"], [])
        self.assertEqual(first["metadata"]["fetched_offsets"], list(range(0, 100, 10)))
        pool = {}
        for page in fixture["pages"].values():
            for entry in page["data"]:
                review = extract_review(entry)
                if review is not None:
                    pool.setdefault(review["id"], review)
        self.assertEqual(first["reviews"], random.Random(42).sample(list(pool.values()), 100))

    def test_validation(self):
        self.assertEqual(validate_inputs("389801252", "US", 100), "us")
        for app_id, country, count in [("abc", "us", 100), ("0", "us", 100), ("-1", "us", 100), ("１２３", "us", 100), ("123", "xx", 100), ("123", "usa", 100), ("123", "us", 0)]:
            with self.subTest(app_id=app_id, country=country, count=count):
                with self.assertRaises(ValueError):
                    validate_inputs(app_id, country, count)

    @patch("app.services.collector.time.sleep")
    def test_shortfall_and_invalid_records(self, sleep):
        entry = {"id": "1", "attributes": {"title": "Title", "review": "Text", "rating": 5}}
        invalid = [None, {}, {**entry, "attributes": {**entry["attributes"], "rating": 6}}, {**entry, "attributes": {**entry["attributes"], "review": " "}}, {**entry, "id": None}]
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [entry, entry, *invalid], "next": f"/v1/catalog/us/apps/123/reviews?offset={int(request.url.params['offset']) + 10}"}))
        with httpx.Client(transport=transport) as client:
            result = collect_reviews(client, "123")
        self.assertEqual(result["reviews"], [{"id": "1", "title": "Title", "text": "Text", "rating": 5}])
        self.assertEqual(result["metadata"]["actual_count"], 1)
        self.assertEqual(result["metadata"]["shortfall"], 99)
        self.assertEqual(result["metadata"]["duplicate_records"], 19)
        self.assertEqual(result["metadata"]["invalid_records"], 50)

    def test_cli_saves_shortfall(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []})))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested/reviews.json"
            stderr = io.StringIO()
            with patch("sys.argv", ["fetch_reviews.py", "123", "--output", str(output)]), patch("scripts.fetch_reviews.httpx.Client", return_value=client), redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                status = main()
            saved = json.loads(output.read_text())
        self.assertEqual(status, 0)
        self.assertEqual(saved["reviews"], [])
        self.assertEqual(saved["metadata"]["shortfall"], 100)
        self.assertIn("Shortfall: 100 reviews", stderr.getvalue())

    @patch("app.services.collector.time.sleep")
    def test_partial_page_failure(self, sleep):
        fixture = json.loads((FIXTURES / "apple_catalog_reviews.json").read_text())

        def respond(request):
            offset = request.url.params["offset"]
            if offset == "0":
                return httpx.Response(404)
            return httpx.Response(200, json=fixture["pages"][offset])

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = collect_reviews(client, fixture["app_id"])
        self.assertEqual(len(result["reviews"]), 90)
        self.assertEqual(result["metadata"]["fetched_pages"], list(range(2, 11)))
        self.assertEqual([page["page"] for page in result["metadata"]["failed_pages"]], [1])
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(0.5)] * 9)

    @patch("app.services.collector.time.sleep")
    def test_retry_then_success(self, sleep):
        responses = [httpx.Response(429), httpx.Response(503), httpx.Response(200, json={"data": []})]
        with httpx.Client(transport=httpx.MockTransport(lambda request: responses.pop(0))) as client:
            self.assertEqual(fetch_page(client, "https://example.com"), {"data": []})
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(1), unittest.mock.call(2)])

    @patch("app.services.collector.time.sleep")
    def test_bounded_retries_and_failed_pages(self, sleep):
        requests = []

        def fail(request):
            requests.append(request)
            return httpx.Response(503)

        with httpx.Client(transport=httpx.MockTransport(fail)) as client:
            result = collect_reviews(client, "123")
        self.assertEqual(len(requests), 30)
        self.assertEqual(len(result["metadata"]["failed_pages"]), 10)
        self.assertEqual(result["metadata"]["fetched_pages"], [])
        self.assertEqual(result["metadata"]["shortfall"], 100)
        self.assertEqual(result["reviews"], [])

    @patch("app.services.collector.time.sleep")
    def test_non_retryable_status(self, sleep):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                fetch_page(client, "https://example.com")
        sleep.assert_not_called()

    @patch("app.services.collector.time.sleep")
    def test_timeout_and_malformed_json(self, sleep):
        requests = []

        def respond(request):
            requests.append(request)
            self.assertEqual(request.extensions["timeout"]["read"], 20.0)
            if len(requests) == 1:
                raise httpx.ReadTimeout("Timed out", request=request)
            if len(requests) == 2:
                return httpx.Response(200, text="not JSON")
            return httpx.Response(200, json={"data": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(fetch_page(client, "https://example.com"), {"data": []})
        self.assertEqual(len(requests), 3)

    @patch("app.services.collector.time.sleep")
    def test_follows_next_offset_and_stops_at_last_page(self, sleep):
        offsets = []

        def respond(request):
            offset = int(request.url.params["offset"])
            offsets.append(offset)
            entry = {"id": str(offset), "attributes": {"title": "Title", "review": "Text", "rating": 2}}
            payload = {"data": [entry]}
            if offset == 0:
                payload["next"] = "/v1/catalog/us/apps/123/reviews?l=en-US&offset=25"
            return httpx.Response(200, json=payload)

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = collect_reviews(client, "123")
        self.assertEqual(offsets, [0, 25])
        self.assertEqual(result["metadata"]["actual_count"], 2)
        self.assertEqual(result["metadata"]["shortfall"], 98)
        self.assertEqual(result["metadata"]["failed_pages"], [])

    def test_empty_catalog_page_stops_collection(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"data": []})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            result = collect_reviews(client, "123")
        self.assertEqual(len(requests), 1)
        self.assertEqual(result["reviews"], [])
        self.assertEqual(result["metadata"]["shortfall"], 100)

    def test_invalid_next_offset_reports_partial_collection(self):
        entry = {"id": "1", "attributes": {"title": "Title", "review": "Text", "rating": 2}}
        for next_link in ("?offset=0", "?offset=-1", "?offset=abc", "?other=10"):
            with self.subTest(next_link=next_link):
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": [entry], "next": next_link}))) as client:
                    result = collect_reviews(client, "123")
                self.assertEqual(result["metadata"]["actual_count"], 1)
                self.assertIn("Invalid pagination", result["metadata"]["failed_pages"][0]["error"])

    @patch("app.services.collector.time.sleep")
    def test_malformed_catalog_payload_is_not_an_empty_result(self, sleep):
        for payload in ({"feed": {}}, {"errors": []}, {"data": {}}, {"data": [], "next": 10}):
            with self.subTest(payload=payload):
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
                    with self.assertRaises(ValueError):
                        fetch_page(client, "https://example.com")

    def test_catalog_ratings_must_be_integers(self):
        for rating in (True, "5", 0, 6, 2.5, None):
            with self.subTest(rating=rating):
                entry = {"id": "1", "attributes": {"title": "Title", "review": "Text", "rating": rating}}
                self.assertIsNone(extract_review(entry))


if __name__ == "__main__":
    unittest.main()
