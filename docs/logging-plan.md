# Logging redesign plan

Status: proposed; runtime logging has not changed. Scope: FastAPI, the shared analysis/collection services, model startup, and the full-pipeline CLI. The existing API routes and analysis behavior remain unchanged unless explicitly noted below.

## Confirmed problems

- `app/main.py` calls `logging.basicConfig(level=INFO)` at import time. HTTPX inherits that level, so every successful Apple and Hugging Face request appears alongside application events.
- The request middleware and `uvicorn.access` both report incoming requests, using different formats. Application JSON, Uvicorn text, model output, and progress bars are mixed in one stream.
- `Analyzer.analyze` records stage durations but logs them only at the end, along with a large metadata object. A long-running analysis has no readable stage progression.
- Unexpected failures log their exception class without a traceback or a stable error code. All `ValueError` instances become model-validation failures, regardless of their origin.
- Stage identification uses the last completed duration entry. A deadline raised before the next stage starts can therefore be attributed to the previous stage.
- Collection retries and partial results lack application-level diagnostic events. The collection-only endpoint has no useful completion summary.
- `lookup_app_info` silently falls back when its request fails.

The supplied screenshot also contains the old RSS endpoint. This indicates older-process output or historical terminal output; logging should make the running build and collection source visible at startup.

## Recommended stack and configuration

Use `structlog` with Python standard-library logging and Rich for development tracebacks. Use one `ProcessorFormatter` pipeline for application and permitted third-party records. This keeps existing Uvicorn/library integration while allowing named fields, a console renderer, and a JSON renderer. Add dependencies with `uv` during implementation.

Centralize configuration in `app/logging.py`. Configure once per process before model loading, idempotently for repeated app factories/tests. Remove import-time `basicConfig` and manually serialized `logger.info(json.dumps(...))` calls. Services emit event names and typed fields; renderers own presentation.

| Setting | Default | Behavior |
|---|---|---|
| `LOG_FORMAT` | `auto` | Console for an interactive terminal, JSON otherwise; explicit `console`/`json` overrides |
| `LOG_LEVEL` | `INFO` | Application event threshold |
| `LOG_HTTP_LEVEL` | `WARNING` | HTTPX/httpcore threshold; independent of application DEBUG |

Use existing validated environment settings for deployment. Set `LOG_FORMAT=json` explicitly in Cloud Run. Respect `NO_COLOR` for terminal output. Use stream handlers only, without log files, rotation, a database, or an in-memory log archive.

## Console design

Use aligned timestamp, severity, event, and short request-ID columns. INFO is subdued, WARNING amber, ERROR red. Color supplements readable severity labels. Show a few useful fields per event; leave full model identity, revision, prompt hash, timing maps, and token breakdowns in JSON or DEBUG output.

Illustrative output, not measured timings:

```text
10:42:00.000 INFO  model.loading         —        device=cpu model=cardiffnlp/twitter-roberta-base-sentiment-latest
10:42:02.180 INFO  application.ready     —        model_load=2.18s source=apple_catalog build=e53052e
10:43:00.000 INFO  request.started       7fa2c910 POST /api/analyze
10:43:00.002 INFO  analysis.started      7fa2c910 app=1459969523 country=us requested=100
10:43:00.003 INFO  stage.started         7fa2c910 stage=collection
10:43:04.810 WARN  upstream.retry        7fa2c910 provider=apple offset=50 status=429 attempt=1/3 retry_in=1s
10:43:10.000 WARN  collection.completed  7fa2c910 reviews=80/100 pages=8/10 failed=2 shortfall=20 duration=10.00s
10:43:10.001 INFO  stage.started         7fa2c910 stage=sentiment
10:43:12.400 INFO  sentiment.completed   7fa2c910 reviews=80 negative=30 neutral=20 positive=30 duration=2.40s
10:43:12.401 INFO  stage.started         7fa2c910 stage=gemini
10:43:15.700 INFO  gemini.completed      7fa2c910 input_tokens=5100 output_tokens=620 duration=3.30s
10:43:15.760 INFO  validation.completed  7fa2c910 issues=5 covered_negative=24/30 duration=60ms
10:43:15.800 WARN  request.completed     7fa2c910 status=200 outcome=partial reviews=80/100 duration=15.80s
```

Do not use live progress panels or tables during requests: concurrent health, documentation, and analysis requests must remain readable when interleaved. Full request IDs remain available in JSON and the response header.

## Event contract and noise controls

Every event has UTC timestamp, severity, event, and readable message. Request events add full request ID, method, and route path. Analysis events add app ID, country, and requested count. Use numeric `duration_ms` in JSON and compact milliseconds/seconds in the console, measured with a monotonic clock.

| Level | Events |
|---|---|
| INFO | Model loading/ready, application ready/shutdown, work request started, major stage started/completed, successful terminal request summary |
| WARNING | Retry scheduled, exhausted page, shortfall/incomplete collection, optional metadata fallback, rejected invalid/throttled/busy requests, terminal partial-result summary |
| ERROR | Terminal processing failure, invalid provider output preventing a report, unexpected internal error |
| CRITICAL | Model/configuration failure preventing startup |
| DEBUG | Successful individual pages, small preprocessing/metric stages, detailed timing maps, successful health/static/docs requests |

One collection-completed event reports requested/actual/unique counts, pages attempted/succeeded/failed, invalid records, duplicates, retry count, duration, and outcome. An empty accessible pool is `outcome=empty` with a shortfall warning; total upstream failure is a failure. Successful HTTP status alone never defines analytical completeness.

Log retries only where the retry loop is owned. Apple retries include provider, page/offset, failed attempt, max attempts, upstream status or exception class, and the actual planned delay. Do not invent Gemini retry attempts: its SDK currently owns them; initially report aggregate Gemini-call timing and the final typed failure unless a documented callback is available.

Successful `/api/health`, `/static/*`, `/docs`, `/redoc`, and `/openapi.json` requests move to DEBUG. Their failures remain visible. Disable Uvicorn access output and route Uvicorn lifecycle/errors through the shared formatter. Preserve genuine dependency warnings/errors, while suppressing successful HTTP chatter. Capture Python warnings once without globally ignoring them.

Disable Hugging Face/Transformers progress bars for server execution using their supported controls before loading. Preserve actionable model-loading warnings; never silence every warning to hide expected optional-file probes. A successful optional HEAD probe followed by fallback is not an application error.

## Request correlation and timing

Replace decorator-based request logging with pure ASGI middleware. Generate a UUID request ID, bind it at request entry, return it as `X-Request-ID`, and reset context in `finally`. Do not trust arbitrary client strings as server request IDs.

FastAPI uses synchronous service functions in a thread pool. Bind request context before that boundary; pass a request-local operation context explicitly where completion summaries need fields produced inside the worker. Do not assume context variables modified by a synchronous endpoint propagate back into async middleware. Never keep current request/stage fields on a shared logger or Analyzer instance.

Emit the terminal summary after the final response-body event rather than immediately after response headers. Record disconnect/cancellation distinctly and do not label interrupted work successful. A disconnect does not imply that CPU inference in a worker was cancelled.

Track the active stage explicitly before checking its deadline and preserve elapsed time for failed stages. At INFO show collection, sentiment, Gemini, and validation; retain app lookup, preprocessing, rating metrics, and sentiment metrics timings at DEBUG and in the existing response metadata. Startup model loading has its own timer, separate from request time.

## Errors and issue control

Extend `AnalysisError` with stable code, stage, provider, upstream status, and retryable flag as appropriate. Preserve exception chaining. Classify errors at their actual service boundary; replace the blanket `ValueError` means model validation rule.

| Code | Meaning | Existing HTTP behavior to preserve |
|---|---|---|
| `INVALID_REQUEST` | Invalid app ID, country, count, or body | 422 |
| `ANALYSIS_BUSY` | Another inference is active | 409 |
| `RATE_LIMITED` | Our analysis request limit | 429 |
| `GEMINI_NOT_CONFIGURED` | Missing provider credentials | 503 |
| `APPLE_RATE_LIMITED` | Apple retries exhausted and no usable reviews | 502 |
| `APPLE_UNAVAILABLE` | Apple transport/HTTP failures prevent collection | 502 |
| `APPLE_RESPONSE_INVALID` | Malformed catalog response prevents collection | 502 |
| `GEMINI_UNAVAILABLE` | Provider failure prevents insight generation | 502 |
| `INSIGHTS_INVALID` | Invalid schema or unsupported evidence | 502 |
| `ANALYSIS_TIMEOUT` | Application processing deadline | 504 |
| `INTERNAL_ERROR` | Unexpected programming/inference failure | 500 |

The final row intentionally fixes the existing broad conversion of unexpected failures to 502. Expected upstream failures keep their existing status; successful partial collections keep HTTP 200 with warnings.

Retries and failed pages are WARNING events. For an HTTP request, the outer request/error boundary owns exactly one terminal event: `request.completed`, `request.rejected`, or `request.failed`. Inner services attach failure context and re-raise instead of duplicating ERROR records. The CLI boundary owns terminal failures for CLI execution. Coordinate with Uvicorn exception handling so an already-reported exception does not produce a second detailed traceback.

Expected failures get a concise reason/code and recovery hint, without a stack trace. Unexpected failures get one traceback with Rich locally, and one escaped multiline exception field in a single JSON record in production. Disable traceback locals. Avoid exposing raw provider exception messages or response bodies; sanitize exception messages/chains as well as fields. Retain exception class and frame file/function/line for diagnosis.

Keep the existing safe JSON `detail` field for API clients; add `code` and `request_id` for failed responses. Treat this as an explicit additive API change and test it. Error correlation headers must also cover validation errors and unhandled exceptions. No tracebacks go to API responses.

Partial-result warnings use stable codes such as `REVIEW_SHORTFALL`, `APPLE_PAGE_FAILED`, `TEXT_TRUNCATED`, and `APP_INFO_UNAVAILABLE`, with counts and a concise recovery hint. Keep current human-readable response warnings compatible; structured log fields do not require changing them.

## Data boundaries and production output

Log operational metadata only. Exclude review bodies/titles, reviewer identities, prompts, model outputs, API keys, authorization/cookie headers, request/response bodies, and arbitrary query strings. Escape control characters and bound free-text lengths. This applies to DEBUG and exception paths as well as normal logs.

JSON mode emits one JSON object per physical line with `severity` and `message` for Cloud Logging, plus typed application fields. No ANSI sequences or progress bars. Use stdout/stderr streams only. Cloud Run's existing platform request logs remain; application logs correlate with them using `logging.googleapis.com/trace` when a valid `X-Cloud-Trace-Context` and configured project ID are available. Without that configuration, request IDs still work. Do not add a logging service, agent, or database.

Process memory remains `peak_process_rss_mb`, clearly named as a process-lifetime high-water mark. Token usage includes available input/output/cached counts; absent usage stays absent rather than becoming an invented zero. Emit the running build revision only when supplied by the build environment, and keep detailed model revision/source in the startup JSON record.

## Implementation order and acceptance checks

1. Add `app/logging.py`, rendering configuration, validated environment settings, and stream-only logging tests. Verify console alignment/color controls, parseable JSON, and idempotent initialization.
2. Replace request middleware and unify Uvicorn output. Verify one terminal event per request, `X-Request-ID`, validation/error responses, and context isolation across concurrent requests and sync workers.
3. Instrument the collector and stage boundaries. Replay the catalog fixture; verify page success stays DEBUG, retries expose real attempts/delays, partial success is WARNING, and deadline failures identify the stage being entered.
4. Add typed error context and boundary ownership. Exercise Apple 429/timeout/malformed JSON, Gemini failure, evidence rejection, startup failure, and an unexpected internal exception. Verify status/code, one detailed terminal error, and lock release/recovery on the following request.
5. Configure model/library noise, the pipeline CLI, Docker, and Cloud Run JSON mode. Initialize logging before model loading in each entry point. Preserve compatibility-script user summaries; avoid a package-wide CLI rewrite.
6. Update README, architecture, deployment docs, and `.env.example`. Run the Python suite, a captured console success/failure flow, and JSON parsing/redaction assertions. Use fixture-backed HTTP requests for failure testing without paid model calls. Then verify one live collection and inspect readable output.

Acceptance: a normal collection has a start, a collection summary, and one terminal request summary; an ordinary analysis has roughly 10–14 INFO events independent of page count. Retries add WARNING events only when they occur. Successful health/docs/static traffic produces no INFO noise. Every request-related event is correlatable, every major stage is timed, and no review/secret data or log files are produced. A real Cloud Run trace-correlation check remains deployment verification, not something local tests can prove.

## Official references

- [structlog console output](https://www.structlog.org/en/stable/console-output.html): console rendering, Rich exceptions, and color controls.
- [structlog standard-library integration](https://www.structlog.org/en/stable/standard-library.html): `ProcessorFormatter` for application and standard-library records.
- [structlog context variables](https://www.structlog.org/en/stable/contextvars.html): request context and the sync/async propagation caveat.
- [Starlette middleware](https://starlette.dev/middleware/): pure ASGI middleware and `BaseHTTPMiddleware` context limitations.
- [HTTPX logging](https://www.python-httpx.org/logging/): separate HTTPX/httpcore logger configuration.
- [Uvicorn settings](https://github.com/Kludex/uvicorn/blob/main/docs/settings.md): logging configuration and access-log controls.
- [Hugging Face Hub environment variables](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables): supported progress-bar controls.
- [Transformers logging](https://huggingface.co/docs/transformers/main_classes/logging): warning levels, handlers, and progress-bar controls.
- [Cloud Run logging](https://docs.cloud.google.com/run/docs/logging): structured JSON, severity/message fields, and request trace correlation.
