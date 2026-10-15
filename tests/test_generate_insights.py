import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google import genai

from scripts.generate_insights import (
    MODEL, PROMPT_PATH, ModelReport, build_report, main,
    prepare_reviews, preprocess_text, render_report, request_insights,
)


def collection():
    return {"metadata": {"app_id": "123", "seed": 42}, "reviews": [
        {"id": "n1", "title": "Broken", "text": "Does not open 😡", "rating": 1, "sentiment": {"label": "negative", "score": 0.9}},
        {"id": "n2", "title": "Broken", "text": "Cannot open the app and navigation labels are confusing", "rating": 1, "sentiment": "negative"},
        {"id": "p1", "title": "Great", "text": "I love it 😀", "rating": 5, "sentiment": "positive"},
        {"id": "u1", "title": "Okay", "text": "Has a menu", "rating": 3, "sentiment": "neutral"},
    ]}


def response(evidence_ids=None):
    return ModelReport.model_validate({"issues": [{
        "category": "Startup failures", "action": "Investigate and fix startup failures.",
        "priority": "high", "evidence_ids": ["n1", "n2"] if evidence_ids is None else evidence_ids,
    }]})


class GenerateInsightsTests(unittest.TestCase):
    def test_grouping_preserves_all_sentiments_without_repeating_labels(self):
        data = collection()
        original = copy.deepcopy(data)
        groups = prepare_reviews(data)
        self.assertEqual([len(groups[label]) for label in ("negative", "positive", "neutral")], [2, 1, 1])
        for reviews in groups.values():
            for review in reviews:
                self.assertEqual(set(review), {"id", "title", "text", "rating"})
        self.assertIn("not open", groups["negative"][0]["text"])
        self.assertFalse(any(character in groups["negative"][0]["text"] for character in "😡"))
        self.assertIn("[", groups["positive"][0]["text"])
        self.assertEqual(data, original)

    def test_preprocessing_marks_truncation_and_preserves_both_ends(self):
        data = collection()
        data["reviews"][0]["title"] = "START " + "a" * 300 + " END"
        data["reviews"][0]["text"] = "START " + "a" * 6000 + " NOT WORKING END"
        review = prepare_reviews(data)["negative"][0]
        self.assertEqual(review["truncated_fields"], ["title", "text"])
        self.assertEqual(len(review["title"]), 200)
        self.assertEqual(len(review["text"]), 4000)
        self.assertTrue(review["text"].startswith("START"))
        self.assertTrue(review["text"].endswith("NOT WORKING END"))
        self.assertIn("[truncated]", review["text"])
        self.assertEqual(preprocess_text("not\n working\t now", 4000), ("not working now", False))

    def test_invalid_reviews_fail_before_api(self):
        invalid = [[], {}, {"reviews": {}}, {"metadata": [], "reviews": []}]
        for changes in [{"id": ""}, {"title": None}, {"text": " "}, {"rating": True}, {"rating": 6}, {"sentiment": "unknown"}]:
            data = collection()
            data["reviews"][0].update(changes)
            invalid.append(data)
        duplicate = collection()
        duplicate["reviews"].append(duplicate["reviews"][0])
        invalid.append(duplicate)
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    prepare_reviews(data)

    @patch("app.services.insights.genai.Client")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}, clear=True)
    def test_request_passes_compact_groups_system_prompt_and_schema(self, client):
        groups = prepare_reviews(collection())
        fake = client.return_value.__enter__.return_value
        fake.models.generate_content.return_value = SimpleNamespace(text=response().model_dump_json())
        result = request_insights(groups, "System instructions")
        kwargs = fake.models.generate_content.call_args.kwargs
        self.assertEqual(kwargs["model"], MODEL)
        self.assertEqual(json.loads(kwargs["contents"]), groups)
        self.assertNotIn('"sentiment":', kwargs["contents"])
        self.assertEqual(kwargs["config"].system_instruction, "System instructions")
        self.assertEqual(kwargs["config"].response_mime_type, "application/json")
        schema = kwargs["config"].response_json_schema
        self.assertEqual(schema["$defs"]["Issue"]["properties"]["evidence_ids"]["items"], {"type": "string", "enum": ["n1", "n2"]})
        self.assertIsNone(kwargs["config"].response_schema)
        self.assertEqual(client.call_args.kwargs["http_options"].timeout, 60000)
        self.assertEqual(client.call_args.kwargs["http_options"].retry_options.attempts, 1)
        self.assertEqual(result, response())

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_key_and_empty_collection(self):
        groups = prepare_reviews(collection())
        with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
            request_insights(groups, "Prompt")
        self.assertEqual(request_insights(prepare_reviews({"reviews": []}), "Prompt").issues, [])

    @patch("app.services.insights.genai.Client")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}, clear=True)
    def test_token_usage_accumulates_across_generation_and_repair(self, client):
        fake = client.return_value.__enter__.return_value
        fake.models.generate_content.return_value = SimpleNamespace(
            text=response().model_dump_json(),
            usage_metadata=SimpleNamespace(model_dump=lambda **kwargs: {"prompt_token_count": 100, "candidates_token_count": 20, "total_token_count": 120}),
        )
        usage = {}
        for prompt in ["Prompt", "Correction"]:
            request_insights(prepare_reviews(collection()), prompt, usage)
        self.assertEqual(usage, {"prompt_token_count": 200, "candidates_token_count": 40, "total_token_count": 240})

    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}, clear=True)
    def test_real_sdk_serialization_and_transient_retry(self):
        requests = []
        client_type = genai.Client

        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(503, json={"error": {"code": 503, "message": "Unavailable", "status": "UNAVAILABLE"}})
            return httpx.Response(200, json={"candidates": [{
                "content": {"role": "model", "parts": [{"text": response().model_dump_json()}]},
                "finishReason": "STOP",
            }]})

        def make_client(**kwargs):
            kwargs["http_options"].httpx_client = httpx.Client(transport=httpx.MockTransport(handle), event_hooks=kwargs["http_options"].httpx_client.event_hooks)
            return client_type(**kwargs)

        with patch("app.services.insights.genai.Client", side_effect=make_client):
            report = request_insights(prepare_reviews(collection()), "System prompt")
        self.assertEqual(report, response())
        self.assertEqual(len(requests), 2)
        payload = json.loads(requests[-1].content)
        self.assertIn(MODEL, str(requests[-1].url))
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "System prompt")
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        schema = payload["generationConfig"]["responseJsonSchema"]
        self.assertNotIn("responseSchema", payload["generationConfig"])
        self.assertEqual(schema["$defs"]["Issue"]["properties"]["evidence_ids"]["items"], {"type": "string", "enum": ["n1", "n2"]})
        self.assertIs(schema["additionalProperties"], False)
        self.assertIs(schema["$defs"]["Issue"]["additionalProperties"], False)
        self.assertNotIn("additional_properties", json.dumps(schema))
        groups = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(set(groups), {"negative", "positive", "neutral"})

    @patch("app.services.insights.genai.Client")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}, clear=True)
    def test_evidence_schema_is_request_specific_and_keeps_context(self, client):
        fake = client.return_value.__enter__.return_value
        fake.models.generate_content.return_value = SimpleNamespace(text='{"issues": []}')
        data = collection()
        data["reviews"][0]["id"] = "14031104092"
        first_groups = prepare_reviews(data)
        request_insights(first_groups, "Prompt")
        first_schema = fake.models.generate_content.call_args.kwargs["config"].response_json_schema
        self.assertEqual(first_schema["$defs"]["Issue"]["properties"]["evidence_ids"]["items"]["enum"], ["14031104092", "n2"])
        context = prepare_reviews({"reviews": data["reviews"][2:]})
        request_insights(context, "Prompt")
        kwargs = fake.models.generate_content.call_args.kwargs
        self.assertEqual(json.loads(kwargs["contents"]), context)
        self.assertEqual(kwargs["config"].response_json_schema["properties"]["issues"]["maxItems"], 0)
        request_insights(prepare_reviews(collection()), "Prompt")
        latest = fake.models.generate_content.call_args.kwargs["config"].response_json_schema
        self.assertNotIn("maxItems", latest["properties"]["issues"])
        self.assertEqual(latest["$defs"]["Issue"]["properties"]["evidence_ids"]["items"]["enum"], ["n1", "n2"])
        self.assertNotIn("enum", ModelReport.model_json_schema()["$defs"]["Issue"]["properties"]["evidence_ids"]["items"])

    @patch("app.services.insights.genai.Client")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "test-only"}, clear=True)
    def test_missing_or_malformed_model_text_fails(self, client):
        fake = client.return_value.__enter__.return_value
        for text in [None, "not JSON", '{"issues": [{"category": "Missing fields"}]}']:
            with self.subTest(text=text):
                fake.models.generate_content.return_value = SimpleNamespace(text=text)
                with self.assertRaises(ValueError):
                    request_insights(prepare_reviews(collection()), "Prompt")

    def test_local_validation_rejects_extra_fields(self):
        for data in [{"issues": [], "unexpected": True}, {"issues": [{
            "category": "Startup", "action": "Investigate startup failures.",
            "priority": "high", "evidence_ids": ["n1"], "unexpected": True,
        }]}]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    ModelReport.model_validate(data)

    def test_counts_priorities_recurrence_and_metadata(self):
        data = collection()
        groups = prepare_reviews(data)
        model = response()
        model.issues.extend(ModelReport.model_validate({"issues": [{
            "category": "Navigation", "action": "Clarify navigation labels.",
            "priority": "low", "evidence_ids": ["n2"],
        }]}).issues)
        report = build_report(data, groups, model, "Prompt")
        self.assertEqual(report["summary"], {"total_reviews": 4, "sentiments": {"negative": 2, "positive": 1, "neutral": 1}, "categorized_negative_reviews": 2, "uncategorized_negative_reviews": 0, "issue_count": 2})
        self.assertEqual(report["insights"]["high"]["repeated"][0]["review_count"], 2)
        self.assertEqual(report["insights"]["low"]["single"][0]["review_count"], 1)
        self.assertEqual(report["insights"]["low"]["single"][0]["negative_review_fraction"], 0.5)
        self.assertEqual(report["metadata"]["app_id"], "123")
        self.assertEqual(report["metadata"]["seed"], 42)
        markdown = render_report(report)
        self.assertIn("High priority — repeated reports", markdown)
        self.assertIn("2 reviews", markdown)

    def test_rejects_fabricated_positive_neutral_and_duplicate_evidence(self):
        data = collection()
        for ids in [["unknown"], ["p1"], ["u1"], ["n1", "n1"]]:
            with self.subTest(ids=ids):
                with self.assertRaises(ValueError):
                    build_report(data, prepare_reviews(data), response(ids), "Prompt")
        with self.assertRaises(ValueError):
            response([])
        duplicate = response()
        duplicate.issues.append(duplicate.issues[0])
        with self.assertRaises(ValueError):
            build_report(data, prepare_reviews(data), duplicate, "Prompt")

    def test_empty_insights_are_valid_with_or_without_negative_reviews(self):
        for data in [collection(), {"reviews": []}, {"reviews": collection()["reviews"][2:]}]:
            groups = prepare_reviews(data)
            report = build_report(data, groups, ModelReport(issues=[]), "Prompt")
            self.assertEqual(report["summary"]["issue_count"], 0)
            self.assertTrue(all(not items for buckets in report["insights"].values() for items in buckets.values()))
            self.assertIn("No supported actionable complaints", render_report(report))
        positive_only = {"reviews": collection()["reviews"][2:]}
        with self.assertRaises(ValueError):
            build_report(positive_only, prepare_reviews(positive_only), response(["p1"]), "Prompt")

    @patch("app.services.insights.request_insights", return_value=response())
    def test_cli_writes_json_and_markdown_without_modifying_input(self, request):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reviews_sentiment.json"
            content = json.dumps(collection())
            source.write_text(content)
            with patch("sys.argv", ["generate_insights.py", str(source)]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            output = source.with_name("reviews_sentiment_insights.json")
            report = json.loads(output.read_text())
            self.assertTrue(output.with_suffix(".md").exists())
            self.assertEqual(source.read_text(), content)
        self.assertEqual(report["summary"]["issue_count"], 1)
        self.assertEqual(request.call_args.args[1], PROMPT_PATH.read_text())

    @patch("app.services.insights.request_insights", return_value=response(["unknown"]))
    def test_invalid_model_output_does_not_write_reports(self, request):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reviews.json"
            source.write_text(json.dumps(collection()))
            with patch("sys.argv", ["generate_insights.py", str(source)]), redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 1)
            self.assertFalse(source.with_name("reviews_insights.json").exists())
            self.assertFalse(source.with_name("reviews_insights.md").exists())


if __name__ == "__main__":
    unittest.main()
