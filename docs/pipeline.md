# Pipeline and standalone tools

The API and full CLI share `app/services/analyzer.py`. Standalone scripts wrap the same services for inspecting individual stages; their intermediate JSON formats differ from the canonical API response. The HTTP service writes no reports. Only explicit CLI outputs and the checked-in documentation example are files.

## Complete analysis

```bash
uv run --env-file .env review-analyzer 389801252 --country us --count 100 --output output/report.json
```

This collects reviews, prepares text once, classifies sentiment, generates and validates insights, computes metrics once, and assembles the canonical report. Settings are validated environment variables; secrets stay in the environment. Output directories are created and an existing output is overwritten only after successful analysis. Failures print an error and return status 1; argument errors return 2.

Reproducible experiments use `uv run --env-file .env python -m experiments.analyze` with Hydra overrides from `conf/experiment.yaml`, saving resolved config and output under ignored `hydra_outputs/`. The legacy seed option has no effect on collection. Apple feeds and Gemini output can change between runs.

## Individual stages

```bash
uv run scripts/fetch_reviews.py 389801252 --country us --count 100 --output output/reviews.json
uv run scripts/analyze_sentiment.py output/reviews.json
uv run --env-file .env scripts/generate_insights.py output/reviews_sentiment.json
uv run scripts/calculate_metrics.py output/reviews_sentiment.json --insights output/reviews_sentiment_insights.json
```

Collection uses bounded catalog/RSS/webpage fallbacks and explicit shortfalls; see [collection](fetch_reviews.md). Sentiment defaults to `INPUT_STEM_sentiment.json`. Insights default to `INPUT_STEM_insights.json` plus a sibling Markdown report. Metrics default to `INPUT_STEM_metrics.json`. Each script supports `--output`, creates directories, and refuses output paths that overwrite its inputs. Insight output must end in `.json`; its JSON/Markdown writes are not transactional. Other existing output files are overwritten. Errors return status 1; argument errors return 2.

## Preprocessing and sentiment

`preprocessor.py` requires unique nonempty string IDs, string titles, nonempty text, integer ratings 1–5, and valid lowercase sentiment labels when grouping. Unicode emoji become bracketed descriptive words, whitespace collapses, and negation/punctuation/case are preserved. ASCII emoticons are unchanged. Titles are limited to 200 characters and text to 4,000, retaining the first 75% and the tail around a truncation marker. Metadata identifies truncated reviews. The canonical report retains original source text.

`ReviewBatch` owns defensive original/prepared views and attaches consistent classifier labels. CardiffNLP RoBERTa runs on CPU, pinned to the revision in `settings.py`, with batch size 1, truncation, and maximum length 512 tokens. The first local run downloads weights; Docker bundles them offline. Only text influences classification; titles and stars do not. Confidence is predicted-class confidence, not sentiment intensity. The standalone sentiment script preserves its input text; the complete pipeline supplies normalized text for inference. Empty inputs skip model inference.

The classifier is English-oriented and trained on tweets; app-review sentiment is an estimate, with no language detection or equal-quality guarantee across storefronts.

## Gemini generation

`insights.py` supplies `prompts/generate_insights.txt` as the system instruction and compact JSON arrays keyed `negative`, `positive`, and `neutral` as data. Each item contains ID, title, text, and rating; repeated sentiment labels, confidence scores, and collection metadata are omitted. Negative reviews are issue evidence; other sentiments provide context. Review contents are untrusted instructions. Vague negative opinions can yield no issues.

The default model is `gemini-3.5-flash-lite`; GEMINI_MODEL overrides it. GEMINI_API_KEY takes precedence over GOOGLE_API_KEY. Nonempty inputs are sent to Google's API and need credentials; empty inputs produce an empty insight report without a provider call. The HTTP endpoint checks credentials before collecting.

`ModelReport` accepts an issues array. Each issue requires category (1–80 characters), action (1–180), priority high/medium/low, and nonempty evidence IDs; keywords default to an empty list with at most eight entries. Extra fields are forbidden. The SDK receives `response_json_schema` and an 8,192 output-token limit. Each call constrains evidence IDs to negative source IDs. Empty negative groups require zero issues. Local validation remains authoritative; see [evidence validation and repair](insight_validation.md).

High priority means blocked access, payment/data loss, or unusable core functions; medium means degraded functionality/performance; low means minor usability. Priority and semantic grouping are model judgments, independent of star ratings. Schema and ID validation guarantee structure/provenance, not semantic correctness.

The adapter owns at most three transport attempts for HTTP 408/429/500/502/503/504 and timeout/connect errors, with one/two-second delays. Nested SDK retries are disabled. Each request uses the lesser of 60 seconds and the remaining shared analysis budget. Invalid evidence gets at most one regeneration; malformed JSON or repeated invalid evidence cannot become a successful report. Stage checks reject late completion. This is cooperative: CPU inference is not forcibly interrupted, and HTTPX timeouts bound network inactivity rather than total operation duration.

The standalone insight report groups issues by priority and recurrence: repeated means at least two unique evidence IDs; single means one. Items sort by frequency descending, then category. Quotations use original source reviews. Metadata records model, accepted prompt hash, generation time, preprocessing limits, and truncated IDs. Usage in canonical reports accumulates across generation/repair; tracing retains per-call usage.

## Metrics

Ratings and sentiment have independent denominators. Missing/null ratings are unrated; valid ratings are integers 1–5, excluding booleans. Missing/null sentiment, including an object without a label, is unclassified. Present labels must be positive/neutral/negative; both string and `{label, score}` representations are accepted. Invalid present values fail. In canonical API reports collected/classified reviews normally have both dimensions populated.

- Average rating: sum of valid ratings / rated reviews.
- Rating percentages: each star count / rated reviews × 100.
- Sentiment percentages: each label count / classified reviews × 100.
- Issue frequency: unique negative review IDs cited for that issue.
- Negative review fraction: issue frequency / all negative reviews.
- Issue coverage: union of all issue evidence IDs / all negative reviews.

A five-star negative review contributes to both buckets independently. One review may support multiple issues; issue frequencies cannot be summed to obtain coverage. Undefined denominators yield null, counts remain zero, and negative reviews with no supported issues yield coverage zero. Percentages use 0–100; fractions/coverage use 0–1.

The standalone metrics tool accepts the grouped insight format, verifies app/country metadata agreement when present, rejects unknown/nonnegative evidence and duplicate categories, and recomputes counts instead of trusting cached model totals. Duplicate IDs within a metrics evidence list count once; the stricter insight-generation validator rejects them before producing its report. JSON preserves full precision; display rounding does not alter calculations.

## Verification

`uv run --locked pytest -q` covers source fixtures, preprocessing, batch consistency, classifier alignment, SDK serialization, repair, exact counts, overlap, empty inputs, deadlines, error attribution, and CLI outputs without live provider calls. The [sample report](../examples/nebula-report.md) documents a supplied live application export.

For an isolated CPU backend/batching comparison using saved reviews, see the [sentiment benchmark](sentiment-benchmark.md). It uses Hydra, development-only ONNX Runtime, pinned conversion artifacts, and no Gemini calls.
