App Store Review Analyzer — Infrastructure Plan
1. Architecture
Modular monolith: One FastAPI application serving both the REST API and web dashboard.
                  Browser
                     │
                     ▼
              Google Cloud Run
                     │
              FastAPI Application
                     │
          ┌──────────┴──────────┐
          │                     │
       REST API             Web UI
          │                     │
          └──────────┬──────────┘
                     ▼
              Analysis Service
                     │
          ┌──────────▼──────────┐
          │ 1. Collect reviews  │
          │ 2. Preprocess       │
          │ 3. Rating metrics   │
          │ 4. Sentiment model  │
          │ 5. Sentiment metrics│
          │ 6. Gemini insights  │
          │ 7. Validate results │
          │ 8. Assemble report  │
          └──────────┬──────────┘
                     │
                     ▼
                JSON Report

2. Technology Stack
Component	Technology
Backend	FastAPI
API validation	Pydantic
Frontend	HTML, CSS, Vanilla JavaScript
HTML templates	Jinja2
Sentiment analysis	Cardiff NLP RoBERTa
Insights generation	Gemini 3.5 Flash-Lite API
Package management	uv
Configuration	Environment variables + Hydra for experiments
Testing	pytest, FastAPI TestClient
Containerization	Docker
Hosting	Google Cloud Run


3. Backend Design
Service-oriented modules
- Collector: Fetch the first unique accessible App Store reviews in source order.
- Preprocessor: Normalize and validate review data.
- Sentiment: Classify reviews as positive, negative, or neutral.
- Metrics: Calculate rating, sentiment, and issue statistics.
- Insights: Generate recurring issues, keywords, and recommendations using Gemini.
- Validator: Verify LLM evidence, review IDs, and calculated frequencies.
- Analyzer: Orchestrate the complete pipeline.
Business logic remains independent of FastAPI.
Standalone CLI scripts and API endpoints reuse the same services.
Execution
- Synchronous HTTP request-response.
- Sequential pipeline execution.
- Sentiment model loaded once during application startup.
- Gemini accessed through an external API.
- One application worker initially.
- No background jobs or task queues.
4. REST API
Method	Endpoint	Purpose
POST	/api/analyze	Complete review analysis
POST	/api/reviews/collect	Collect raw reviews
GET	/api/health	Health check
GET	/docs	OpenAPI documentation
GET	/	Web dashboard


Analysis request
{
  "app_id": "389801252",
  "country": "us",
  "review_count": 100
}

Accept App Store URLs at the UI level and normalize them to app IDs.
Analysis response
{
  "app": {},
  "collection": {},
  "metrics": {
    "ratings": {},
    "sentiment": {},
    "issues": {}
  },
  "insights": {
    "issues": [],
    "recommendations": []
  },
  "reviews": [],
  "metadata": {
    "models": {},
    "processing_time": {},
    "warnings": []
  }
}

The response includes supporting review IDs, evidence quotations, and explicit metric denominators.
5. Frontend
Single-page dashboard served by FastAPI.
Input
- App Store ID or URL.
- Country.
- Review count (default: 100).
- Analyze button.
Output
- App information.
- Average rating and distribution.
- Sentiment distribution.
- Recurring problems and frequencies.
- AI-generated improvement recommendations.
- Supporting reviews.
- Raw reviews table.
- JSON responses and browser-local JSON downloads.
Frontend communicates exclusively through REST endpoints.
No separate frontend deployment.
6. Infrastructure
Google Cloud Run
Configuration	Value
CPU	1 vCPU
RAM	4 GiB
GPU	None
Minimum instances	0
Maximum instances	1
HTTP concurrency	8
Active processing requests	1
Request timeout	300 seconds
Workers	1
Scaling	Scale to zero
HTTPS	Managed by Cloud Run


Resource settings are initial estimates and require validation under actual inference load.
Container
Contains:
- Python runtime and locked dependencies.
- FastAPI application.
- CPU-only PyTorch.
- Pinned RoBERTa model weights.
- Frontend static assets.
- Analysis configuration and prompts.
Gemini inference runs externally.
Cloud dependencies
- Google Cloud Run — application hosting.
- Artifact Registry — container image storage.
- Secret Manager — Gemini API credentials.
- Cloud Logging — application logs.
Cloud Run has a monthly free allowance, but requires billing registration and does not guarantee zero total charges.
7. State and Persistence
Stateless architecture.
- No PostgreSQL.
- No Redis.
- No Celery.
- No persistent job storage.
- No message broker.
Reports are assembled in memory and returned through HTTP.
The browser manages the current report state and supports local downloads.
8. Configuration and Security
Configuration
- Environment variables for deployment settings and secrets.
- Hydra for reproducible standalone experiments.
- Versioned LLM system prompts.
- Pinned ML model revision.
- Locked Python dependencies via uv.lock.
Security
- Pydantic request validation.
- Secrets excluded from source control and frontend.
- Upstream API timeouts and bounded retries.
- Explicit handling of provider errors.
- LLM evidence validation.
- Review text treated as untrusted input.
- Public access without sign-in; one shared processing gate for analysis and collection.
- Process-local minute/day allowances, Retry-After responses, and WORK_ENABLED maintenance switch.
- One worker and maximum one instance; no traffic splitting. See docs/deployment.md for rollout limitations.
9. Observability
Track:
Metric	Purpose
Request latency	API performance
Collection duration	App Store fetching performance
Sentiment inference duration	Local ML performance
Gemini latency	External inference performance
Gemini token usage	API costs
Peak memory	Cloud Run resource sizing
Pipeline errors	Reliability
Evidence validation failures	LLM output quality


Use structured application logs without additional monitoring infrastructure.
10. Architectural Decisions
Decision	Rationale
Modular monolith	Simple deployment and maintainable boundaries
Synchronous analysis	Expected processing time does not justify queues
Stateless API	No requirement for persistent reports
Single frontend/backend service	Shared deployment and API contract
CPU sentiment inference	Workload is limited to approximately 100 reviews
External Gemini API	Avoid local generative-model hosting
Deterministic metrics	Reproducible calculations
Evidence-grounded LLM output	Traceable recommendations
Docker + Cloud Run	Portable deployment and managed infrastructure


Design principle: Minimize infrastructure complexity while retaining clear module boundaries, reproducibility, testability, and reliable API contracts.
