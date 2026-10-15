# Code review fixes

- [ ] 1. Bind original reviews, prepared text, and sentiment in one review batch; remove independently supplied groups from insight generation. Verify mismatched evidence cannot enter a report and originals remain intact.
- [ ] 2. Preserve sanitized startup and telemetry failure details in console logs. Share record preparation between output formats without serializing JSON as an intermediate representation.
- [ ] 3. Count only actual evidence validation failures. Remove insight-specific counting from generic tracing and verify timeout and unexpected-error attribution.

Each fix receives regression coverage, a README update, and its own commit. Public report shapes, provider retry limits, and deployment scope remain unchanged.
