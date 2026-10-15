# Production refactor todo

Scope: preserve the stateless modular monolith, public report shape, original comments, independent ratings and sentiment, first-unique collection order, and strict evidence validation. Deployment remains preparation only.

- [x] 1. Deepen insight generation: one owner for generation, bounded repair, validation, usage, and provenance; remove discarded metrics work and repeated preprocessing; preserve stage-specific errors.
- [x] 2. Simplify the dashboard: consume the canonical report directly; remove legacy file reconciliation; retain meaningful integrity checks.
- [x] 3. Consolidate collection: source adapters own pagination; one accumulator owns unique reviews and collection statistics; preserve fallback semantics and metadata.
- [x] 4. Separate observability outputs: readable request blocks locally, JSON in production, explicit trace dumps; sum usage across repair calls.
- [x] 5. Unify the analysis budget: bound external operations and retries by remaining time; cover lookup, Gemini, and repair with deterministic deadline tests.

Each step receives focused verification and a separate commit. Final verification runs all Python and frontend tests, checks deployment configuration, and reviews the final changes against the strict maintainability findings.

Cloud Run deployment, live Cloud Trace ingestion, and target-machine resource sizing require deployment verification and are outside this local refactor.

## 1. Insight ownership

`AnalysisRun` owns stage identity, cooperative deadline checks, timings, and token usage for one invocation. Both the complete analysis and standalone insight script use it. `generate_insights` owns the two-attempt generation/validation lifecycle, with transport behind `request_insights`. It accepts a `ReviewBatch` that owns original classified reviews and their prepared sentiment groups; evidence eligibility and quotations use original source records.

The analysis normalizes each title/text once, classifies the prepared collection, groups that normalized result, and restores originals only in the public report. Truncation markers are carried internally and excluded from original reviews. Metrics are computed once after supported issues exist; the discarded preliminary calculations are gone. Processing-time metadata now records actual metrics work under `metrics`.

Evidence exceptions no longer mutate logging context merely by being constructed. The insight lifecycle emits reason codes at the catch point. Decode failures use an explicit exception type; unrelated inference ValueErrors retain inference attribution. Successful repaired reports record the configured model and the hash of the actual accepted instructions.

Focused tests cover canonical repair behavior, usage accumulation, original quotations, exact normalization/computation counts, prompt provenance, and non-model ValueError attribution. Existing SDK serialization, evidence rejection, traces, and API checks remain in place.

## 2. Canonical dashboard report

`prepareDashboard(response)` now accepts the actual unified response. `dashboardSources` and the generated three-file representation were deleted. The module validates source distributions, exact review counts, issue/recommendation agreement, unique evidence, negative provenance, and coverage directly, then returns render-ready state. The removed optional-source-text path was only used by the old file importer.

The renderer still uses the same state shape, filters, original comments, and browser-local canonical JSON export. Node tests now construct real response-shaped fixtures rather than historical file triples. They retain empty-report, independent sentiment/rating, escaping, filters, URL/country, and malformed-report checks.

## 3. Collection ownership

Catalog, RSS, and webpage adapters yield `SourcePage` results. They retain their own pagination and failure behavior: catalog may advance after later failures, RSS stops at a failed page, and webpage retrieves one embedded-state document. `ReviewCollection.consume` is the only owner of record validation, deduplication, exact count stopping, and per-source contributions. Its report derives aggregate counts directly from source statistics rather than incrementally reopening an earlier report.

The catalog-only function remains a compatibility adapter over the same accumulator. Its legacy metadata remains catalog-only; the full collector includes tagged source attempts. Source ordering, equal-share remaining budgets, ten-page caps, pacing, safe lookup URL construction, and original text stay unchanged. Recorded catalog/RSS fixtures and fallback cases verify output and metadata invariants.

## 4. Observation and presentation

`app/log_formatters.py` contains the JSON and console adapters. `LOG_FORMAT=auto` selects a readable request block for a terminal and JSON for redirected output. `console` and `json` override local selection; Cloud Run's `K_SERVICE` always forces JSON. Production deployment files set JSON explicitly. The standard logging handler emits the entire block under one lock; concurrent requests cannot interleave its lines.

Console trace export now writes through a logging adapter at DEBUG. `LOG_LEVEL=INFO` suppresses raw spans without disabling trace context or stage measurements; `LOG_LEVEL=DEBUG` opts into detailed span JSON. Google trace export is independent of log verbosity. Request token totals accumulate across generation and repair; each Gemini span retains per-call usage. Missing usage leaves known counters intact and marks completeness false.

Focused tests verify local blocks, production JSON, INFO/DEBUG filtering, request usage versus span usage, correlation, sanitization, and failure isolation.

## 5. Remaining analysis budget

`AnalysisRun.deadline` is the monotonic deadline shared with collection, lookup, generation, and repair. Stages check before and after work. `remaining_seconds` rejects expired work and caps operation timeouts. Expiry during an external operation maps to the existing HTTP 504 response with the active stage, preserving the original exception internally.

Gemini uses a supplied standard HTTPX transport and disables nested SDK retries (`attempts=1`). The insight adapter owns at most three transport attempts for HTTP 408/429/500/502/503/504 and HTTPX timeout/connect errors. Before each attempt it caps timeout at the lesser of 60 seconds and remaining time. The request hook rechecks the remaining time, updates network timeout extensions, and refreshes `X-Server-Timeout`. Exponential retry delays of one and two seconds are capped by the same remaining budget; no retry starts after expiry. Successful provider usage is retained even when a late result must be rejected. Lookup similarly caps its ten-second timeout, including redirect requests, and rejects late results.

This remains cooperative: synchronous CPU inference cannot be forcibly interrupted, and HTTPX timeouts bound network inactivity rather than total wall-clock response time. No background worker or cancellation framework was introduced. See the official [HTTPX timeout semantics](https://www.python-httpx.org/advanced/timeouts/) and [Google SDK HTTP configuration](https://googleapis.github.io/python-genai/genai.html). Fake-clock tests cover shrinking retry timeouts, server timeout headers, bounded delays, exhausted budgets, late success, lookup, stage completion, and HTTP 504 attribution.

## Verification

- Full suite: 109 Python tests and 62 subtests passed; six frontend tests passed.
- Deployment shell syntax passes. JSON log format is explicit in both Cloud Run deployment definitions.
- Live verification on 2026-10-15: app `373311252`, US, 100 unique reviews, 35 negative reviews, five validated issues, HTTP 200 in 17.6 seconds, 22,353 total Gemini tokens. All cited IDs belonged to negative reviews; exactly one metrics computation appeared in the 21-span trace. One local request block included stage timings and actual usage.
- No deployment, image push, persistent report storage, or cloud telemetry transmission was performed.
