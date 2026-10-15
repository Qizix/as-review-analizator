# Logging redesign plan

Status: proposed; runtime logging has not changed. Scope: FastAPI, the shared analysis/collection services, model startup, and the full-pipeline CLI. The existing API routes and analysis behavior remain unchanged unless explicitly noted below.

## Confirmed problems

- `app/main.py` calls `logging.basicConfig(level=INFO)` at import time. HTTPX inherits that level, so every successful Apple and Hugging Face request appears alongside application events.
- The request middleware and `uvicorn.access` both report incoming requests, using different formats. Application JSON, Uvicorn text, model output, and progress bars are mixed in one stream.
- `Analyzer.analyze` records stage durations but logs them only at the end, along with a large metadata object. Stage activity is not organized into a readable request-level report.
- Unexpected failures log their exception class without a traceback or a stable error code. All `ValueError` instances become model-validation failures, regardless of their origin.
- Stage identification uses the last completed duration entry. A deadline raised before the next stage starts can therefore be attributed to the previous stage.
- Collection retries and partial results lack application-level diagnostic events. The collection-only endpoint has no useful completion summary.
- `lookup_app_info` silently falls back when its request fails.

The supplied screenshot also contains the old RSS endpoint. This indicates older-process output or historical terminal output; logging should make the running build and collection source visible at startup.

## Recommended stack and configuration

Use `structlog` with Python standard-library logging and Rich for development tracebacks. Use one `ProcessorFormatter` pipeline for application and permitted third-party records. This keeps existing Uvicorn/library integration while allowing named fields, a console renderer, and a JSON renderer. Add dependencies with `uv` during implementation.

Centralize configuration in `app/logging.py`. Configure once per process before model loading, idempotently for repeated app factories/tests. Remove import-time `basicConfig` and manually serialized `logger.info(json.dumps(...))` calls. Services add typed stage results, retry details, and warnings to a request-local summary. The request boundary emits one record; renderers own presentation. Non-request startup/shutdown events are emitted immediately.

| Setting | Default | Behavior |
|---|---|---|
| `LOG_FORMAT` | `auto` | Console for an interactive terminal, JSON otherwise; explicit `console`/`json` overrides |
| `LOG_LEVEL` | `INFO` | Application event threshold |
| `LOG_HTTP_LEVEL` | `WARNING` | HTTPX/httpcore threshold; independent of application DEBUG |

Use existing validated environment settings for deployment. Set `LOG_FORMAT=json` explicitly in Cloud Run. Respect `NO_COLOR` for terminal output. Use stream handlers only, without log files, rotation, a database, or a persistent log archive. A bounded request-local summary exists only while work is active and contains operational metadata, never review data.

## One block per request

Emit one complete console block when a request finishes or fails. Put its identity, input parameters, stage timings, collection statistics, sentiment counts, Gemini usage, warnings, and final result inside that block. Requests must never share a block or interleave their rows. Startup, model loading, and shutdown remain separate immediate process events.

Use Rich to render a compact panel with a stage table. The header contains the method, route, short request ID, and outcome. The footer contains HTTP status and total duration. INFO/success is subdued green, partial results and rejections amber, failures red; labels preserve meaning with color disabled. Show full model identity/revision and prompt hash as optional details rather than repeating them on every row.

Illustrative completed block, not measured timings:

```text
╭─ POST /api/analyze · 7fa2c910 · PARTIAL ─────────────────────────╮
│ App       1459969523     Country us      Requested 100          │
│ Started   10:43:00 UTC                                         │
│                                                               │
│ Stage                Duration   Result                        │
│ Collection             10.00s   80/100 reviews · 8/10 pages     │
│ App lookup                8ms   App information available      │
│ Preprocessing            12ms   2 oversized texts cropped      │
│ Rating metrics            5ms   Average 3.42/5                  │
│ Sentiment               2.40s   30 negative · 20 neutral        │
│                                 30 positive                   │
│ Sentiment metrics         4ms   Classified 80/80               │
│ Gemini                  3.30s   5,100 input · 620 output tokens│
│ Validation & metrics     60ms   5 issues · coverage 24/30       │
│                                                               │
│ Issues                                                        │
│ WARN Apple 429: 2 pages exhausted retries; offsets 50, 70      │
│ WARN Review shortfall: 20                                      │
│ WARN Text cropped: 2 reviews                                  │
│                                                               │
│ HTTP 200 · Partial collection · Total 15.80s                    │
╰───────────────────────────────────────────────────────────────╯
```

Use terminal-width-aware wrapping and an ASCII/plain fallback when appropriate. A failure block retains completed stages, marks the failed stage, shows its code and recovery hint, and includes one sanitized traceback only for an unexpected failure. Stages never entered are omitted or marked not run, never assigned invented zero durations.

The formatter must render the entire block to a string first, then emit it through one shared stream handler under its emission lock. Do not call `print` separately for each row or issue a series of Rich writes. This prevents rows from concurrent requests mixing within this process. Keep the existing single-worker deployment; JSON mode uses one physical line per request.

This deliberately replaces live stage-by-stage output. The request block appears only after completion/failure. A hard process crash can lose an unfinished summary; immediate fatal process events remain visible. Do not introduce repeated progress snapshots that would turn one request back into multiple blocks.

## Request summary contract and noise controls

Each emitted request record has UTC start/end timestamps, severity, event, message, full request ID, method, route path, HTTP status, outcome, and total numeric `duration_ms`. Analysis records add app ID, country, requested/actual counts, stage results, collection statistics, available token usage, and warnings/errors. Use milliseconds/seconds in the console and typed numeric fields in JSON.

Collect stage results and retry observations during execution without emitting separate request log records. A summary may contain `stages`, `warnings`, `retry_summary`, and an optional terminal `error`. Bound request-local diagnostics to 128 detailed observations and 64 KiB, including sanitized error text. Retain fixed aggregate counters and terminal failure details when limits are reached; collapse repeated warnings and expose an `omitted_detail_count`. Release the summary after its single emission. Do not retain raw HTTP records, review bodies, or provider responses.

| Final severity | Request outcome |
|---|---|
| INFO | Successful analysis or collection |
| WARNING | Partial/empty collection, recovered retry warnings, optional metadata fallback, or rejected invalid/throttled/busy request |
| ERROR | Terminal processing failure, invalid provider output preventing a report, or unexpected internal error |
| DEBUG | Successful health/static/docs request, normally hidden |

Model loading/ready, application ready/shutdown, and fatal startup failure are independent process events, using INFO or CRITICAL as appropriate. DEBUG adds detail inside the same request record rather than generating extra request blocks.

Collection statistics include requested/actual/unique counts, pages attempted/succeeded/failed, invalid records, duplicates, retry count, elapsed time, and outcome. An empty accessible pool is `outcome=empty` with a shortfall warning; total upstream failure is a failure. Successful HTTP status alone never defines analytical completeness.

Record retry details only where the retry loop is owned. Apple observations include provider, page/offset, failed attempt, max attempts, upstream status or exception class, and actual planned delay. Successful recovery still appears in the block's retry summary. Do not invent Gemini retry attempts: its SDK currently owns them; initially report aggregate Gemini-call timing and the final typed failure unless a documented callback is available.

Successful `/api/health`, `/static/*`, `/docs`, `/redoc`, and `/openapi.json` requests remain hidden at INFO. Their failures get their own complete request block. Disable Uvicorn access output and route Uvicorn lifecycle/errors through the shared formatter. Request-associated dependency warnings join that request's summary; unassociated genuine dependency warnings/errors remain immediate process events. Suppress successful HTTP chatter and capture Python warnings once without globally ignoring them.

Disable Hugging Face/Transformers progress bars for server execution using their supported controls before loading. Preserve actionable model-loading warnings; never silence every warning to hide expected optional-file probes. A successful optional HEAD probe followed by fallback is not an application error.

## Request correlation and timing

Replace decorator-based request logging with pure ASGI middleware. Generate a UUID request ID, bind it at request entry, return it as `X-Request-ID`, and reset context in `finally`. Do not trust arbitrary client strings as server request IDs.

FastAPI uses synchronous service functions in a thread pool. Bind request context before that boundary; pass a request-local operation context explicitly where completion summaries need fields produced inside the worker. Do not assume context variables modified by a synchronous endpoint propagate back into async middleware. Never keep current request/stage fields on a shared logger or Analyzer instance.

Emit and finalize the single request summary after the final response-body event rather than immediately after response headers. Record disconnect/cancellation distinctly and do not label interrupted work successful. A disconnect does not imply that CPU inference in a worker was cancelled: include `processing_may_continue=true` when applicable. Freeze the request summary once emitted, suppress late additions to that finalized record, and never produce a second request block. Use an emission guard shared by completion/error/cancellation paths.

Track the active stage explicitly before checking its deadline and preserve elapsed time for failed stages. Include every executed pipeline stage in the completed block, including app lookup, preprocessing, rating metrics, and sentiment metrics. Retain the existing response timing metadata. Startup model loading has its own timer, separate from request time.

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

Retries and failed pages are WARNING observations collected inside the request summary. For an HTTP request, the outer request/error boundary owns exactly one terminal event: `request.completed`, `request.rejected`, or `request.failed`. Inner services attach failure context and re-raise instead of emitting their own request blocks or ERROR records. The CLI boundary owns terminal failures for CLI execution. Coordinate with Uvicorn exception handling so an already-reported exception does not produce a second detailed traceback.

Expected failures get a concise reason/code and recovery hint inside the same block, without a stack trace. Unexpected failures get one traceback with Rich locally, inside the request block, and an escaped multiline exception field in that same single JSON request record in production. Disable traceback locals. Avoid exposing raw provider exception messages or response bodies; sanitize exception messages/chains as well as fields. Retain exception class and frame file/function/line for diagnosis.

Keep the existing safe JSON `detail` field for API clients; add `code` and `request_id` for failed responses. Treat this as an explicit additive API change and test it. Error correlation headers must also cover validation errors and unhandled exceptions. No tracebacks go to API responses.

Partial-result warnings use stable codes such as `REVIEW_SHORTFALL`, `APPLE_PAGE_FAILED`, `TEXT_TRUNCATED`, and `APP_INFO_UNAVAILABLE`, with counts and a concise recovery hint. Keep current human-readable response warnings compatible; structured log fields do not require changing them.

## Data boundaries and production output

Log operational metadata only. Exclude review bodies/titles, reviewer identities, prompts, model outputs, API keys, authorization/cookie headers, request/response bodies, and arbitrary query strings. Escape control characters and bound free-text lengths. This applies to DEBUG and exception paths as well as normal logs.

JSON mode emits one complete request-summary object per physical line with `severity` and `message` for Cloud Logging, plus nested stage, retry, warning, and error fields. This is the structured equivalent of the console block; it does not also emit one JSON record per internal stage. No ANSI sequences or progress bars. Use stdout/stderr streams only. Cloud Run's existing platform request logs remain; application logs correlate with them using `logging.googleapis.com/trace` when a valid `X-Cloud-Trace-Context` and configured project ID are available. Without that configuration, request IDs still work. Do not add a logging service, agent, or database.

Process memory remains `peak_process_rss_mb`, clearly named as a process-lifetime high-water mark. Token usage includes available input/output/cached counts; absent usage stays absent rather than becoming an invented zero. Emit the running build revision only when supplied by the build environment, and keep detailed model revision/source in the startup JSON record.

## Implementation order and acceptance checks

1. Add `app/logging.py`, rendering configuration, validated environment settings, and stream-only logging tests. Verify whole-block rendering/color controls, parseable single-record JSON, bounded summaries, and idempotent initialization.
2. Replace request middleware and unify Uvicorn output. Verify one block/JSON record per request, atomic emission, `X-Request-ID`, validation/error responses, and context isolation across concurrent requests and sync workers.
3. Instrument the collector and stage boundaries. Replay the catalog fixture; verify successful pages do not emit separate logs, retries retain real attempts/delays inside the block, partial success is WARNING, and deadline failures identify the stage being entered.
4. Add typed error context and boundary ownership. Exercise Apple 429/timeout/malformed JSON, Gemini failure, evidence rejection, startup failure, and an unexpected internal exception. Verify status/code, one detailed terminal error, and lock release/recovery on the following request.
5. Configure model/library noise, the pipeline CLI, Docker, and Cloud Run JSON mode. Initialize logging before model loading in each entry point. Preserve compatibility-script user summaries; avoid a package-wide CLI rewrite.
6. Update README, architecture, deployment docs, and `.env.example`. Run the Python suite, a captured console success/failure flow, and JSON parsing/redaction assertions. Use fixture-backed HTTP requests for failure testing without paid model calls. Then verify one live collection and inspect readable output.

Acceptance: each visible request produces exactly one complete console block or one physical JSON record. All executed stages, retry summaries, warnings, and any failure appear inside it. Concurrent request rows never interleave; completion/error/cancellation cannot emit duplicates. Successful health/docs/static traffic produces no INFO output. Every major stage and the whole request are timed. Success, partial result, rejection, and failure are explicit. Bounded transient summaries are freed after emission; no review/secret data or log files are produced. A real Cloud Run trace-correlation check remains deployment verification, not something local tests can prove.

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
