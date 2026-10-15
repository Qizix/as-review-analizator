# Modular analysis application

`INFRASTRUCTURE.md` is the target architecture. `app/main.py` owns FastAPI routes, Jinja2 HTML, static assets, lifespan, request logging, and an in-process rate limit. Business logic in `app/services/` does not import FastAPI.

## Services and lifecycle

- `collector.py`: validates ID/country/count, fetches up to 10 App Store catalog review pages sequentially using `next` offsets, with a 0.5-second pause between pages and bounded retries, deduplicates reviews, and samples with a local seeded RNG. It accepts a monotonic deadline; request timeouts and retry delays shrink with the remaining budget. Unfetched pages are recorded as failed rather than padded.
- `app_info.py`: best-effort Apple lookup for name, developer, store/icon URLs, and current version. Failure retains ID/country without fabricating metadata.
- `preprocessor.py`: converts Unicode emoji to bracketed words, collapses whitespace, caps titles at 200 characters and texts at 4,000 with head/tail preservation, and records truncated IDs. Original source text is retained for the final report.
- `sentiment.py`: loads CardiffNLP RoBERTa on CPU, classifies preprocessed text in batches of 8, and truncates at 512 tokens. The revision is pinned to `3216a57f2a0d9c45a2e6c20157c20c49fb4bf9c7`. Torch uses one thread by default. Rating never determines sentiment.
- `metrics.py`: calculates independent distributions and denominators, unique issue frequencies, negative fractions, and union-based coverage. Undefined ratios are null.
- `insights.py`: sends compact negative/positive/neutral arrays and a versioned system prompt to Gemini 3.5 Flash-Lite, using `response_json_schema` and capturing provider usage.
- `validator.py`: rejects unknown/nonnegative/duplicate evidence and duplicate categories. Quotations are taken directly from original reviews. Keywords come from semantic grouping.
- `analyzer.py`: orchestrates all stages and assembles the unified response. Original titles/text are restored after sentiment classification, so model preprocessing does not replace raw source reviews.

FastAPI lifespan loads one classifier per worker before accepting traffic. A nonblocking per-worker analysis lock returns 409 for parallel inference instead of queuing it. The synchronous route runs through FastAPI's thread pool and returns the complete JSON report. There are no background jobs, persistent reports, databases, or brokers.

Missing Gemini credentials are rejected before API collection. Empty samples skip inference/provider generation. If every collection page fails and no reviews are available, analysis fails. Empty accessible feeds and negative feedback without concrete actionable complaints can produce valid empty insight lists.

## REST contract

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/analyze` | Complete analysis from app_id, country, review_count, optional seed |
| POST | `/api/reviews/collect` | Raw reviews plus collection metadata |
| GET | `/api/health` | Readiness and credential availability, never credentials |
| GET | `/docs` | OpenAPI documentation |
| GET | `/` | Jinja2 dashboard |

`AnalyzeRequest` accepts positive numeric string IDs and normalized ISO countries. Count is a strict integer 1–500 at the schema boundary, limited to 100 by default operational settings. The dashboard receives the configured maximum. Seed defaults to 42.

The unified response includes `app`, `collection`, `metrics`, `insights`, `reviews`, and `metadata`:

- App information and actual/requested/available review counts, sampling pool, page outcomes, shortfall, seed, and collection time.
- Rating metrics and singular `metrics.sentiment`, with explicit independent denominators.
- Issue frequencies/coverage computed locally from source labels and unique evidence IDs.
- `insights.issues` with category, priority, review count, negative fraction, supporting IDs, original quotations, and keywords.
- `insights.recommendations` with short actions linked to categories and evidence IDs.
- Original source reviews plus sentiment labels/scores.
- Model/revision identity, timings, token usage, prompt hash, peak process RSS, preprocessing details, and warnings.

Rating, sentiment, and issue metrics are calculated inside the analysis pipeline and returned by `POST /api/analyze`. There is no standalone metrics endpoint. Coverage uses the union of evidence IDs, since a review can support several issues. The API returns JSON and has no export endpoint. The dashboard can download the JSON report directly from browser memory. No review or report file is stored server-side.

## Errors, limits, and observability

422 indicates request/data validation, 503 missing provider credentials, 409 concurrent analysis, 429 rate limiting, 502 provider/inference/evidence failure, and 504 an exceeded stage deadline. Failed evidence validation never produces a successful report.

The paid endpoint uses a global per-worker rolling limit of three attempted analyses/minute by default; 429 includes Retry-After. This volatile limit resets after restarts and is not distributed identity/quota infrastructure. Prepared Cloud Run deployment is private by default. Health and assets do not spend Gemini tokens.

The 280-second application deadline is cooperative, below Cloud Run's 300-second timeout. Collection uses shrinking budgets, and stage boundaries check elapsed time. CPU inference and SDK calls cannot be forcibly cancelled mid-operation; disconnected clients can leave their current computation finishing. Validate resource sizing and timeout behavior on Cloud Run before production use.

Structured logs record request latency/status, stage timings, review count, Gemini usage, model/prompt provenance, peak memory, and error type/stage. Credentials and review/model-response bodies are not logged. Peak RSS is a process-lifetime high-water mark, not a per-request allocation.

## CLI, compatibility, and experiments

`review-analyzer` in `app/cli.py` invokes the same Analyzer and writes an explicit local output file. Old per-step scripts now wrap shared services and retain their earlier output formats. The temporary static dashboard server was removed. Unified API reports are the primary interface; old file formats are compatibility tooling.

Settings come from validated environment variables. Defaults: one Torch thread, 100 reviews, 280 seconds, three analyses/minute, and the pinned revision. Python is pinned to 3.12 and constrained below 3.14 because Hydra's completion parser currently fails on 3.14.

`experiments/analyze.py` and `conf/experiment.yaml` use Hydra overrides and save resolved configuration/report under ignored `hydra_outputs/`. Secrets remain environment-only. A fixed seed does not freeze changing Apple feeds or Gemini interpretation; exact sampling is tested against recorded responses.

Pytest covers services plus FastAPI success/failure paths, classifier reuse, validation, evidence rejection, throttling, and removal of the metrics and export endpoints. Frontend Node tests cover API adaptation, URL parsing, denominators, evidence consistency, filters, empty reports, and escaping. Live 100-review HTTP and browser analyses verified the real providers. Local performance is not a Cloud Run benchmark; image build and deployment remain unverified because Docker was not running and deployment was not requested.
