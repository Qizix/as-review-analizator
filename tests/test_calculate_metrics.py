import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.calculate_metrics import calculate_metrics, main


def inputs():
    reviews = {"metadata": {"app_id": "123"}, "reviews": [
        {"id": "a", "rating": 5, "sentiment": {"label": "negative"}},
        {"id": "b", "rating": 1, "sentiment": "negative"},
        {"id": "c", "rating": 3, "sentiment": "positive"},
        {"id": "d", "rating": 3, "sentiment": "neutral"},
        {"id": "e", "rating": None, "sentiment": "negative"},
        {"id": "f", "rating": 4},
    ]}
    report = {"metadata": {"app_id": "123"}, "insights": {
        "high": {"repeated": [{"category": "Crashes", "evidence_ids": ["a", "a", "b"], "review_count": 999}], "single": []},
        "medium": {"repeated": [], "single": [{"category": "Navigation", "evidence_ids": ["b"]}]},
        "low": {"repeated": [], "single": []},
    }}
    return reviews, report


class CalculateMetricsTests(unittest.TestCase):
    def test_independent_denominators_and_unique_issue_counts(self):
        data, insights = inputs()
        original = copy.deepcopy((data, insights))
        result = calculate_metrics(data, insights)
        self.assertEqual(result["ratings"]["rated_reviews"], 5)
        self.assertEqual(result["ratings"]["unrated_reviews"], 1)
        self.assertEqual(result["ratings"]["average"], 3.2)
        self.assertEqual(result["ratings"]["counts"], {"1": 1, "2": 0, "3": 2, "4": 1, "5": 1})
        self.assertEqual(result["ratings"]["percentages"]["1"], 20)
        self.assertEqual(result["sentiments"]["classified_reviews"], 5)
        self.assertEqual(result["sentiments"]["unclassified_reviews"], 1)
        self.assertEqual(result["sentiments"]["counts"], {"positive": 1, "neutral": 1, "negative": 3})
        self.assertEqual(result["sentiments"]["percentages"]["negative"], 60)
        self.assertEqual(result["issues"]["items"][0]["frequency"], 2)
        self.assertEqual(result["issues"]["items"][0]["negative_review_fraction"], 2 / 3)
        self.assertEqual(result["issues"]["covered_negative_reviews"], 2)
        self.assertEqual(result["issues"]["uncovered_negative_reviews"], 1)
        self.assertEqual(result["issues"]["coverage"], 2 / 3)
        self.assertAlmostEqual(result["issues"]["coverage_percentage"], 200 / 3)
        self.assertEqual((data, insights), original)

    def test_empty_inputs_and_zero_denominators(self):
        data, insights = inputs()
        data["reviews"] = []
        for buckets in insights["insights"].values():
            buckets["repeated"], buckets["single"] = [], []
        result = calculate_metrics(data, insights)
        self.assertIsNone(result["ratings"]["average"])
        self.assertTrue(all(value is None for value in result["ratings"]["percentages"].values()))
        self.assertTrue(all(value is None for value in result["sentiments"]["percentages"].values()))
        self.assertIsNone(result["issues"]["coverage"])
        data["reviews"] = [{"id": "x", "rating": 5, "sentiment": "positive"}]
        self.assertIsNone(calculate_metrics(data, insights)["issues"]["coverage"])
        data["reviews"] = [{"id": "x", "sentiment": "negative"}]
        self.assertEqual(calculate_metrics(data, insights)["issues"]["coverage"], 0)

    def test_invalid_ratings_sentiments_and_duplicate_reviews(self):
        for changes in [{"rating": True}, {"rating": 6}, {"rating": "5"}, {"sentiment": "unknown"}, {"id": ""}]:
            data, insights = inputs()
            data["reviews"][0].update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    calculate_metrics(data, insights)
        data, insights = inputs()
        data["reviews"].append(data["reviews"][0])
        with self.assertRaises(ValueError):
            calculate_metrics(data, insights)

    def test_invalid_issue_evidence_and_report_mismatch(self):
        for evidence in [["unknown"], ["f"], [], None]:
            data, insights = inputs()
            insights["insights"]["high"]["repeated"][0]["evidence_ids"] = evidence
            with self.subTest(evidence=evidence):
                with self.assertRaises(ValueError):
                    calculate_metrics(data, insights)
        data, insights = inputs()
        insights["metadata"]["app_id"] = "456"
        with self.assertRaises(ValueError):
            calculate_metrics(data, insights)

    def test_cli_saves_metrics_without_changing_inputs(self):
        data, insights = inputs()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reviews.json"
            report = Path(directory) / "insights.json"
            source.write_text(json.dumps(data))
            report.write_text(json.dumps(insights))
            before = source.read_bytes(), report.read_bytes()
            with patch("sys.argv", ["calculate_metrics.py", str(source), "--insights", str(report)]), redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            saved = json.loads(source.with_name("reviews_metrics.json").read_text())
            self.assertEqual((source.read_bytes(), report.read_bytes()), before)
        self.assertEqual(saved["total_reviews"], 6)
        self.assertEqual(saved["issues"]["coverage"], 2 / 3)


if __name__ == "__main__":
    unittest.main()
