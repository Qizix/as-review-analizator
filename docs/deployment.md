# Docker and Cloud Run

Deployment files are prepared only. No cloud resource, image push, or deployment was performed. `INFRASTRUCTURE.md` defines the initial resource profile; validate it on Cloud Run before production use.

## Container

Docker builds frontend assets in Node 22 and uses Python 3.12 with uv 0.11.26 and locked production dependencies. Linux Torch is resolved from the explicit official CPU index, excluding CUDA/NVIDIA packages. macOS uses its native wheel with CPU inference selected explicitly.

`scripts/download_model.py` fetches tokenizer/config files and one weight format at the pinned CardiffNLP revision. Weights are bundled in `/app/models/sentiment`; runtime sets `HF_HUB_OFFLINE=1`. Gemini inference remains external. The final container is unprivileged, runs one Uvicorn worker, respects PORT, and stores no reports. Docker context excludes credentials, outputs, local environments, models, node_modules, and Git metadata.

```bash
docker build --platform linux/amd64 -t review-analyzer .
docker run --rm --env-file .env -p 8080:8080 review-analyzer
```

Open `http://localhost:8080/`. Docker must be running. Image execution was not verified here because no daemon was available; local services/API/assets were tested separately.

## Cloud prerequisites

Use an existing billing-enabled project and chosen region. Enable Cloud Run Admin, Artifact Registry, Cloud Build, and Secret Manager. Create a Docker repository named `review-analyzer`, a runtime service account, and a Secret Manager secret named `gemini-api-key`. Grant the runtime account Secret Accessor on that secret only. Keep credentials out of images/frontend.

The operator needs build/push/deploy and service-account impersonation permissions; Cloud Build needs repository write access, and Cloud Run needs image pull access. Use existing organizational identity policies rather than broad owner grants.

`infra/cloud-run.yaml` documents the service settings. Replace project, region, image, account, and secret placeholders before using it with `gcloud run services replace`. It references a secret version without containing the value.

Alternatively, with prerequisites already configured:

```bash
bash infra/deploy.sh PROJECT_ID REGION SERVICE_ACCOUNT_EMAIL
```

The script builds/pushes through Cloud Build and deploys a private service. It creates no project, registry repository, account, or secret. Override `GEMINI_SECRET_NAME` for another existing secret. This command performs deployment and can incur charges; it has not been run.

## Resource profile

| Setting | Value |
|---|---|
| CPU / GPU | 1 vCPU / none |
| RAM | 4 GiB |
| Minimum / maximum instances | 0 / 1 |
| Concurrency / workers | 1 / 1 |
| Request timeout | 300 seconds |
| Cooperative analysis budget | 280 seconds |
| Torch threads | 1 |
| Paid endpoint limit | 3 attempts/minute per worker |

The startup probe checks `/api/v1/health` after the classifier loads. Cloud Run provides HTTPS and scale-to-zero. Private access requires IAM invocation. For a private service, use `gcloud run services proxy app-store-review-analyzer --project PROJECT_ID --region REGION --port 8080` to test through a local browser without placing credentials in frontend code.

Cloud Logging receives structured application events. Verify cold-start time, end-to-end latency at 100 reviews, actual container memory, retries, and deadline behavior under the configured resources. Local Mac RSS measurements are not a Cloud Run guarantee.

The in-memory rate limit resets on restart and is not a distributed quota system. The private deployment supplies an access boundary. Making it public requires an explicitly chosen access policy and suitable server-side limits. Free allowances do not guarantee zero charges for builds, images, runtime, logs, or Gemini.
