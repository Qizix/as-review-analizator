# Insight validation and recovery

`app/services/validator.py` validates evidence against the IDs in `groups["negative"]`. Duplicate evidence within an issue, unknown/nonnegative IDs, and empty actions or duplicate normalized categories raise `InsightValidationError`, a `ValueError` subclass. It records a fixed reason code in `insight_validation_reason` and an `INSIGHTS_INVALID` warning, without logging review text, model output, or evidence IDs.

`Analyzer.analyze` catches this error on the first validation attempt and makes one additional `request_insights` call. The correction appends the reason code, the permitted negative IDs, and uniqueness requirements to the original system prompt. It regenerates from the same review groups; collection and sentiment are not repeated. The retry does not send the rejected response back to the model. The original prompt hash remains report provenance; the Gemini request span hashes the actual corrective prompt.

The second report passes the same validator. A second invalid report still returns HTTP 502 with `INSIGHTS_INVALID`; unsupported evidence is never silently removed or accepted. Provider errors and malformed JSON retain existing failure behavior. Stage boundaries check the cooperative analysis deadline before the repair and its validation; SDK calls remain bounded by their existing timeouts and transport retry settings.

Request logs include `insight_repair_count: 1` and `INSIGHTS_RETRY` when regeneration occurs. Stage durations accumulate across attempts. `metadata.gemini_usage` sums token-count fields from both generation calls so the extra request is included in reported usage. Each Gemini request span retains its own provider usage. Recovery is bounded and cannot guarantee that a model always produces a valid report.

Regression coverage in `tests/test_api.py` exercises duplicate, fabricated, and positive-review evidence, successful correction, repeated rejection, and deadline exhaustion. `tests/test_generate_insights.py` verifies cumulative provider token usage.
