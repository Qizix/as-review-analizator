# App Store Review Analyzer

Collect App Store reviews and turn feedback into sentiment metrics, prioritized issues, and recommendations linked to source evidence. FastAPI serves both the JSON API and an interactive dashboard.

[Live dashboard](https://app-store-review-analyzer-795908196054.europe-central2.run.app) · [Live API docs](https://app-store-review-analyzer-795908196054.europe-central2.run.app/docs)

## Run locally

Install [uv](https://docs.astral.sh/uv/). Python 3.12 is installed automatically when needed. Copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

```bash
uv sync --locked --extra quality
uv run --locked --extra quality --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open the [dashboard](http://127.0.0.1:8000/) or [API docs](http://127.0.0.1:8000/docs). The first startup downloads the sentiment model. Apple and Gemini access are required; review excerpts are sent to Google for analysis.

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"1459969523","country":"us","review_count":100,"mode":"fast"}'
```

| Endpoint | Result |
|---|---|
| `POST /api/analyze` | Reviews, metrics, issues, recommendations, and evidence |
| `POST /api/reviews/collect` | Raw reviews and collection metadata; same request body |
| `GET /api/health` | Readiness, available modes, and credential availability |

## How it works

1. **Collect:** Fetch unique Apple reviews with bounded retries and explicit shortfall reporting.
2. **Analyze:** Choose `fast` for unified Gemini sentiment and issue analysis, or `quality` (default) for RoBERTa sentiment followed by Gemini issue analysis.
3. **Verify:** Check review IDs and source quotations, then calculate ratings, sentiment proportions, and issue frequencies in Python.
4. **Explore:** Filter issues, read full supporting reviews, and download the JSON report from the dashboard.

Ratings and sentiment stay independent. Complaints can be identified in positive and neutral reviews too. The application uses no database or persistent report storage; FastAPI serves the dashboard directly, with no frontend build step. Mode names describe the methods; comparative accuracy has not been established.

## Successful Cloud Run benchmarks

Measured **16 October 2026**, using identical sets of **100 Nebula reviews** on a warm **1 vCPU / 4 GiB** instance in Warsaw. These figures summarize **one validated fast report and two validated quality reports**, rather than overall request reliability.

| Result | Fast | Quality |
|---|---:|---:|
| Total time, median | **23.44 s** | **102.68 s** |
| Review collection | 4.85 s | 5.46 s |
| RoBERTa sentiment | — | 73.06 s |
| Gemini analysis, including repairs | 18.26 s | 23.75 s |
| Evidence validation | 18.89 ms | 34.80 ms |
| Estimated processing cost per report, median | **$0.02448** | **$0.02818** |
| Issues identified | 7 | 7–8 |
| Negative reviews covered by issues | 57.7% | 81.6–85.7% |

Costs include Gemini tokens and request-time compute, before allowances and taxes. They exclude startup and idle costs. The current always-allocated instance costs approximately **$2.70/day** in compute, which already includes request-time compute.

## Example: Nebula

The verified cloud quality report analyzed **100 unique US reviews** of **Nebula: Spiritual Guidance** (`1459969523`):

| Metric | Result |
|---|---|
| Average rating | **2.86 / 5** |
| Sentiment | **35 positive · 16 neutral · 49 negative** |
| Issues | 7 |
| Negative issue coverage | **40 / 49 (81.6%)**, nine unassigned |

Selected recommendations:

| Priority | Recommended action | Supporting reviews |
|---|---|---:|
| High | Display subscription terms and charges clearly before confirmation | 30 |
| High | Improve support responsiveness and refund handling | 6 |
| Medium | Make core features accessible without excessive paywalls | 9 |

Reviews may support multiple issues. Recommendations reflect the sampled feedback, not independently verified defects. [Download the complete report](examples/nebula-cloud-report.json).

![Nebula dashboard with rating and sentiment charts, prioritized issues, and full review text](examples/nebula-dashboard.png)
