# Observability

FastAPI uses the official OpenTelemetry instrumentation for HTTP server spans, custom spans for existing service operations, and standard-library structured logging. The default service name is `appstore-review-analyzer`. No collector, database, telemetry files, separate metrics exporter, or additional service is required.

## Lifecycle and configuration

`app/telemetry.py` owns the per-application tracer provider, exporters, sanitization, and shutdown. The provider is supplied to FastAPI instrumentation when the app is constructed, before its middleware stack is built. The exporter/processor starts once in the application lifespan, before model loading. Shutdown force-flushes the provider and then shuts it down, including when model loading fails. No global tracer provider is replaced, which keeps independently created test applications isolated.

`app/observability.py` owns request context, stage timing, and sanitized exception recording; `app/log_formatters.py` owns JSON and readable-console presentation. Request context is bound in ASGI middleware before FastAPI enters synchronous worker functions. A request-local observation carries aggregate results back to middleware without relying on worker context-variable changes propagating upstream.

| Environment variable | Default | Purpose |
|---|---|---|
| `OTEL_ENABLED` | `true` | Enable tracing; JSON logging remains active when false |
| `OTEL_SERVICE_NAME` | `appstore-review-analyzer` | OpenTelemetry service resource |
| `OTEL_EXPORTER` | `console` | `console`, `google`, or `none` |
| `OTEL_EXPORT_TIMEOUT_SECONDS` | `3` | Google OTLP request and credential-refresh timeout, constrained to 0.1–10 seconds |
| `GOOGLE_CLOUD_PROJECT` | unset | Required for Google export and full Cloud Logging trace references |
| `LOG_LEVEL` | `INFO` | Application threshold: DEBUG, INFO, WARNING, ERROR; DEBUG also enables console span dumps |
| `LOG_FORMAT` | `auto` | Terminal request blocks or redirected JSON; `console`/`json` override locally; Cloud Run always uses JSON |

These settings use the existing validated environment configuration. `OTEL_ENABLED=false` or `OTEL_EXPORTER=none` disables tracing and performs no credential lookup or trace export. Automatic instrumentation excludes `/api/health`, disables internal ASGI send/receive spans, and uses a no-op meter provider. No HTTPX-wide or Gemini auto-instrumentation is enabled.

The API request and response bodies remain unchanged. Responses handled by request middleware receive `X-Request-ID`; a valid incoming ID containing 1–64 ASCII letters, digits, dots, underscores, or hyphens is propagated, otherwise a random UUID is generated. Unexpected errors handled by the outer ASGI server may not include this header; their generated ID remains in the application log.

## Trace hierarchy

The hierarchy follows the existing operations. App lookup is best-effort within the remaining deadline. Metrics are computed once after validated insights exist. Optional repair adds `insights.repair`, another Gemini request, and a second validation span.

```text
POST /api/analyze
├── reviews.collect
│   └── appstore.request (one per logical Apple request, including its retries)
├── appstore.lookup
├── reviews.preprocess
├── sentiment.classify
├── insights.generate
│   ├── gemini.request
│   └── insights.decode
├── insights.validate
├── metrics.calculate (issues)
└── report.assemble
```

Collection-only requests use the same collection and Apple spans. The root HTTP span uses the official FastAPI instrumentor. Custom spans obtain its active context in the synchronous worker, preserving parent-child relationships. Standard W3C `traceparent` headers are honored. The application uses `ALWAYS_ON` sampling so enabled analysis requests are recorded even when their incoming parent is unsampled; Cloud Run's separately managed request-span sampling is independent.

Attributes include counts, country, model identifier, batch size, prompt version, upstream HTTP status, and retries. `app.validation.failures` counts only `InsightValidationError`; timeouts and unexpected validation errors retain failed span status without counting as invalid evidence. Prompt version is the SHA-256 of the actual system instructions, matching the existing report provenance; the prompt is never attached. Local operation spans use `app.*` attributes, while applicable HTTP and GenAI fields use their OpenTelemetry names. GenAI semantic conventions are evolving; changing their names requires updating the export allowlist and tests.

## One JSON summary per request

Related request information is combined into one structured record on stdout. It contains the request/trace IDs, status/outcome, per-stage durations, counts, warnings, available Gemini usage, and sanitized failure details. Child spans provide the detailed distributed trace; they are not duplicated as individual application log records. Startup/model events and telemetry-export diagnostics are separate process events.

Example summary shape, with illustrative values:

```json
{
  "severity": "WARNING",
  "event": "request.completed",
  "message": "POST /api/analyze: partial, HTTP 200, 18400 ms",
  "request_id": "sample-request",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "logging.googleapis.com/trace": "projects/PROJECT_ID/traces/4bf92f3577b34da6a3ce929d0e0e4736",
  "app_id": "389801252",
  "duration_ms": 18400,
  "analysis_duration_ms": 18380,
  "reviews_collected": 80,
  "shortfall": 20,
  "issues_identified": 7,
  "warnings": [{"code": "REVIEW_SHORTFALL", "count": 20}]
}
```

With JSON presentation, actual records are single physical JSON lines and also include UTC timestamp, route/method, stage results, and available diagnostic fields. A shared logging stream handler serializes each complete record under its standard emission lock. No request bodies are buffered for logging. Stage observations are capped at 128, warnings at 32, and stack frames at 20; omitted observations are counted. Transient summaries are released after the request, without an archive.

Successful health/static/docs requests are DEBUG-only logs. Normal completion is INFO, retries/shortfalls/rejections are WARNING, and terminal failures are ERROR. Failures use `request.failed`; rejected client requests use `request.rejected`. Uvicorn access logs are disabled. HTTPX/httpcore, Hugging Face, Google SDK, and OpenTelemetry informational chatter is suppressed. Dependency diagnostics that lack an application-safe event payload retain severity/logger identity but withhold their raw message. Model progress bars are disabled through supported library controls.

The summary is emitted when the request finishes; it is not live stage-by-stage output. ASGI cancellation produces one `request.interrupted` summary with `processing_may_continue=true`; it does not claim worker inference was cancelled, and late observations do not create a second summary. Console tracing is a separate diagnostic stream of `trace.span` JSON records that displays individual spans. Detailed span records respect `LOG_LEVEL`: INFO shows only application summaries; DEBUG opts into raw spans. Trace collection stays active at INFO. Terminal presentation groups the summary into one readable block. Startup and telemetry failures also retain sanitized error types and bounded stack locations. Both output formats share record preparation; console rendering does not pass through JSON encoding.

## Timings and Gemini usage

Monotonic clocks measure operations. Span timestamps/durations are primary; request summaries also expose these numeric millisecond fields for filtering:

- `analysis_duration_ms`: complete pipeline work, distinct from HTTP `duration_ms` which also includes response serialization.
- `collection_duration_ms`, `preprocessing_duration_ms`, and `sentiment_duration_ms`.
- `metrics_duration_ms`: sum of the existing metric calculations.
- `gemini_duration_ms`: Gemini client call, including retries and SDK setup/teardown.
- `validation_duration_ms`: the existing validation stage, including its final issue-metric calculation.

Gemini span attributes record actual SDK usage: input, output, separately reported thinking, total, and cached input tokens. Missing counters remain absent. `gemini_usage_available=false` explicitly marks missing metadata. Request-summary counters sum generation and repair; individual spans retain per-call values. Missing metadata marks the request totals incomplete. No monetary cost is estimated.

The insight adapter owns three bounded transport attempts; nested SDK retries are disabled so each attempt and delay uses the remaining analysis budget. HTTPX request/response hooks count observed network attempts and final HTTP status without inspecting bodies. Each Gemini span records attempts minus one; the request summary sums retries across generation and repair. If hooks do not observe a request, availability is false and the count is null/absent rather than assumed zero. Apple retries retain attempt numbers and delays as span events and bounded request-summary warnings.

## Errors and data boundaries

Stage exceptions mark their spans ERROR and attach exception type and stack frame locations without exception messages, locals, source snippets, or provider payloads. `AnalysisError` retains its cause and existing HTTP status/detail; internal code/stage fields improve logs without changing response schemas. Validation failures identify the validation span and increment `app.validation.failures`. Optional app-info failures mark that operation and add a warning while preserving the existing fallback behavior.

The export wrapper constructs sanitized span copies with an explicit attribute allowlist. It drops full URLs/query strings, headers, arbitrary attributes, vendor tracestate, exception messages, and raw automatic-instrumentation stack text. Trace IDs, span IDs, parent relationships, and sampling flags are preserved. Exported exception events retain type and bounded file/function/line locations. Reviews, titles, reviewer identities, system instructions, complete model output, API keys, authorization headers, and cookies are not intentionally recorded. Application request logs similarly contain only explicitly supplied operational fields.

Exporter exceptions or failed export results emit a bounded `telemetry.export_failed` diagnostic, without raising into user requests. Failed exporter initialization logs `telemetry.initialization_failed`; the application continues. Repeated failures are reported once until an export succeeds. A full SDK export queue can drop spans. Loss of telemetry is preferable to failing analysis; export is best-effort, not durable storage.

## Local verification

Start the API with local console export; Google credentials are unnecessary:

```bash
OTEL_ENABLED=true OTEL_EXPORTER=console LOG_LEVEL=DEBUG uv run --extra quality --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Run the complete-analysis curl in the README, optionally adding `-H 'X-Request-ID: local-check'`. Look for one `request.completed` summary and `trace.span` records with the same trace ID. The console exporter batches spans every second and flushes on graceful shutdown, so span output can follow the response. Successful `/api/health` calls must not produce spans.

For a log-only run, use `OTEL_EXPORTER=none`. Use `OTEL_ENABLED=false` to disable tracing entirely. Model weights and the Gemini key are still required for real analysis, independently of local tracing.

Run `uv run --locked --extra quality pytest -q`. Observability tests use injected in-memory exporters, real SDK calls over mocked HTTP transports, and a mocked classifier. They verify hierarchy, W3C context, stage attribution, token/retry counters, missing usage, correlation, privacy, disabled mode, export failures, initialization failures, concurrent request isolation, and shutdown export of the completed HTTP server span. No test sends telemetry to Google Cloud.

## Google Cloud requirements

The prepared deployment uses direct OTLP HTTP/protobuf to `https://telemetry.googleapis.com/v1/traces`, the official OpenTelemetry HTTP exporter, and a Google-auth `AuthorizedSession` using Application Default Credentials. The service account attached to Cloud Run supplies credentials; do not create static keys. The project is supplied as both `gcp.project_id` and `x-goog-user-project`.

Enable the relevant APIs in the target project:

```bash
gcloud services enable telemetry.googleapis.com cloudtrace.googleapis.com logging.googleapis.com monitoring.googleapis.com --project PROJECT_ID
```

Grant the runtime service account `roles/telemetry.tracesWriter` on the trace project and `roles/serviceusage.serviceUsageConsumer` on the quota project (the prepared configuration uses the same project). Secret Manager access remains a separate existing requirement. Native Cloud Run stdout log collection needs no logging client or agent; no application metric-export role is required because custom metrics are not exported. Operators need the appropriate Trace/Logging/Monitoring viewer permissions.

`infra/cloud-run.yaml` and `infra/deploy.sh` set `OTEL_EXPORTER=google`, `GOOGLE_CLOUD_PROJECT`, the service name, and instance-based CPU allocation (`--no-cpu-throttling`). This allows the batch exporter to run after the HTTP server span ends. It changes billing to instance-based CPU allocation; minimum instances remain zero. Do not remove this setting while relying on background batch export with request-based CPU throttling.

Flushing inside a route would miss the still-open HTTP server span. This implementation does not do that. The SDK batch thread runs with CPU available, export calls have configured timeouts, its queue is bounded, and lifespan shutdown flushes/shuts down the provider. No external collector or application background job worker is involved. SIGKILL, abrupt eviction, network failures, or queue overflow can still lose traces.

Cloud Run automatically supplies W3C `traceparent`. Logs use the active trace ID and `logging.googleapis.com/trace` with the configured project to join request/application logs in Cloud Logging. If only the legacy `X-Cloud-Trace-Context` is supplied by another proxy, convert it to W3C trace context upstream; this app does not install an additional propagator.

After deployment, execute one analysis and confirm the root/custom spans in Cloud Trace, matching log trace references, token attributes, and native Cloud Monitoring request/error/latency/CPU/memory/instance/startup metrics. The first Warsaw deployment verified live ADC/IAM export, trace hierarchy, log correlation, Gemini usage, and native request metrics. See [release verification](deployment.md#initial-release-verification--2026-10-15); repeat these checks after each deployment.

## Admission diagnostics

Public demo processing uses one shared gate and process-local allowances. `error_stage=admission` distinguishes busy (409/ANALYSIS_BUSY), minute allowance (429/RATE_LIMITED), daily allowance (429/DAILY_LIMITED), and paused service (503/SERVICE_PAUSED) responses from provider failures. These requests do not produce pipeline stage spans. Retry-After tells clients when to retry. See [deployment](deployment.md) for configuration and counter/rollout limitations.

## Investigation guide

For a slow analysis, find its request ID or trace ID, compare the summary duration fields, then open the trace waterfall. Collection spans show HTTP status/retry delays; sentiment spans show model/count/batch size; Gemini spans show call latency, HTTP status, observed retries, and actual usage. Inspect the single final metrics calculation if `metrics_duration_ms` is unexpectedly large. CPU inference and SDK calls retain the existing cooperative deadline limitations.

For Gemini failures, filter `event=request.failed` and `error_stage=gemini` or validation. Check `error_type`, `gemini_http_status`, the `gemini.request`/`insights.decode` spans, and `insights.validate` exception events. Schema/evidence failures are distinct from network/provider failures. The response remains safe and generic; raw prompts/responses are intentionally unavailable in telemetry.

For missing traces, check `OTEL_ENABLED`, exporter selection, `trace_export_ready` in `application.ready`, initialization/export diagnostics, project/ADC/IAM/API configuration, instance CPU allocation, and shutdown/queue loss. Disabling tracing is the immediate rollback: set `OTEL_ENABLED=false`; analysis and JSON logging remain available.

The full-pipeline CLI and compatibility scripts retain their existing interfaces. Automatic HTTP tracing and request-summary logging are initialized by FastAPI; standalone script execution does not initialize an exporter.

## Official references

- [OpenTelemetry FastAPI instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)
- [OpenTelemetry GenAI span conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
- [Google direct OTLP export and authentication](https://docs.cloud.google.com/stackdriver/docs/instrumentation/migrate-to-otlp-endpoints)
- [Cloud Run structured logs and trace correlation](https://docs.cloud.google.com/run/docs/logging)
- [Cloud Run trace context](https://docs.cloud.google.com/run/docs/trace)
- [Cloud Run CPU allocation and billing](https://docs.cloud.google.com/run/docs/configuring/billing-settings)

## Analysis modes

Request summaries include analysis_mode; collection spans include app.analysis.mode and app.collection.page_limit. Both attributes are explicitly allowed by the telemetry sanitizer. Fast mode nests gemini.request under analysis.unified and has no sentiment.classify span. Quality retains sentiment.classify, insights.generate and an optional insights.repair. Both use insights.decode, insights.validate, metrics.calculate and report.assemble. Gemini usage and latency retain their existing fields; fast does not report a fabricated local sentiment duration. Neither prompts, review contents nor model quotations enter telemetry.
