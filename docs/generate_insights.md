# Gemini review insights

`scripts/generate_insights.py` replaces CountVectorizer extraction with Gemini 3.5 Flash-Lite. Collection and local sentiment classification remain independent scripts. The new dependencies are `google-genai`, `pydantic`, and `emoji`, managed by uv. The obsolete extraction script, tests, documentation, and scikit-learn dependency were removed. Existing ignored phrase-output files are not deleted.

## CLI and credentials

```bash
uv sync --locked
uv run scripts/generate_insights.py output/reviews_sentiment.json
uv run scripts/generate_insights.py output/reviews_sentiment.json --output output/insights.json
```

The input is required. Default outputs are `INPUT_STEM_insights.json` and a sibling `.md` file. Custom output must have a `.json` suffix. Input and output paths must differ. Output directories are created; existing reports are overwritten only after model output passes validation. File-writing errors can leave one of the two outputs written; writes are not transactional.

Configure `GEMINI_API_KEY` or `GOOGLE_API_KEY` externally. The script prefers the former when both are present and never persists either key. Nonempty input requires a key. Empty input produces an empty report without credentials or an API request. Reviews are sent to Google's API; inference is not local.

The fixed model ID is [`gemini-3.5-flash-lite`](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite). The script uses the Google GenAI SDK's `models.generate_content`, a 60,000 ms HTTP timeout, and retry options allowing three attempts including the original request, with initial delay 1 second and maximum delay 8 seconds. The SDK decides which transport/status failures are retryable; invalid model JSON or evidence is rejected without a repair call. Timeout/retry settings do not constitute a total-run deadline.

## Input and preprocessing

`prepare_reviews` validates an object containing a `reviews` list and optional object-valued `metadata`. Every review requires a unique nonempty string ID, string title, nonempty text, integer rating from 1 through 5, and a valid sentiment. Sentiment may be the upstream `{label, score}` object or a plain label string. Labels must be lowercase `negative`, `positive`, or `neutral`. Conflicting/duplicate IDs and malformed records fail rather than changing counts silently.

Reviews are sent as compact JSON with `negative`, `positive`, and `neutral` arrays. Each item contains only `id`, `title`, `text`, and `rating`; sentiment is expressed by its array. Empty groups stay empty arrays. Metadata, confidence scores, and repeated per-record sentiment fields are omitted from the API payload. All sentiment groups are sent, including when no reviews are negative.

Unicode emoji are converted to bracketed descriptive words using `emoji.replace_emoji` and `emoji.demojize`. Whitespace is collapsed; punctuation, capitalization, and negation are preserved. ASCII emoticons are not converted. Length limits apply after conversion: 200 characters for titles and 4,000 for texts. Oversized strings retain their first 75% of the character budget, a `[truncated]` marker, and their end within the remaining budget. A record gets `truncated_fields` only if necessary. Truncation may omit evidence in the middle; this is disclosed to the model and via metadata. Source records are never mutated.

## Prompt, output schema, and validation

`prompts/generate_insights.txt` is supplied as the system instruction, separately from review JSON. It directs Gemini to use negative reviews as the only evidence for actionable complaints and other sentiments only for context. Review text is untrusted data. Equivalent complaints should be combined, different issues kept separate, and unsupported complaints, causes, counts, OS details, or resolution history omitted. A vague negative opinion need not generate an issue; an empty issue list is valid even when negative reviews exist.

The `ModelReport` Pydantic schema accepts only an `issues` array. Each `Issue` has `category` (1–80 characters), `action` (1–180 characters), `priority` (`high`, `medium`, `low`), and nonempty `evidence_ids`. Extra fields are forbidden. The generated JSON Schema is passed as `response_json_schema`, preserving `additionalProperties` and definitions without conversion into Gemini's narrower legacy `response_schema` format. That conversion can send unsupported `additional_properties` fields and trigger HTTP 400. The same Pydantic models validate the returned JSON locally. Regression tests inspect the actual SDK HTTP payload and check that extra fields are still rejected. The prompt requests one short actionable sentence per issue, but sentence grammar and actual semantic support are not guaranteed by schema validation.

Priority is impact-based: high for blocked access, payment/data loss, or unusable core functions; medium for degraded functionality/performance; low for minor usability issues. Ratings alone do not determine priority. The model receives an 8,192 output-token limit. An empty, malformed, blocked, or truncated response cannot become a successful report if its text does not validate.

`build_report` checks that every cited ID belongs to a negative input review and appears only once per issue. Unknown, positive, neutral, or repeated evidence IDs fail the whole report. Categories must be nonempty and unique after whitespace normalization and case folding; actions must be nonempty. Equivalent categories with different wording still require correct model grouping. Evidence-ID checks ensure provenance and counts, not that the model interpreted each review correctly. Google also [recommends application-level value validation](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).

The local code calculates `review_count` from evidence IDs and `negative_review_fraction` using the total negative-review count. Recurrence means review frequency, not reproducibility of a bug. Counts of 2 or more go into `repeated`; 1 goes into `single`. A review may support multiple distinct issues; overall categorized negative-review count uses the union of evidence IDs, so it is never the sum of issue counts.

## Saved reports

JSON contains preserved `metadata`, a deterministic `summary`, and `insights` grouped first by priority and then recurrence. Each issue includes category, action, review count, negative-review fraction, and sorted evidence IDs. Within each bucket, issues sort by descending count and then case-folded category. Empty buckets remain arrays. Summary includes total and per-sentiment review counts, categorized/uncategorized negative-review counts, and issue count.

`metadata.insight_generation` records the model, whether it was called, UTC generation timestamp, SHA-256 of the exact prompt, preprocessing limits, and IDs of truncated reviews. Markdown is rendered locally from validated JSON, with short action lists rather than free-form model prose. A report with no issues explicitly says no supported actionable complaints were identified. It does not infer historical rating changes or mark fixes resolved because the input contains no longitudinal history.

Successful writes return exit status 0. File/input/schema/evidence/API/HTTP errors return status 1 with a message on stderr. Argument errors return status 2. Failure produces no newly generated report before validation completes; existing reports may still exist from earlier successful runs. Original collection/sentiment files are retained.

## Verification

Run `uv run python -m unittest discover -s tests -v`. Tests cover grouped sentiment payloads, emoji conversion, whitespace and negation, length caps and truncation markers, duplicate/input validation, model/system-prompt/schema parameters, credential handling, evidence validation, local counting and overlapping evidence, priority/recurrence buckets, empty reports, source preservation, and CLI JSON/Markdown generation.

Automated tests mock Gemini rather than inventing real model reports for collected data. An SDK-level test uses a local HTTP mock to check actual request serialization and recovery from HTTP 503. Preparation was also verified against the 100 saved reviews: 45 negative, 31 positive, and 24 neutral, with no truncation needed. A live verification requires a configured API key and should run the CLI against `output/reviews_sentiment.json`, then inspect whether each action is supported by its cited negative review IDs. Semantic accuracy and absence of false positives cannot be established solely by offline mocks.
