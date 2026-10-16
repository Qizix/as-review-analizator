import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.analyze_sentiment import MODEL, analyze_sentiment, main


class AnalyzeSentimentTests(unittest.TestCase):
    @patch("app.services.sentiment.pipeline")
    def test_predictions_preserve_review_order_and_collection_metadata(self, pipeline):
        data = {
            "metadata": {"app_id": "123", "seed": 42},
            "reviews": [
                {"id": "a", "title": "Great", "text": "I love this app", "rating": 5},
                {"id": "b", "title": "Bad", "text": "This app keeps crashing", "rating": 1},
                {"id": "c", "title": "Okay", "text": "An app with a menu", "rating": 3},
            ],
        }
        original = copy.deepcopy(data)
        pipeline.return_value = Mock(return_value=[
            {"label": "Positive", "score": 0.95},
            {"label": "Negative", "score": 0.9},
            {"label": "Neutral", "score": 0.8},
        ])
        result = analyze_sentiment(data)
        from app.settings import SENTIMENT_REVISION
        pipeline.assert_called_once_with(task="text-classification", model=MODEL, revision=SENTIMENT_REVISION, device="cpu")
        pipeline.return_value.assert_called_once_with([review["text"] for review in data["reviews"]], batch_size=1, truncation=True, max_length=512)
        self.assertEqual(data, original)
        self.assertEqual([review["id"] for review in result["reviews"]], ["a", "b", "c"])
        self.assertEqual([review["sentiment"]["label"] for review in result["reviews"]], ["positive", "negative", "neutral"])
        self.assertEqual(result["reviews"][0]["sentiment"]["score"], 0.95)
        for before, after in zip(data["reviews"], result["reviews"]):
            self.assertEqual(before, {key: value for key, value in after.items() if key != "sentiment"})
        self.assertEqual(result["metadata"]["app_id"], "123")
        self.assertEqual(result["metadata"]["sentiment_analysis"]["batch_size"], 1)
        self.assertEqual(result["metadata"]["sentiment_analysis"]["analyzed_count"], 3)

    @patch("app.services.sentiment.pipeline")
    def test_invalid_inputs_fail_before_loading_model(self, pipeline):
        for data in [[], {}, {"reviews": {}}, {"reviews": [None]}, {"reviews": [{"text": " "}]}, {"reviews": [{"text": 3}]}, {"metadata": None, "reviews": []}]:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    analyze_sentiment(data)
        pipeline.assert_not_called()

    @patch("app.services.sentiment.pipeline")
    def test_empty_collection_does_not_load_model(self, pipeline):
        result = analyze_sentiment({"metadata": {"shortfall": 100}, "reviews": []})
        self.assertEqual(result["reviews"], [])
        self.assertEqual(result["metadata"]["shortfall"], 100)
        self.assertEqual(result["metadata"]["sentiment_analysis"]["analyzed_count"], 0)
        pipeline.assert_not_called()

    @patch("app.services.sentiment.pipeline")
    def test_invalid_predictions_fail(self, pipeline):
        for predictions in [[], [{"label": "unknown", "score": 0.5}], [{"label": "positive", "score": float("nan")}]]:
            with self.subTest(predictions=predictions):
                pipeline.return_value.return_value = predictions
                with self.assertRaises(ValueError):
                    analyze_sentiment({"reviews": [{"text": "Review"}]})

    @patch("app.services.sentiment.pipeline")
    def test_cli_default_output_preserves_input(self, pipeline):
        pipeline.return_value.return_value = [{"label": "positive", "score": 0.9}]
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "reviews.json"
            content = json.dumps({"reviews": [{"id": "1", "text": "Great app"}]})
            input_path.write_text(content)
            with patch("sys.argv", ["analyze_sentiment.py", str(input_path)]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            saved = json.loads(input_path.with_name("reviews_sentiment.json").read_text())
            self.assertEqual(input_path.read_text(), content)
        self.assertEqual(saved["reviews"][0]["sentiment"], {"label": "positive", "score": 0.9})

    @patch("app.services.sentiment.pipeline")
    def test_cli_malformed_json_does_not_write_output(self, pipeline):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "reviews.json"
            input_path.write_text("invalid JSON")
            with patch("sys.argv", ["analyze_sentiment.py", str(input_path)]), redirect_stderr(io.StringIO()):
                self.assertEqual(main(), 1)
            self.assertFalse(input_path.with_name("reviews_sentiment.json").exists())
        pipeline.assert_not_called()


if __name__ == "__main__":
    unittest.main()
