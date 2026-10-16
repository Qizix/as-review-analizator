# Public single-request deployment

The deployed service is public, with no sign-in, one Uvicorn worker, and one active processing request. The initial deployment was verified on 2026-10-15; see release verification below. Local container checks require a working Docker daemon; Cloud Build executes the same smoke checks remotely.

## Processing policy

`app/admission.py` owns a nonblocking gate shared by `/api/analyze` and `/api/reviews/collect`. Analysis uses internal collection without acquiring the gate twice. A second processing request gets 409 and `Retry-After: 5`; it is not queued. The dashboard, assets, docs, and health remain accessible. Request/response JSON schemas are unchanged.

| Environment variable | Default | Behavior |
|---|---|---|
| WORK_ENABLED | true | false returns 503 for processing, Retry-After 60 |
| MAX_REVIEW_COUNT | 100 | Maximum reviews in each request |
| ANALYZE_REQUESTS_PER_MINUTE | 3 | Rolling 60-second global analysis allowance |
| ANALYZE_REQUESTS_PER_DAY | 100 | Global accepted analyses per UTC day |
| COLLECT_REQUESTS_PER_MINUTE | 6 | Rolling standalone collection allowance |
| COLLECT_REQUESTS_PER_DAY | 200 | Global accepted standalone collections per UTC day |

Exhausted allowances return 429 and Retry-After. Accepted work consumes allowance even if providers fail. Invalid request counts and busy rejections do not consume allowance. Minute/day counters are separate for analysis and standalone collection. They contain no review data or client identifiers and reset when the process restarts. These are shared demo limits: any public visitor can consume them. They provide neither per-user fairness nor a durable spending ceiling. Configure Gemini provider quotas and billing alerts separately; alerts do not stop spending.

## Resource profile

| Setting | Value |
|---|---|
| CPU / GPU / memory | 1 vCPU / none / 4 GiB |
| Minimum / maximum instances | 0 / 1 (service and revision limits) |
| HTTP concurrency / workers | 8 / 1 |
| Active processing requests per process | 1 |
| Request timeout / cooperative analysis budget | 300 / 280 seconds |
| Torch threads | 1 |
| Access | Public, Invoker IAM check disabled |

HTTP concurrency 8 lets busy requests and health checks reach the application. Setting it to 1 would make Cloud Run queue requests before the application could reject them. The startup probe waits for `/api/health` after model loading. CPU allocation is instance-based so telemetry can export completed HTTP spans; minimum instances remain zero.

Cloud Run can temporarily exceed instance limits during maintenance or rollouts. The gate is process-local, not a distributed lock. Keep one serving revision with 100% traffic, no traffic splitting or revision URLs exposed to users, and drain work before releases. Strict global exclusivity through platform replacements would require external coordination, intentionally outside this stateless design.

## Local and CI checks

Docker uses Python 3.12, uv 0.11.26, locked production dependencies, official Linux CPU Torch wheels, and the pinned CardiffNLP weights. Runtime inference uses the bundled model offline. The unprivileged image contains third-party attribution in `/app/NOTICE`. Reports remain in memory and return as JSON.

```bash
uv run --locked --extra quality pytest -q
npm test --prefix frontend
bash -n infra/deploy.sh infra/smoke.sh
docker build --platform linux/amd64 -t review-analyzer .
bash infra/smoke.sh review-analyzer
```

The smoke check runs offline CPU sentiment inference under 1 CPU/4 GiB, checks the non-root user, starts HTTP, checks readiness/dashboard/docs, and confirms analysis fails cleanly without credentials. It makes no paid Gemini calls. HTTP checks run inside the isolated container; no host ports or host Python/curl are needed. GitHub Actions runs tests followed by these container checks on Linux; it does not deploy, push images, or require cloud secrets. Initial model downloads during builds need network access.

Dual-mode images remain the default deployment profile. For the optional fast-only target and QUALITY_ENABLED behavior, see [dual-mode packaging](dual-mode-analysis.md#packaging-and-operation).

## Cloud prerequisites

Use a billing-enabled project and an existing chosen region. Install current gcloud, uv, and Node.js. Authenticate the operator through normal Google credentials. Enable Cloud Run Admin, Artifact Registry, Cloud Build, Secret Manager, and the Telemetry/Trace APIs described in [observability](observability.md).

Create an Artifact Registry Docker repository named `review-analyzer`, a runtime service account, and a Secret Manager secret named `gemini-api-key`. Grant that account Secret Accessor on this secret only, plus the Trace/Telemetry roles in the observability guide. Use ADC, with no static service-account keys in images or source.

The deploying identity needs build, repository push, Cloud Run deploy/public-access configuration, and permission to act as the runtime account. Cloud Build needs repository write access; Cloud Run needs image pull access. Organization policies may disallow public access. Resolve policy constraints explicitly rather than broadening IAM grants.

## Deployment

First pass container checks and commit the release. The script refuses a dirty working tree, runs Python/frontend tests, tags the Cloud Build image with the full commit SHA, runs offline inference and HTTP smoke checks before publishing it, deploys the public resource profile, routes 100% of service traffic to the latest revision, and polls readiness with Gemini configuration required. The health check confirms presence of credentials, not their validity. Use a numeric secret version for reproducible releases.

```bash
GEMINI_SECRET_VERSION=1 bash infra/deploy.sh PROJECT_ID REGION SERVICE_ACCOUNT_EMAIL
```

The script builds, pushes, changes public access and traffic, and can incur charges. It creates no project, repository, account, or secret. GEMINI_SECRET_NAME overrides the secret name; absent GEMINI_SECRET_VERSION defaults to latest. Build failures stop before deploy. Failed readiness exits nonzero but does not automatically revert traffic; use rollback below. Never run competing releases concurrently. Keep release image tags immutable in Artifact Registry.

`infra/cloud-run.yaml` is the equivalent configuration template. Replace project, region, image tag, account, and secret version before applying it. Prefer a commit tag or image digest over its illustrative latest placeholder.

## Updating an existing deployment

Record the current serving revision and secret version before each release:

```bash
gcloud run services describe app-store-review-analyzer --project PROJECT_ID --region REGION --format='yaml(status.traffic)'
```

For a maintenance release, first pause processing:

```bash
gcloud run services update app-store-review-analyzer --project PROJECT_ID --region REGION --update-env-vars WORK_ENABLED=false
```

Wait until the paused revision serves all service traffic and existing processing finishes (inspect request completion logs; allow the full 300-second request window). Then run the deployment script, which re-enables processing on the new revision. The configuration change itself is a rollout: the switch cannot instantly cancel already-running work. For emergency containment, also disable public invocation or restrict ingress and revoke/limit the Gemini credential as appropriate.

## Rollback

Route service traffic back to the recorded known-good revision:

```bash
gcloud run services update-traffic app-store-review-analyzer --project PROJECT_ID --region REGION --to-revisions PREVIOUS_REVISION=100
uv run --locked python scripts/check_health.py https://SERVICE_URL --require-gemini
```

Select the earlier working revision, not the maintenance-paused revision. Old requests may finish during rollback. Revision rollback restores its environment/image but does not restore service-level public access settings or external secret contents; keep pinned secret versions available. If the service has no known-good revision, leave processing paused while fixing it.

## Deployment acceptance

After deployment, verify public dashboard/health without an identity token, then run one real 100-review analysis. While it runs, send a second processing request: expect 409/Retry-After and responsive health. Confirm the original response and browser JSON export. Exercise a small test allowance and maintenance pause in a non-production configuration, then restore defaults.

Inspect actual memory, cold-start time, end-to-end latency, provider failures, and deadline handling under the configured resources. Confirm Cloud Trace spans, correlated JSON logs, Gemini token usage, and native Cloud Run metrics. Repeat these environment-dependent checks after each release; the initial results are recorded below. The sentiment classifier is English-oriented; country selection does not guarantee equal sentiment accuracy across languages.

Official references: [public access](https://docs.cloud.google.com/run/docs/authenticating/public), [deploy flags](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy), [instance limits](https://docs.cloud.google.com/run/docs/configuring/max-instances), [uv CI integration](https://docs.astral.sh/uv/guides/integration/github/).

## Current deployment target

Project: `obrio-test-task-api`. Runtime region: `europe-central2` (Warsaw). Cloud Build runs in `europe-west1` with Cloud Logging-only build logs. The runtime service account is `review-analyzer@obrio-test-task-api.iam.gserviceaccount.com`; Gemini credentials are supplied by Secret Manager, pinned to a numeric version. The operator selected manual scaling with one instance through Cloud Console on 2026-10-16; this live setting was preserved during the batch-1 rollout. It differs from the prepared autoscaling/scale-to-zero profile above and keeps one instance allocated until scaling is changed. Build and Docker contexts exclude checked-in report examples and local secrets. Release verification follows below.

## Initial release verification — 2026-10-15

- Public URL: https://app-store-review-analyzer-795908196054.europe-central2.run.app
- Serving revision: `app-store-review-analyzer-00001-h68`, 100% traffic.
- Image: `europe-central2-docker.pkg.dev/obrio-test-task-api/review-analyzer/app:866c202c789867997e15f250be80a1ad38f1cff8`.
- Cloud Build: `0d2182ab-b725-4fc0-b39b-6369eee67c33` in europe-west1; build, offline CPU inference, non-root execution, HTTP readiness/assets/docs, and missing-credential failure checks passed.
- Release preflight: 134 Python tests, 62 subtests, and six frontend tests passed.
- Public dashboard, API docs, and JavaScript returned 200 without authentication.
- Real Nebula analysis: 100 unique valid reviews, average rating 2.86, sentiment 35 positive/16 neutral/49 negative, seven validated issues. One bounded Gemini evidence repair succeeded; its warnings and both calls' usage are visible in telemetry. No collection/truncation warnings were reported.
- During analysis, a concurrent collection returned 409 with Retry-After 5; health returned 200 with sentiment ready and credentials configured.
- HTTP duration: 159.6 seconds; collection 6.0, sentiment 140.1, Gemini generation/repair 12.8. Peak process RSS was about 1,038 MiB. This is one measured run, not a latency guarantee. The single CPU is the observed inference bottleneck; capacity tuning remains separate work.
- Request ID: `deployment-smoke-66baf074e35b`; trace ID: `b5dcac06f7ef4db381397917ff0c368d`. Cloud Trace returned 25 spans with verified parent-child relationships and both calls' token attributes. Cloud Logging contained the matching trace reference and request summary; no telemetry-export failure events were found during verification.
- Native Cloud Monitoring returned Cloud Run request-count series. CPU/memory/latency dashboards remain the standard Cloud Run monitoring views; no custom metrics service was added.

The service-account key was never created; runtime ADC is supplied by Cloud Run. Secret Manager version 1 supplies the Gemini credential. The real report was inspected locally for verification and was not persisted by the HTTP application. There is no earlier known-good revision yet; for a first-release incident, pause processing rather than selecting an unverified rollback target.

## Per-request cost calculation

The README estimate uses the 2026-10-16 Nebula verification request `final-100-review-20261016`: 90 collected reviews out of 100 requested, server HTTP duration 101.466818 seconds, and Gemini reporting 25,624 input and 1,432 output tokens (27,056 total). Apple throttling caused the shortfall. One Gemini transport retry was recorded; no evidence repair was needed. These measurements describe this request, not a projected 100-review request. CPU/memory costs use allocated resources for the whole processing window, including network waits; peak RSS does not determine billed memory. Provider token counts cover reported usage; no unreported retry usage is estimated.

Warsaw instance-based list rates, verified through the Google Cloud Billing Catalog: CPU SKU `6872-8766-BE5C` costs $0.0000216 per vCPU-second; memory SKU `14DC-011F-3982` costs $0.0000024 per GiB-second. [Cloud Run pricing](https://cloud.google.com/run/pricing).

Gemini pricing rechecked on 2026-10-16; Cloud Run rates retain the 2026-10-15 Billing Catalog verification. Gemini 3.5 Flash-Lite standard paid rates are $0.30 per million input tokens and $2.50 per million output tokens, including thinking. This run reported no separate thinking-token counter. Actual provider usage is used, without estimated token counts or assumed cache discounts. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite).

- CPU: 101.466818 × 1 × $0.0000216 = $0.0021917.
- Memory: 101.466818 × 4 × $0.0000024 = $0.0009741.
- Gemini input: 25,624 / 1,000,000 × $0.30 = $0.0076872.
- Gemini output: 1,432 / 1,000,000 × $2.50 = $0.0035800.
- Processing-window total: $0.0144330, displayed as $0.01443 (~1.44 US cents).

This is an allocation-based estimate before free tiers, credits, discounts, and taxes, not an invoice. Instance-based billing also covers startup and idle time until shutdown; builds, artifact/source storage, Secret Manager, networking, logging, and tracing have separate costs. Idle time cannot be attributed uniquely to one request without a billing-allocation policy. No fixed all-inclusive per-request charge is claimed.

## Batch size 1 rollout — 2026-10-16

- Source commit: `ee4ded65669284f95b9f8bb79f5090024af097b4`; batch size 1 is shared by inference, standalone sentiment metadata, and the sentiment span attribute.
- Cloud Build: `67603f9e-65ff-4cfe-922b-f70c040cfcb8`, successful Linux container build and smoke checks.
- Image digest: `sha256:5376042dd01faaf864cd64f8d437d0253a0d4ad079d6fb9e1d75ce21cf32fa9d` in the existing regional `review-analyzer/app` repository.
- Processing was paused on revision `app-store-review-analyzer-00002-tlw`, then a full 300-second request drain window elapsed. The new image was staged without traffic before the switch.
- Serving revision: `app-store-review-analyzer-00003-67b`, healthy and serving 100% traffic on the existing public URL. One CPU, 4 GiB memory, one inference thread, and the operator's manual one-instance scaling remain unchanged. PyTorch and the original model/tokenizer remain in use; no ONNX backend was deployed.
- Verification request: `batch-one-c0ad9ded8522`; trace ID `e22d538cda98439fb881e0d7abf2a3b5`. Cloud Trace returned 24 spans and confirmed `app.batch_size=1` on `sentiment.classify`. Cloud Logging attributed the completed request to the new revision.
- Real API result: HTTP 200, 10 unique reviews and three validated issues. Apple returned HTTP 429 for catalog pages 2–10; fallback sources added no unique reviews. The 90-review shortfall and incomplete collection were explicitly reported. No padding or fabricated reviews were introduced.
- Measured times: collection 35.10 seconds, sentiment 15.74, Gemini 8.67, server HTTP duration 59.72. This 10-review result cannot establish a performance improvement against the earlier 100-review benchmark; Apple throttling prevents a comparable live sample.
- During analysis, concurrent processing returned 409 and health remained 200. Release checks passed with 139 Python tests, 62 subtests, and six frontend tests.

The earlier known-good processing revision is `app-store-review-analyzer-00001-h68` (batch size 8). If rollback is needed, route 100% traffic there; do not select the maintenance-paused revision `00002-tlw`. Git commits were kept local as requested; deployment used the Cloud Build source upload.

## Final Nebula verification — 2026-10-16

- Public `POST /api/analyze`, app `1459969523`, US storefront, requested count 100. HTTP 200 on revision `app-store-review-analyzer-00003-67b`; resources remained one vCPU, 4 GiB RAM, one inference thread, batch size 1.
- Request ID: `final-100-review-20261016`; trace ID: `c85317fc3edebf30f2129f75911a7777`. Cloud Logging confirmed the serving revision and server HTTP duration of 101.466818 seconds. Client elapsed time was 101.776416 seconds.
- Returned 90 unique reviews, all with valid 1–5 ratings. Catalog page 6 exhausted bounded retries with HTTP 429; fallback sources added no unique reviews. The response reported a shortfall of 10 and incomplete collection. This was a successful partial analysis.
- Independently checked response-schema validity, unique IDs, ratings, average rating, sentiment counts, issue evidence membership in negative reviews, duplicate-free evidence, per-issue counts, and union coverage against the returned reviews.
- Average rating: 2.788889. Sentiment: 31 positive, 14 neutral, 45 negative. Six issue groups cover 40/45 negative reviews (88.8889%); five remain unassigned. Group counts: unwanted subscriptions 23, paywalls 12, customer support/refunds 7, messaging errors 4, account access/deletion 3, astrological calculations 3. Counts overlap where reviews mention several issues.
- Response stage durations in seconds: collection 15.1656, app lookup 0.1453, preprocessing 0.1592, sentiment 77.9742, Gemini generation 8.0097, validation 0.0006, metrics 0.0003, pipeline 101.4591. Structured logs provide validation 0.560 ms, metrics 0.339 ms, and report assembly 0.118 ms. Gemini generation includes its request, retry, and decoding time; overlapping spans must not be added again.
- Gemini reported 25,624 input and 1,432 output tokens; one transport retry, no evidence repair. Estimated processing-window cost is $0.0144330 using the calculation above. Peak process RSS was 894.02 MiB.

The README presents this final measured result. The initial release and 10-review rollout checks above remain historical verification records, not comparable speed benchmarks.

## Dual-mode release — 2026-10-16

Revision `app-store-review-analyzer-00005-x56` serves 100% of traffic from image digest `sha256:414488dbd3012b0ba7d77dd70f4641b8cf97405ea6736299e5d9e55e65c7a28a`, built from source `29d893cbea4141e8f395fcdfb7466ce47059496e`. Cloud Build `2a7a38db-a800-4e9e-a992-1c03ae03a8d6` passed both image-profile smoke checks. Manual one-instance scaling, 1 vCPU/4 GiB, one inference thread, concurrency 8, public access and secret version 1 were preserved.

The paused revision `00004-cfw` was verified to reject processing before the full drain window; the new image was staged without traffic before the switch. The previous working processing revision is `00003-67b`, which predates the mode parameter. Reload clients and omit mode if reverting there. Do not roll back to the maintenance-paused revision.

The new revision's six live 100-review experiments, including validation failures, exact request costs, token usage, trace correlation, and native monitoring, are documented in [Cloud Run results](cloud-experiments.md). Both modes remain available, with quality the compatibility default and fast experimental. The earlier benchmark tables above are historical records, not current mode comparisons.
