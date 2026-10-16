# App Store Review Analyzer

Collect App Store reviews, measure ratings and sentiment, and turn recurring complaints into prioritized, evidence-linked actions. A FastAPI API and browser dashboard share one analysis pipeline.

[Live dashboard](https://app-store-review-analyzer-795908196054.europe-central2.run.app) · [Live API docs](https://app-store-review-analyzer-795908196054.europe-central2.run.app/docs)

## Run locally

Install [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned and installed automatically by uv when needed. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

```bash
uv sync --locked --extra quality
uv run --extra quality --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open the [dashboard](http://127.0.0.1:8000/) or [interactive API docs](http://127.0.0.1:8000/docs). The dual-mode startup downloads the sentiment model if it is not cached. Analysis needs internet access to Apple and Gemini; collection alone needs no Gemini key. Prepared review text is sent to Google for insights.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"1459969523","country":"us","review_count":100,"mode":"fast"}'
```

| Endpoint | Result |
|---|---|
| `POST /api/analyze` | Reviews, metrics, prioritized insights, and evidence |
| `POST /api/reviews/collect` | Raw reviews and collection metadata; same request body |
| `GET /api/health` | Model readiness and credential availability |

Responses are JSON. The dashboard visualizes distributions, issue frequency/coverage, and supporting reviews, and downloads the report locally.

Choose `mode: "fast"` for one Gemini call that classifies sentiment and extracts issues, or `mode: "quality"` for RoBERTa followed by Gemini with deeper collection. Omitted mode defaults to `quality` for compatibility; the name does not guarantee higher accuracy.

| Mode | Sentiment | Catalog page limit |
|---|---|---:|
| Fast | Gemini, alongside issue analysis | 10 |
| Quality | RoBERTa on CPU | 30 |

Both stop when the requested number of unique reviews is collected. Both extract complaints across all sentiments, verify source quotations, and calculate metrics in Python. Negative issue fractions use only negative supporting reviews.

For a lightweight fast-only installation, run `uv sync --locked --no-dev`, set `QUALITY_ENABLED=false` in `.env`, then run `uv run --no-dev --env-file .env uvicorn app.main:app --workers 1`. It needs no Torch, Transformers, or model weights. See [mode behavior, testing, and container profiles](docs/dual-mode-analysis.md). A local check on the same 100 saved reviews completed in **19.05 s fast / 28.12 s quality**; it excludes collection and model loading and is not a Cloud Run benchmark.

## Approach and design

- **One service:** FastAPI serves the API and a vanilla JavaScript dashboard, with no frontend build step, database, or queue.
- **Bounded collection:** Apple catalog, RSS, then webpage fallback; retain the first unique reviews in source order and report shortfalls.
- **Independent sentiment:** Fast mode uses Gemini; quality mode uses pinned CardiffNLP RoBERTa on CPU. Star ratings never determine sentiment. RoBERTa input converts emoji to words; Gemini input preserves characters and crops oversized text into source excerpts; original comments remain in the report.
- **Evidence-linked insights:** Gemini groups complaints from any sentiment and proposes short actions by priority and recurrence. Source quotations are verified. Quality mode allows one evidence repair; fast mode rejects invalid output without model fallback. An empty issue list is valid.
- **Local metrics:** Python calculates averages, distributions, unique issue frequencies, and coverage from review IDs. The API keeps reports in memory; one analysis or collection runs at a time, with busy responses and configurable usage limits.

Sentiment and issue interpretation are model estimates. The RoBERTa classifier is English-oriented; selecting a storefront does not filter review language. Logs and traces contain timings and diagnostics, excluding review contents and credentials.

## Service metrics per request

These measurements describe the earlier deployed RoBERTa baseline. Cloud Build verifies both dual-mode and fast-only containers before publishing a dual-mode release. Updated deployment experiments are recorded after rollout.

Verified on **2026-10-16** with a deployed Nebula analysis: **90 unique reviews out of 100 requested**, HTTP 200, and six validated issue groups. Apple throttled one review page; the report explicitly records the 10-review shortfall. Runtime: **1 vCPU, 4 GiB RAM, one inference thread, PyTorch batch size 1**, Warsaw.

The sample's average rating is **2.79/5**. Sentiment: **31 positive, 14 neutral, 45 negative**. Issue groups cover **40 of 45 negative reviews (88.9%)**, with five unassigned. The most frequent group concerns unwanted subscriptions (23 reviews).

| Processing stage | Measured time |
|---|---:|
| Review collection, including fallback attempts | 15.166 s |
| App information lookup | 0.145 s |
| Text preprocessing | 0.159 s |
| Sentiment inference on CPU (90 reviews) | 77.974 s |
| Gemini insight generation | 8.010 s |
| Evidence validation | 0.560 ms |
| Rating, sentiment, and issue metrics | 0.339 ms |
| Report assembly | 0.118 ms |
| **Complete analysis pipeline** | **101.459 s** |
| **Complete HTTP request** | **101.467 s** |

Stage timings come from the response and structured request log; totals include orchestration and response handling. This is one measured request, not a latency guarantee. See [verification details](docs/deployment.md#final-nebula-verification--2026-10-16).

Estimated processing cost for the **same 90-review request**, using actual provider token counts:

| Cost component | Usage | Estimated cost (USD) |
|---|---|---:|
| Cloud Run CPU | 101.47 vCPU-seconds | $0.00219 |
| Cloud Run allocated memory | 405.87 GiB-seconds | $0.00097 |
| Gemini input | 25,624 tokens | $0.00769 |
| Gemini output | 1,432 tokens | $0.00358 |
| **Total processing estimate** | **One 90-review request** | **$0.01443 (~1.44 cents)** |

Estimates use paid list prices before free-tier allowances, credits, discounts, and taxes. Cloud Run startup/idle time, builds, storage, networking, logs, and traces are additional. Review length and provider retries affect usage. See [calculation and pricing sources](docs/deployment.md#per-request-cost-calculation).

## Application example: Nebula

![Nebula dashboard showing ratings, sentiment, prioritized improvements, and supporting reviews](examples/nebula-dashboard.png)

The supplied US-storefront report for **Nebula: Spiritual Guidance** (app ID `1459969523`), collected on **2026-10-15**, contains **100 unique reviews** with an average rating of **2.86/5**. Sentiment is **35% positive, 16% neutral, and 49% negative**. Five issue groups cover **33 of 49 negative reviews (67.3%)**; 16 negative reviews remain unassigned.

| Priority | Improvement | Reviews | Recurrence |
|---|---|---:|---|
| High | Clarify subscription terms and simplify cancellation | 15 | Repeated |
| High | Improve billing and refund handling | 10 | Repeated |
| Medium | Make core features more accessible | 8 | Repeated |
| Medium | Review alarming or inaccurate horoscope content | 2 | Repeated |
| Low | Reduce false positives in community content filtering | 1 | Single |

The screenshot shows the paywall issue selected, so its review panel displays **8 supporting reviews** from the full 100-review report. Issue counts can overlap because one review may mention several problems.

Read the [sample report](examples/nebula-report.md) or inspect the [original exported JSON with source reviews and evidence](examples/nebula-report.json). These are reported experiences in this sample, not independently verified defects or a measurement of all Nebula users.

[Deployment instructions](docs/deployment.md) include the Warsaw target, Cloud Build container checks, and rollback steps.
