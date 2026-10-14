# Rating, sentiment, and issue metrics

`scripts/calculate_metrics.py` performs deterministic local calculations with the standard library. It makes no network requests and does not load a model. Rating metrics and sentiment metrics use separate denominators and never infer one another.

## Usage and inputs

```bash
uv run scripts/calculate_metrics.py output/reviews_sentiment.json --insights output/reviews_sentiment_insights.json
uv run scripts/calculate_metrics.py output/reviews_sentiment.json --insights output/reviews_sentiment_insights.json --output output/metrics.json
```

The positional input is the review JSON, and `--insights` is required to provide the Gemini-generated JSON report. Default output is `INPUT_STEM_metrics.json` beside the reviews. Directories are created and an existing output is overwritten. Output must differ from both input paths. Inputs remain unchanged.

Reviews must be objects with unique nonempty string IDs. Missing or null ratings are excluded from rating calculations; present ratings must be integers 1–5, excluding booleans. Missing/null sentiment or a sentiment object without a label is unclassified. Present sentiment labels must be lowercase `positive`, `neutral`, or `negative`. Both the upstream `{label, score}` object and plain label strings are accepted. Missingness in one dimension does not exclude the review from the other. Invalid present values fail rather than changing denominators silently.

The insights input must contain the `high`, `medium`, and `low` priority groups, each with `repeated` and `single` lists, as produced by `generate_insights.py`. Every issue must have a nonempty unique category and a nonempty list of string evidence IDs. All evidence must reference negative reviews in the review input. Unknown IDs, positive/neutral/unclassified evidence, invalid structures, and duplicate categories fail. If both inputs provide `metadata.app_id` or `metadata.country`, these fields must match.

## Formulas and output

Output contains preserved review `metadata`, `total_reviews`, and three metric groups:

- `ratings.rated_reviews`: count of valid present ratings; `unrated_reviews`: total minus rated count.
- `ratings.average`: sum of ratings divided by rated count. It is not rounded in JSON.
- `ratings.counts`: counts for each star value 1–5, including zero-count buckets.
- `ratings.percentages`: star count divided by rated count, multiplied by 100.
- `sentiments.classified_reviews`: count of present valid labels; `unclassified_reviews`: total minus classified count.
- `sentiments.counts`: counts for positive, neutral, and negative, including zeros.
- `sentiments.percentages`: sentiment count divided by classified count, multiplied by 100.
- `issues.items[].frequency`: number of unique supporting evidence IDs for that issue. Duplicate IDs within an issue count once.
- `issues.items[].negative_review_fraction`: unique evidence count divided by negative-review count, on a 0–1 scale.
- `issues.covered_negative_reviews`: size of the union of all issue evidence sets. A review supporting several issues contributes once to coverage.
- `issues.uncovered_negative_reviews`: negative count minus covered count.
- `issues.coverage`: covered negative reviews divided by negative count, on a 0–1 scale; `coverage_percentage`: the same ratio multiplied by 100.

Issue entries retain category, priority, and sorted evidence IDs. They sort by priority high/medium/low, then frequency descending, then case-folded category. Frequencies and coverage are recomputed from evidence; the report's cached counts, fractions, summary, and recurrence buckets are not treated as numerical truth. Recurrence buckets do not affect the computed frequency. This inherits Gemini's semantic grouping rather than performing new grouping.

The current Gemini pipeline permits only negative evidence, so issue frequency counts negative reviews, with positive and neutral reviews excluded from issue metrics. A five-star negative review still counts as five-star in rating metrics and negative in sentiment/issue metrics.

Zero denominators yield JSON `null` for averages, percentages, and fractions, rather than misleading zero values or division errors. Counts remain zero. Negative reviews with no assigned issues have coverage 0; no negative reviews means undefined (`null`) coverage. Per-issue counts/fractions can overlap and need not sum to the coverage count or 100%.

Successful writes return status 0. File, JSON, and data-validation errors return status 1 with stderr output. Argument errors return status 2. The CLI prints a compact summary; displayed average and coverage are rounded to two decimals, while saved calculations retain full precision.

## Verification

Run `uv run python -m unittest discover -s tests -v`. Tests verify the exact average and distributions, independent missing-value denominators, a five-star negative review, unique evidence counting despite duplicates, overlapping issues counted once for coverage, zero denominators, missing classifications, invalid evidence, mismatched app metadata, and preservation of both input files. The script is also run against the current collected sentiment results and Gemini report.
