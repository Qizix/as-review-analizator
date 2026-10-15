# Production refactor todo

Scope: preserve the stateless modular monolith, public report shape, original comments, independent ratings and sentiment, first-unique collection order, and strict evidence validation. Deployment remains preparation only.

- [ ] 1. Deepen insight generation: one owner for generation, bounded repair, validation, usage, and provenance; remove discarded metrics work and repeated preprocessing; preserve stage-specific errors.
- [ ] 2. Simplify the dashboard: consume the canonical report directly; remove legacy file reconciliation; retain meaningful integrity checks.
- [ ] 3. Consolidate collection: source adapters own pagination; one accumulator owns unique reviews and collection statistics; preserve fallback semantics and metadata.
- [ ] 4. Separate observability outputs: readable request blocks locally, JSON in production, explicit trace dumps; sum usage across repair calls.
- [ ] 5. Unify the analysis budget: bound external operations and retries by remaining time; cover lookup, Gemini, and repair with deterministic deadline tests.

Each step receives focused verification and a separate commit. Final verification runs all Python and frontend tests, checks deployment configuration, and reviews the final changes against the strict maintainability findings.

Cloud Run deployment, live Cloud Trace ingestion, and target-machine resource sizing require deployment verification and are outside this local refactor.
