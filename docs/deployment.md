# Public single-request deployment

The prepared target is a public Cloud Run service with no sign-in, one Uvicorn worker, and one active processing request. No cloud deployment has been performed. Container execution requires a working Docker daemon; local Python tests do not establish Linux/container or Cloud Run readiness.

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
uv run --locked pytest -q
npm test --prefix frontend
bash -n infra/deploy.sh infra/smoke.sh
docker build --platform linux/amd64 -t review-analyzer .
bash infra/smoke.sh review-analyzer
```

The smoke check runs offline CPU sentiment inference under 1 CPU/4 GiB, checks the non-root user, starts HTTP, checks readiness/dashboard/docs, and confirms analysis fails cleanly without credentials. It makes no paid Gemini calls. Set SMOKE_PORT to change the default 18080 host port. GitHub Actions runs tests followed by these container checks on Linux; it does not deploy, push images, or require cloud secrets. Initial model downloads during builds need network access.

## Cloud prerequisites

Use a billing-enabled project and an existing chosen region. Install current gcloud, uv, and Node.js. Authenticate the operator through normal Google credentials. Enable Cloud Run Admin, Artifact Registry, Cloud Build, Secret Manager, and the Telemetry/Trace APIs described in [observability](observability.md).

Create an Artifact Registry Docker repository named `review-analyzer`, a runtime service account, and a Secret Manager secret named `gemini-api-key`. Grant that account Secret Accessor on this secret only, plus the Trace/Telemetry roles in the observability guide. Use ADC, with no static service-account keys in images or source.

The deploying identity needs build, repository push, Cloud Run deploy/public-access configuration, and permission to act as the runtime account. Cloud Build needs repository write access; Cloud Run needs image pull access. Organization policies may disallow public access. Resolve policy constraints explicitly rather than broadening IAM grants.

## Deployment

First pass container checks and commit the release. The script refuses a dirty working tree, runs Python/frontend tests, tags the Cloud Build image with the full commit SHA, deploys the public resource profile, routes 100% of service traffic to the latest revision, and polls readiness with Gemini configuration required. The health check confirms presence of credentials, not their validity. Use a numeric secret version for reproducible releases.

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

Inspect actual memory, cold-start time, end-to-end latency, provider failures, and deadline handling under the configured resources. Confirm Cloud Trace spans, correlated JSON logs, Gemini token usage, and native Cloud Run metrics. These checks remain environment-dependent and have not been executed here. The sentiment classifier is English-oriented; country selection does not guarantee equal sentiment accuracy across languages.

Official references: [public access](https://docs.cloud.google.com/run/docs/authenticating/public), [deploy flags](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy), [instance limits](https://docs.cloud.google.com/run/docs/configuring/max-instances), [uv CI integration](https://docs.astral.sh/uv/guides/integration/github/).
