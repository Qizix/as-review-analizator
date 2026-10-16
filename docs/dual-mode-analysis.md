# Dual-mode analysis

## Implementation todo

- [x] Review interpretation and collection seams.
- [x] Share Gemini transport without duplicating retries or telemetry.
- [x] Implement fast and quality interpretation with verified quotations.
- [x] Support all-sentiment issue evidence and independent denominators.
- [x] Add bounded collection profiles and request mode selection.
- [x] Add dashboard selection and mixed-sentiment evidence support.
- [x] Make local ML optional and prepare both container profiles.
- [x] Test contracts, integration, failures, deadlines, packaging, and dashboard data contracts.
- [x] Document behavior and verification; commit without pushing.

## Architecture

The interpretation module owns two adapters: fast uses one Gemini generation for sentiments and issues; quality runs RoBERTa before Gemini issue interpretation. The orchestrator retains collection, app lookup, deterministic metrics, admission, deadlines, and report assembly. Both adapters use the same Gemini transport and quotation validation. Legacy standalone insight scripts retain their negative-only contract.

Fast input excludes ratings and existing sentiment. Quality includes text-based labels but excludes ratings. Semantic input preserves original characters and crops oversized fields into separate source excerpts; original reviews remain intact. Quotation matching restores capitalization from exact source substrings and retained excerpts. It establishes provenance, not semantic truth.

Both modes permit complaints from any sentiment. Frequency counts all unique supporting reviews; negative-review fractions count only negative support. Coverage is the union of supporting negative IDs divided by negative reviews, or null with no negatives. No issue is required.

Collection stops at the requested count, source exhaustion, deadline, or catalog cap: 10 pages for fast, 30 for quality. Deeper collection cannot bypass provider throttling. Existing source fallbacks and bounded retries remain shared.

## Contracts and failure policy

`AnalyzeRequest.mode` accepts `fast` or `quality`, defaulting to quality. Both processing endpoints use the selected collection profile. Reports retain their top-level shape and textual sentiment labels. Metadata adds mode and actual sentiment model; fast mode has no RoBERTa revision or invented confidence. Issue records add negative_review_count alongside total review_count/frequency. Health adds available_modes; its legacy sentiment_ready remains application readiness, including fast-only installations.

`interpret_reviews` is the interpretation interface. Quality classifies `ReviewBatch` once, then sends original source excerpts with labels to Gemini. Fast sends excerpts without ratings or labels, and attaches returned numeric predictions by exact ID coverage. Both use `request_structured` for one shared SDK transport, timeout calculation, up to three transient transport attempts, actual usage accounting, and sanitized tracing. Gemini uses minimal thinking, structured JSON, no external tools, and an 8,192-token output cap. Truncated or malformed output fails validation; no partial report is returned.

The production schema is static: the initial schema with repeated request-specific ID enums was rejected during a live 100-review check. Python enforces all ID membership and exact prediction coverage instead. A static schema also avoids request-specific schema mutation. Structured output controls shape; deterministic validation controls provenance and statistics. It cannot prove semantic truth or model accuracy.

Both modes normally use one Gemini generation and may regenerate once after deterministic validation fails, including invalid quotations or incomplete fast sentiment coverage. Fast repairs return the complete unified result without local inference. Both attempts revalidate the entire result; a second invalid response returns no report. Repair spans use `insights.repair`, cumulative token usage includes both calls, and `insight_repair_count` records recovery attempts; malformed JSON/schema failures fail immediately. All calls share the analysis deadline. No silent strategy fallback occurs. Invalid mode returns 422, unavailable quality mode returns 503, invalid output returns 502, and deadline exhaustion returns 504 under existing error handling. Original provider errors remain internal.

Semantic preprocessing preserves original whitespace and emoji, retains the first 200 title characters, and either retains the complete body up to 4,000 characters or sends separate 3,000-character head and 1,000-character tail fields. Quotes must match within one retained field. Case, whitespace layout, and straight/typographic quotation-mark differences are resolved by copying the matching original substring. Words, negation, and other punctuation are not rewritten, skipped, or fuzzily matched. The final quote always appears verbatim in the original review. Metadata describes this policy; quality also records its separate existing RoBERTa normalization policy. Original comments are always returned unchanged.

## Packaging and operation

Install both modes with `uv sync --locked --extra quality`; use the same extra on `uv run` commands. Fast-only uses `uv sync --locked --no-dev`, `QUALITY_ENABLED=false`, and `uv run --no-dev`. Quality mode stays the default for existing callers, so fast-only callers must explicitly select fast. The dashboard hides unavailable quality mode.

The default Docker target `dual` includes local weights and initializes RoBERTa during startup. `docker build --target fast -t review-analyzer:fast .` excludes the quality extra and weights, and sets QUALITY_ENABLED=false. Fast requests on a dual image skip local inference but do not remove its startup/memory cost. Runtime deployment/resource/scaling settings have not changed.

CI builds and smoke-checks both targets; `infra/smoke.sh` checks local inference offline for dual, absence of ML dependencies for fast, and readiness/assets/credential failure for both. The deployment script still builds dual. Fast-only deployment requires selecting its image explicitly; no second deployed instance is required.

## Evaluation

Run `uv run --extra quality --env-file .env python -m experiments.evaluate_analysis` to compare both methods against the same saved Nebula review set. Hydra config lives in `conf/analysis_evaluation.yaml`; input and mode overrides are supported. The experiment makes paid Gemini calls but no Apple requests. Local outputs under ignored hydra_outputs include full reports and a summary; collection and model loading are excluded from timing. One run per mode is a functional comparison, not a percentile latency benchmark.

Optional `gold_labels=/absolute/path/labels.json` accepts a human-labeled ID-to-sentiment-name mapping covering exactly the input reviews. Only then are macro-F1 and negative recall calculated. Without human labels these values remain null; between-model agreement is not accuracy. Evidence matching checks source provenance. Semantic grouping, recommendation usefulness, and whether quotes actually express a complaint require human review.

## Verification — 2026-10-16

- Automated checks: 174 Python tests plus 60 subtests; eight frontend tests; JavaScript and deployment-shell syntax checks; locked dependency resolution.
- Full 100-review requests were verified for both modes using the real Gemini SDK with mocked HTTP responses. Assertions cover unique IDs, ratings, ID-order independence, one local inference only in quality, actual token accounting, mixed-sentiment evidence and independent negative denominators.
- A fresh production-only uv environment installed without Torch, Transformers, or Hugging Face and successfully served readiness and dashboard HTML. A subprocess import blocker separately proves fast-only startup does not import local ML.
- At initial local verification, Docker daemon and browser automation were unavailable. Subsequent Cloud Build smoke checks passed for both image targets, and the dual-mode revision was deployed. See [Cloud Run experiments](cloud-experiments.md) for measured results and remaining limitations.
- Live Gemini calls used the same saved 100-review Nebula dataset for both adapters. The final paired run passed source verification and metrics, including one evidence repair for quality. Full original reviews and live raw responses remain local; the checked-in [evaluation summary](benchmarks/analysis-modes-macos-arm64.json) contains only measurements and model provenance.

| Final local run | Fast | Quality |
|---|---:|---:|
| Reviews | 100 | 100 |
| Inference and validation time | 19.052 s | 28.118 s |
| Local sentiment inference | None | 7.680 s |
| Gemini generation time, including repair | 19.005 s | 20.385 s |
| Successful Gemini generations | 1 | 2 |
| Input tokens, cumulative | 28,325 | 57,459 |
| Output tokens, cumulative | 7,228 | 5,052 |
| Validated issues | 9 | 6 |
| Negative issue coverage | 45/54 (83.3%) | 40/49 (81.6%) |

This is one local ARM Mac comparison, excluding collection, model loading, HTTP response handling, and Cloud Run overhead. Fast usage reported 26,031 cached input tokens; no uncached-cost assumption is made. Sentiment agreement was 73%, with no human ground truth; macro-F1 and negative recall remain unmeasured. Coverage is not issue precision or accuracy. These results do not establish production latency or superior quality for either method.

Development live checks exposed provider rejection of the initial ID-enumerated schema, overly long generated fields, quotation formatting differences, a shortened quotation with an ellipsis, and temporary provider HTTP 503 responses. The final static schema, tighter prompt, bounded source restoration, strict omitted-word rejection and bounded transport/evidence policies address these without accepting invented evidence. Failed earlier trials are not counted as successful reports or evidence of a production success rate. The experiment records failed runs explicitly and returns a nonzero status if any mode fails.

## Deployed experiment

Revision `app-store-review-analyzer-00005-x56` now serves both modes. The six live 100-review trials produced one valid fast report and two valid quality reports, with three explicit evidence-validation rejections. Cloud timings and success fractions supersede local results for deployed behavior; see [full measurements and methodology](cloud-experiments.md).

## Fast evidence references

The fast adapter now uses `ReferencedAnalysis` rather than model-generated quotation strings. `source_excerpts.py` partitions each retained title/body/tail field independently into contiguous chunks of at most 240 characters, preferring spaces in the latter half. It does not normalize Unicode, whitespace, spelling, or emoji, and never bridges the cropped middle. Whitespace-only chunks are omitted. Excerpt IDs are zero-based, unique across the request, and mapped to their original review; they are not persistent identifiers.

`request_analysis` sends review IDs and numbered excerpts without ratings or previous labels. Gemini returns sentiments and compact issue evidence lists of integer excerpt IDs. The static structured-output schema forbids quotation text and requires nonnegative strict integer excerpt IDs. `validate_analysis` verifies review membership and excerpt membership and its source review, then copies the original excerpt into the existing public `evidence_quotes` structure. The public API and dashboard contract remain unchanged. Quality and legacy scripts retain quotation matching.

This removes generative text copying from fast evidence output and shortens evidence records. It does not prove that an existing excerpt supports an issue semantically; that still requires model interpretation or human review. No invalid reference is discarded silently, and no invented quote is substituted. Unknown references or incomplete sentiment coverage trigger the existing single repair attempt within the request deadline. Schema failures and provider truncation still reject the response explicitly.

The incident motivating this change was confirmed in Cloud Logging on revision `00007-km2`: requests `6d8b38088d10404eb2c90f39a7febdd7` and `79ac78075adc4701923cbf577e5b3179` decoded successfully but both generations failed `evidence_quote_not_in_source_excerpt`. Increasing the token budget or accepting approximate wording would not address that provenance failure. The new reference contract prevents generated quotation mismatches without weakening provenance checks.

The final compact integer-reference contract passed real Gemini checks on 100 saved reviews from each affected app: `835599320` in 22.361 seconds (44,184 input / 4,945 output tokens) and Nebula `1459969523` in 14.390 seconds. Both used one generation without repair. These are local inference-plus-validation measurements, excluding collection/model loading/HTTP handling, not production latency guarantees or measured accuracy. All returned quotations were independently checked against original review text. Automated verification passed 188 Python tests plus 60 subtests and eight frontend tests.

Cloud verification exposed an out-of-range generated excerpt ID followed by provider HTTP 503s during repair. The fast structured schema now sets the evidence integer maximum to the last request-local reference ID (minimum remains zero). References are contiguous, so every integer allowed by the schema resolves to a source. Empty reference sets constrain issues to zero. Python still checks bounds independently; schema conformance does not establish semantic relevance. The first cloud request exhausted transport retries with 503; the second could not complete repair because of 503s. These are failed checks, not evidence of production success.
