# App Store Review Analyzer

Collect App Store reviews and turn feedback into sentiment metrics, prioritized issues, and recommendations linked to source evidence. FastAPI serves both the JSON API and an interactive dashboard.

[Live dashboard](https://app-store-review-analyzer-795908196054.europe-central2.run.app) · [Live API docs](https://app-store-review-analyzer-795908196054.europe-central2.run.app/docs)

## Run locally

Install [uv](https://docs.astral.sh/uv/). Python 3.12 is installed automatically when needed. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

```bash
uv sync --locked --extra quality
uv run --locked --extra quality --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open the [dashboard](http://127.0.0.1:8000/) or [API docs](http://127.0.0.1:8000/docs). The first dual-mode startup downloads the sentiment model; Docker bundles its weights. Analysis requires access to Apple and Gemini. Review excerpts are sent to Google; original comments remain in the returned report.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"1459969523","country":"us","review_count":100,"mode":"quality"}'
```

| Endpoint | Result |
|---|---|
| `POST /api/analyze` | Reviews, metrics, issues, recommendations, and evidence |
| `POST /api/reviews/collect` | Raw reviews and collection metadata; same request body |
| `GET /api/health` | Readiness, available modes, and credential availability |

| Mode | Analysis | Catalog page limit |
|---|---|---:|
| `fast` | One Gemini generation for sentiment and issues | 10 |
| `quality` (default) | CPU RoBERTa sentiment, then Gemini issues | 30 |

Both stop at the requested number of unique reviews. Quality can attempt one evidence repair; fast rejects invalid output without a second semantic generation. Mode names identify methods, not measured accuracy guarantees. A fast-only installation without Torch or weights is also available; see [mode configuration and container profiles](docs/dual-mode-analysis.md).

## Approach

- **One application:** FastAPI and vanilla JavaScript, with no database, queue, or frontend build step. Reports stay in memory; JSON export downloads directly from the browser.
- **Bounded collection:** Apple catalog → RSS → webpage fallback, preserving unique source reviews and explicitly reporting shortfalls. Text is cropped only for model input; original comments are returned intact.
- **Independent analysis:** Ratings never determine sentiment. Both modes can identify complaints within positive or neutral reviews. Gemini groups issues and proposes actions; Python verifies IDs and source quotations and calculates every metric.

Issue frequency counts all supporting reviews. Negative fractions and coverage count only negative supporting reviews. Interpretation remains a model estimate; source matching proves quotation provenance, not that a recommendation is correct. RoBERTa is English-oriented, and storefront selection does not filter language.

## Cloud Run results

Measured **16 October 2026**, on **1 vCPU, 4 GiB RAM, one inference thread, batch size 1**, in Warsaw. Both modes are deployed on the same warm dual-mode instance. Six sequential requests each collected **100/100 Nebula reviews**.

**Fast returned one verified report in 23.44 seconds; quality returned two in 95.34–110.01 seconds. Source validation rejected the other three attempts with HTTP 502.** These are six observed requests, not a production reliability estimate. Quality remains the default; fast is experimental.

| Result | Fast | Quality |
|---|---:|---:|
| Verified reports / attempts | **1 / 3** | **2 / 3** |
| Sentiment: positive / neutral / negative | 40 / 8 / 52 | 35 / 16 / 49 |
| Issues in verified reports | 7 | 7–8 |
| Negative issue coverage | 57.7% | 81.6–85.7% |
| Valid-report HTTP time, median | **23.440 s** | **102.677 s** |
| All-attempt HTTP time, median | 57.124 s | 98.691 s |
| Valid-report processing estimate, median | **$0.02448** | **$0.02818** |
| Observed cost per valid report, including failed attempts | $0.07653 | $0.04856 |

Successful runs used identical review payloads, verified by hash. Their sentiment labels agreed on **72%** of reviews; agreement is not accuracy. Human-labeled macro-F1, negative recall, and issue precision were not measured.

### Time by processing step

Medians of verified reports: one fast run and two quality runs. Gemini latency includes any repair calls; totals include orchestration and response handling.

| Step | Fast | Quality |
|---|---:|---:|
| Review collection | 4.851 s | 5.456 s |
| App lookup | 0.149 s | 0.183 s |
| Preprocessing | 0.149 s | 0.152 s |
| RoBERTa sentiment | Not run | **73.065 s** |
| Gemini API calls | 18.258 s | 23.753 s |
| Evidence validation | 18.892 ms | 34.803 ms |
| Metrics | 0.385 ms | 0.443 ms |
| Report assembly | 0.261 ms | 0.154 ms |
| **Complete HTTP request** | **23.440 s** | **102.677 s** |

### Every measured request

All six collected 100 reviews. A rejected response contains an error, not a partial report. Estimates include the actual provider-reported tokens for failed attempts.

| Run | Mode | HTTP | Server time | Input / output tokens | Estimated USD |
|---|---|---:|---:|---:|---:|
| 1 | Fast | 502 | 57.124 s | 23,703 / 6,557 | $0.02529 |
| 1 | Quality | 200 | 95.343 s | 28,712 / 4,226 | $0.02215 |
| 2 | Fast | 502 | 60.370 s | 28,325 / 6,554 | $0.02677 |
| 2 | Quality | 502 | 98.691 s | 57,453 / 8,178 | $0.04076 |
| 3 | Fast | 200 | 23.440 s | 28,325 / 6,100 | $0.02448 |
| 3 | Quality | 200 | 110.011 s | 57,459 / 8,240 | $0.03421 |

Token totals include repairs. Quality run 3 reported 26,153 cached input tokens; its estimate applies the cached-input rate. **Estimated processing total for all six attempts: $0.17365.** Rates are $0.30/M uncached input, $0.03/M cached input, $2.50/M output, plus allocated Cloud Run CPU and memory during each request.

Prices are before free allowances, credits, and taxes and exclude startup, idle time, builds, storage, networking, and observability. The current manual one-instance configuration keeps compute allocated: approximately **$2.70 per 24 hours** at list rates, even without requests; that daily baseline already includes request-window compute. The deployed dual-mode image still loads RoBERTa, including for fast requests; no cold-start or memory saving is claimed here.

Container startup was **50.14 s**; reported process peak RSS reached **907.18 MiB** on the shared instance. Health stayed available and concurrent processing returned **409 / Retry-After: 5**. All six requests have correlated logs and verified Cloud Trace spans. See [complete experiment details and pricing sources](docs/cloud-experiments.md) and the [machine-readable measurements](docs/benchmarks/cloud-run-2026-10-16.json).

## Sample report: Nebula

The verified cloud quality report analyzed **100 unique US reviews** of **Nebula: Spiritual Guidance** (`1459969523`):

| Metric | Result |
|---|---|
| Average rating | **2.86 / 5** |
| Ratings, 1 → 5 stars | 41 · 8 · 8 · 10 · 33 |
| Sentiment | **35 positive · 16 neutral · 49 negative** |
| Issues | 7 |
| Negative issue coverage | **40 / 49 (81.6%)**, nine unassigned |

Selected model recommendations from that report:

| Priority | Recommendation | Supporting reviews | Negative support |
|---|---|---:|---:|
| High | Display subscription terms and charges clearly before confirmation | 30 | 27 |
| High | Improve support responsiveness and refund handling | 6 | 5 |
| Medium | Make core features accessible without excessive paywalls | 9 | 7 |

A review may support several issues, so issue counts overlap. These are reported experiences and model recommendations, not independently verified defects or a survey of all users. Inspect the [complete cloud JSON report](examples/nebula-cloud-report.json).

![Nebula dashboard with rating and sentiment charts, prioritized issues, and full review text](examples/nebula-dashboard.png)

The screenshot shows the earlier supplied export, which has different issue groups. Its [original JSON](examples/nebula-report.json) and [sample narrative](examples/nebula-report.md) remain available.

[Deployment and rollback](docs/deployment.md) · [Architecture](docs/architecture.md) · [Observability](docs/observability.md)
