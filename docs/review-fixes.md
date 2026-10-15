# Code review fixes

- [x] 1. Bind original reviews, prepared text, and sentiment in one review batch; remove independently supplied groups from insight generation. Verify mismatched evidence cannot enter a report and originals remain intact.
- [x] 2. Preserve sanitized startup and telemetry failure details in console logs. Share record preparation between output formats without serializing JSON as an intermediate representation.
- [ ] 3. Count only actual evidence validation failures. Remove insight-specific counting from generic tracing and verify timeout and unexpected-error attribution.

Each fix receives regression coverage, a README update, and its own commit. Public report shapes, provider retry limits, and deployment scope remain unchanged.

## 1. Review batch consistency

`ReviewBatch` owns copied original records and their normalized representation. Its classification method attaches validated inference results to both representations; callers receive copies of source records and grouped model input. Insight generation accepts this batch instead of independently supplied originals and groups. The public report and per-step script output formats remain unchanged.

The preprocessor centralizes record validation in `validated_reviews`, reused by normalization and sentiment grouping; `prepare_reviews` no longer creates a discarded group just to validate input. Validation still rejects duplicate IDs, invalid ratings, and malformed reviews. Text is normalized once in the complete analysis.

`build_report` independently derives eligible negative IDs from original source sentiment, so even a mismatched direct validator call cannot promote positive/neutral evidence. Tests reproduce both the original mismatch and attempted mutations of input records or returned group/source copies, and verify original quotations, truncation markers, inference labels, and rating independence.

## 2. Console failure diagnostics

`prepare_record` builds the shared structured record directly for JSON and console output. The console formatter no longer serializes a JSON record and parses it back. Both request and process events render sanitized error types and bounded stack locations; telemetry diagnostics with only `error_type` retain that field too. Raw LogRecord messages and exception text remain withheld when a dependency does not supply application event data. JSON output stays one physical line.

Formatter tests cover application and telemetry failures, retained safe fields, non-mutation of event data, and suppression of raw dependency exception text. An application-lifespan test verifies a failed model factory remains diagnosable in console output without revealing the exception message.
