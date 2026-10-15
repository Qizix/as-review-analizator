# App Store Review Analyzer

One FastAPI application collects App Store reviews, classifies sentiment on CPU, generates evidence-linked Gemini insights, and serves an interactive dashboard. Reports include independent rating/sentiment metrics, issue frequencies, coverage, and source reviews.

## Analysis flow

![Project sequence diagram: collection, sentiment, Gemini insights, validation, metrics, and dashboard](docs/sequence.svg)

[PNG image](docs/sequence.png) · [Mermaid source](docs/sequence.mmd)

## Run locally

Python 3.12 is pinned for compatibility with Hydra and the container. Install [uv](https://docs.astral.sh/uv/) (Node.js is only needed for the frontend tests), then set `GEMINI_API_KEY` in `.env` using `.env.example` as a guide.

```bash
uv sync --locked
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [the dashboard](http://127.0.0.1:8000/) or [API documentation](http://127.0.0.1:8000/docs). Enter an app ID or App Store URL, country from the full-name dropdown, and review count. API routes use `/api/analyze`, `/api/reviews/collect`, and `/api/health`. The dashboard header shows the app name, country, and review count. The dashboard renders the report JSON directly using plain HTML, CSS, and JavaScript with no build step. The first local startup may download the pinned sentiment model. Docker bundles its weights instead.

The service keeps no reports in a database or on disk. The browser holds the current report and downloads JSON locally. Review cards show full original comments, including line breaks. Negative reviews support improvement recommendations; positive and neutral reviews provide context. Shortfalls and incomplete collections are reported explicitly.

An App Store link suggests its country until you choose a country yourself. Your selected country always controls which reviews are collected.

One review batch binds original comments to prepared text and sentiment. Gemini evidence IDs are constrained to negative source reviews and validated before a report is returned. See [evidence validation](docs/insight_validation.md).

Insight generation and evidence repair are shared by the analysis and standalone tooling. The completed [production refactor](docs/production-refactor.md) documents module ownership and verification. Terminal logs retain safe failure diagnostics and show one block per request; Cloud Run and redirected output use JSON. External calls and retries share the analysis deadline.

Collection tries catalog, RSS, then webpage reviews, retaining the first unique records and reporting source failures and shortfalls.

The three follow-up bugs from the strict review are resolved in [review fixes](docs/review-fixes.md), including separate timeout and evidence-failure diagnostics.

## Test the API

With the server running, check readiness:

```bash
curl http://127.0.0.1:8000/api/health
```

Collect the first 100 unique valid reviews in source order without model analysis. Collection stops at the requested count, trying catalog JSON, then RSS JSON, then embedded App Store webpage JSON as needed. It returns fewer if all sources are exhausted. Reviews stay within the requested country; metadata records attempts and failures. Change `country` to `ua` for Ukraine or use another two-letter country code:

```bash
curl -X POST http://127.0.0.1:8000/api/reviews/collect \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "1459969523",
    "country": "us",
    "review_count": 100
  }'
```

Run the complete analysis, including sentiment, Gemini insights, and metrics:

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "app_id": "389801252",
    "country": "us",
    "review_count": 100
  }'
```

All API responses are JSON; routes have no `/v1` prefix. Rating, sentiment, and issue metrics are included in the complete analysis response.

If Gemini returns invalid evidence, analysis retries generation once with corrective instructions. A report is returned only after validation succeeds.

## Run the pipeline without the dashboard

```bash
uv run --env-file .env review-analyzer 389801252 --output output/report.json
```

This runs the same services as the API. The older per-step scripts remain optional compatibility tools. Reproducible experiments use `uv run --env-file .env python -m experiments.analyze` with Hydra overrides.

## Verify

```bash
uv run --locked pytest -q
npm test --prefix frontend
```

- [Infrastructure plan](INFRASTRUCTURE.md)
- [Architecture, API, and pipeline](docs/architecture.md)
- [Insight validation and recovery](docs/insight_validation.md)
- [Review sources and fallback behavior](docs/fetch_reviews.md)
- [Dashboard and JSON contract](docs/dashboard.md)
- [Docker and Cloud Run deployment](docs/deployment.md)
- [Observability: tracing, correlated JSON logs, and troubleshooting](docs/observability.md)
- [Assignment](TEST_TASK.md) and [research checklist](RESEARCH_TODO.md)

## Public single-request deployment

Deployment files allow public access without sign-in. One analysis or collection runs at a time; other processing requests receive HTTP 409 with `Retry-After`. The dashboard and health checks stay available. Configurable minute/day limits return 429, and `WORK_ENABLED=false` pauses processing.

[Deployment instructions](docs/deployment.md) cover prerequisites, container checks, rollout, and rollback. GitHub Actions runs tests and an offline container smoke check. Cloud deployment and container execution still require verification; no cloud resources have been created or deployed.
