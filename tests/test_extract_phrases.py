import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.extract_phrases import extract_phrases
from scripts.extract_phrases import main


def collection(*texts, label="negative"):
    return {"metadata": {"app_id": "123"}, "reviews": [
        {"id": str(index), "text": text, "sentiment": {"label": label}}
        for index, text in enumerate(texts)
    ]}


class ExtractPhrasesTests(unittest.TestCase):
    def test_document_frequency_not_occurrence_frequency(self):
        data = collection("Slow loading slow loading slow loading", "slow loading", "crashes constantly")
        data["reviews"].append({"text": "crashes constantly", "sentiment": {"label": "positive"}})
        original = copy.deepcopy(data)
        result = extract_phrases(data)
        phrases = {item["phrase"]: item for item in result["phrases"]}
        self.assertEqual(phrases["slow loading"]["document_frequency"], 2)
        self.assertEqual(phrases["slow loading"]["negative_review_fraction"], 2 / 3)
        self.assertEqual(phrases["crashes constantly"]["document_frequency"], 1)
        self.assertEqual(phrases["crashes constantly"]["negative_review_fraction"], 1 / 3)
        self.assertIn("loading slow loading", phrases)
        self.assertEqual(result["metadata"]["app_id"], "123")
        self.assertEqual(result["metadata"]["phrase_extraction"]["negative_review_count"], 3)
        self.assertEqual(data, original)
        self.assertEqual(result["phrases"], sorted(result["phrases"], key=lambda item: (-item["document_frequency"], item["phrase"])))

    def test_filtering_preserves_negation_and_phrase_structure(self):
        result = extract_phrases(collection("The app is not working never works without ads", "I don't like it and can’t login please"))
        phrases = {item["phrase"] for item in result["phrases"]}
        for phrase in ["not", "not working", "is not working", "never works", "without ads", "don't like", "can’t login"]:
            self.assertIn(phrase, phrases)
        for phrase in ["the", "app", "the app", "the app is", "and", "it", "please"]:
            self.assertNotIn(phrase, phrases)
        self.assertNotIn("app working", phrases)
        self.assertNotIn("not working works", phrases)
        self.assertGreater(result["metadata"]["phrase_extraction"]["filtered_count"], 0)

    def test_empty_and_tokenless_negative_reviews(self):
        for data in [collection(), collection("Great app", label="positive"), collection("😀 !!!")]:
            with self.subTest(data=data):
                result = extract_phrases(data)
                self.assertEqual(result["phrases"], [])
                self.assertEqual(result["metadata"]["phrase_extraction"]["candidate_count"], 0)
        result = extract_phrases(collection("the app", "!!!"))
        self.assertEqual(result["phrases"], [])
        self.assertEqual(result["metadata"]["phrase_extraction"]["negative_review_count"], 2)
        self.assertEqual(result["metadata"]["phrase_extraction"]["filtered_count"], 3)

    def test_invalid_or_unclassified_reviews_fail(self):
        for data in [[], {}, {"reviews": {}}, {"metadata": None, "reviews": []}, {"reviews": [None]}, {"reviews": [{"text": "Review"}]}, {"reviews": [{"text": "Review", "sentiment": {"label": "unknown"}}]}, {"reviews": [{"text": " ", "sentiment": {"label": "negative"}}]}]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    extract_phrases(data)

    def test_cli_saves_default_output_without_changing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "reviews_sentiment.json"
            content = json.dumps(collection("Slow loading", "Slow loading"))
            input_path.write_text(content)
            with patch("sys.argv", ["extract_phrases.py", str(input_path)]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            result = json.loads(input_path.with_name("reviews_sentiment_phrases.json").read_text())
            self.assertEqual(input_path.read_text(), content)
        self.assertEqual(result["phrases"][0]["document_frequency"], 2)

    def test_cli_rejects_overwriting_input(self):
        with patch("sys.argv", ["extract_phrases.py", "reviews.json", "--output", "reviews.json"]), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
