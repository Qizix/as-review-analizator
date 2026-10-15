# App Store Review Analyzer

Collect App Store reviews, measure ratings and sentiment, and turn recurring complaints into prioritized, evidence-linked actions. A FastAPI API and browser dashboard share one analysis pipeline.

## Run locally

Install [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned and installed automatically by uv when needed. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

```bash
uv sync --locked
uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open the [dashboard](http://127.0.0.1:8000/) or [interactive API docs](http://127.0.0.1:8000/docs). The first startup downloads the sentiment model. Analysis needs internet access to Apple and Gemini; collection alone needs no Gemini key. Prepared review text is sent to Google for insights.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"389801252","country":"us","review_count":100}'
```

| Endpoint | Result |
|---|---|
| `POST /api/analyze` | Reviews, metrics, prioritized insights, and evidence |
| `POST /api/reviews/collect` | Raw reviews and collection metadata; same request body |
| `GET /api/health` | Model readiness and credential availability |

Responses are JSON. The dashboard visualizes distributions, issue frequency/coverage, and supporting reviews, and downloads the report locally.

## Approach and design

- **One service:** FastAPI serves the API and a vanilla JavaScript dashboard, with no frontend build step, database, or queue.
- **Bounded collection:** Apple catalog, RSS, then webpage fallback; retain the first unique reviews in source order and report shortfalls. Collection is not random or representative of all historical feedback.
- **Independent sentiment:** Pinned CardiffNLP RoBERTa classifies text on CPU. Star ratings never determine sentiment. Emoji become words and oversized text is trimmed for models; original comments remain in the report.
- **Evidence-linked insights:** Gemini groups negative complaints and proposes short actions by priority and recurrence. Positive/neutral reviews provide context. Invalid evidence is rejected, with one bounded repair attempt; an empty issue list is valid.
- **Local metrics:** Python calculates averages, distributions, unique issue frequencies, and coverage from review IDs. The API keeps reports in memory; one analysis or collection runs at a time, with busy responses and configurable usage limits.

Sentiment and issue interpretation are model estimates. The classifier is English-oriented; selecting a storefront does not filter review language. Logs and traces contain timings and diagnostics, excluding review contents and credentials.

## Sample report: Instagram

A live US-storefront run on **2026-10-15** analyzed **100 unique reviews**: average rating **3.21/5**, with **22 positive, 17 neutral, and 61 negative** classifications. Six issue groups cover **31 of 61 negative reviews (50.8%)**.

| Priority | Example improvement | Reviews |
|---|---|---:|
| High | Improve suspension appeals and human review | 7 |
| Medium | Reduce feed ads and prioritize followed accounts | 7 |
| Low | Improve navigation and layout | 6 |

Read the [complete sample report](examples/instagram-report.md) or inspect its [full API-shaped JSON and source evidence](examples/instagram-report.json). This is a dated sample, not a measurement of all Instagram users.

## Tests and further details

```bash
uv run --locked pytest -q
npm test --prefix frontend
```

Node.js is needed only for frontend tests. See [architecture](docs/architecture.md), [pipeline and CLI tools](docs/pipeline.md), [observability](docs/observability.md), and [deployment/rollback](docs/deployment.md). Public single-request Cloud Run deployment files are prepared; container and cloud verification remain outstanding. Third-party attribution is in [NOTICE](NOTICE).
