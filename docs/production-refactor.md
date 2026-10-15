# Production refactor todo

Scope: preserve the stateless modular monolith, public report shape, original comments, independent ratings and sentiment, first-unique collection order, and strict evidence validation. Deployment remains preparation only.

- [x] 1. Deepen insight generation: one owner for generation, bounded repair, validation, usage, and provenance; remove discarded metrics work and repeated preprocessing; preserve stage-specific errors.
- [x] 2. Simplify the dashboard: consume the canonical report directly; remove legacy file reconciliation; retain meaningful integrity checks.
- [ ] 3. Consolidate collection: source adapters own pagination; one accumulator owns unique reviews and collection statistics; preserve fallback semantics and metadata.
- [ ] 4. Separate observability outputs: readable request blocks locally, JSON in production, explicit trace dumps; sum usage across repair calls.
- [ ] 5. Unify the analysis budget: bound external operations and retries by remaining time; cover lookup, Gemini, and repair with deterministic deadline tests.

Each step receives focused verification and a separate commit. Final verification runs all Python and frontend tests, checks deployment configuration, and reviews the final changes against the strict maintainability findings.

Cloud Run deployment, live Cloud Trace ingestion, and target-machine resource sizing require deployment verification and are outside this local refactor.

## 1. Insight ownership

`AnalysisRun` owns stage identity, cooperative deadline checks, timings, and token usage for one invocation. Both the complete analysis and standalone insight script use it. `generate_insights` owns the two-attempt generation/validation lifecycle, with transport behind `request_insights`. It accepts original classified reviews and their already-prepared sentiment groups: preprocessing remains owned by the preprocessor, while evidence quotations always use originals.

The analysis normalizes each title/text once, classifies the prepared collection, groups that normalized result, and restores originals only in the public report. Truncation markers are carried internally and excluded from original reviews. Metrics are computed once after supported issues exist; the discarded preliminary calculations are gone. Processing-time metadata now records actual metrics work under `metrics`.

Evidence exceptions no longer mutate logging context merely by being constructed. The insight lifecycle emits reason codes at the catch point. Decode failures use an explicit exception type; unrelated inference ValueErrors retain inference attribution. Successful repaired reports record the configured model and the hash of the actual accepted instructions.

Focused tests cover canonical repair behavior, usage accumulation, original quotations, exact normalization/computation counts, prompt provenance, and non-model ValueError attribution. Existing SDK serialization, evidence rejection, traces, and API checks remain in place.

## 2. Canonical dashboard report

`prepareDashboard(response)` now accepts the actual unified response. `dashboardSources` and the generated three-file representation were deleted. The module validates source distributions, exact review counts, issue/recommendation agreement, unique evidence, negative provenance, and coverage directly, then returns render-ready state. The removed optional-source-text path was only used by the old file importer.

The renderer still uses the same state shape, filters, original comments, and browser-local canonical JSON export. Node tests now construct real response-shaped fixtures rather than historical file triples. They retain empty-report, independent sentiment/rating, escaping, filters, URL/country, and malformed-report checks.
