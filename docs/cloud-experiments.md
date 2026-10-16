# Cloud Run experiment — 16 October 2026

## Release

- Project `obrio-test-task-api`, service `app-store-review-analyzer`, region `europe-central2`.
- Serving revision `app-store-review-analyzer-00005-x56`, 100% traffic.
- Source commit `29d893cbea4141e8f395fcdfb7466ce47059496e`.
- Cloud Build `2a7a38db-a800-4e9e-a992-1c03ae03a8d6`, europe-west1; both dual and fast-only images passed offline inference/dependency, non-root, readiness, assets, and missing-credential smoke checks. Only the dual-mode image was published. Build duration was 252.62 seconds.
- Image digest `sha256:414488dbd3012b0ba7d77dd70f4641b8cf97405ea6736299e5d9e55e65c7a28a`; compressed registry size 1,043,325,432 bytes.
- Preserved one vCPU, 4 GiB RAM, one Torch thread, batch size 1, HTTP concurrency 8, one worker, manual one-instance scaling, public access, runtime account, and Secret Manager version 1.
- Processing was paused on `00004-cfw`; traffic was explicitly switched there and 503 processing/200 health verified. A full 300-second drain window elapsed. The new digest was staged without traffic before the final switch.
- Rollback processing revision: `app-store-review-analyzer-00003-67b`, not the paused `00004-cfw`. The earlier revision predates the mode parameter; callers must omit mode after rollback and reload the dashboard.

## Method

Six sequential public `POST /api/analyze` calls: fast then quality, repeated three times, app `1459969523`, country `us`, requested count 100. The warm-instance experiment ran from 10:53:40 to 11:01:05 UTC. Each request performed live Apple collection; no fixture was injected into the deployed application. All six collected 100 unique reviews according to correlated collection logs. The first fast attempt had two failed catalog pages and recovered its count through fallback sources; a full count does not imply every source page succeeded.

The experiment driver saves complete responses locally under ignored hydra_outputs, independently validates every successful report, recomputes metrics from review labels and supporting IDs, checks every returned quote against original source text, and hashes original review fields sorted by ID. Full responses are not persisted by the HTTP application. The checked-in [measurements](benchmarks/cloud-run-2026-10-16.json) retain aggregate results and provenance, without review bodies or credentials.

All three successful reports share payload hash `077456173c36cacb63c3f13ce4c9376afe428d04174fb1eb51e1f47ff76e0ff9`, also matching the supplied `examples/nebula-report.json` source reviews. Failed API calls return no reviews, so their payload equality cannot be independently established by the client; their collection count comes from logs. Comparisons of sentiment and returned metrics therefore use successful reports only.

The driver deliberately exits nonzero when any report fails. The six-run experiment completed; its exit status 1 denotes three rejected reports, not a driver interruption.

```bash
uv run --locked --extra quality python -m experiments.benchmark_cloud
```

Hydra configuration: `conf/cloud_benchmark.yaml`. Override base_url, app_id, country, review_count, modes, or repeats. These calls use the deployed provider credentials, consume application allowances, and can incur Gemini/Cloud Run charges. No credentials are sent by the driver. The default is three attempts per mode, with no hidden retries of failed analyses. Provider transport retries and quality's single evidence regeneration remain application behavior.

## Results

| Mode | Valid reports | Successful HTTP seconds | All-attempt median seconds | Semantic repairs | Evidence validation failures |
|---|---:|---|---:|---:|---:|
| Fast | 1/3 | 23.440 | 57.124 | 0 | 2 |
| Quality | 2/3 | 95.343; 110.011 | 98.691 | 2 | 3 |

The quality failures count validation attempts: run 2 failed twice, exhausting its single repair; run 3 failed once and then passed. All three rejected API requests returned HTTP 502 with INSIGHTS_INVALID, reason evidence_quote_not_in_source_excerpt. Gemini returned structured responses, but source provenance validation failed. No invalid report was returned as a success. These safety checks work; the observed report success fractions do not establish production reliability.

The successful fast run returned 40 positive/8 neutral/52 negative labels, seven issues, and 30/52 negative issue coverage (57.7%). Both successful quality runs returned 35 positive/16 neutral/49 negative labels. Quality run 1 returned seven issues and 40/49 coverage (81.6%); run 3 returned eight issues and 42/49 coverage (85.7%). All successful runs had average rating 2.86 and star counts 41/8/8/10/33 for stars 1–5.

The fast labels agreed with quality on 72/100 reviews in each matched comparison. This is not accuracy. There is no human-labeled ground truth, so macro-F1, negative recall, issue precision and recommendation quality remain unmeasured. Higher coverage can include broader or less precise issue groups. Different issue counts across repeated calls demonstrate model variability, not different underlying review counts.

The [README](../README.md#cloud-run-results) includes every attempt's latency/tokens/cost and the median stage timings for verified reports. The JSON records contain exact per-request stage timings, retries, repairs, returned metrics, source hashes, trace IDs, resource profile, token usage and cost breakdown. Successful-only latency is explicitly separated from all-attempt latency and the cost of rejected requests.

## Pricing

Verified 2026-10-16 against the Google Cloud Billing Catalog and [Gemini standard pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.5-flash-lite). [Cloud Run pricing](https://cloud.google.com/run/pricing) explains instance-based billing and allowances.

| Resource | USD list rate |
|---|---:|
| Warsaw instance CPU, SKU 6872-8766-BE5C | $0.0000216 / vCPU-second |
| Warsaw instance memory, SKU 14DC-011F-3982 | $0.0000024 / GiB-second |
| Gemini 3.5 Flash-Lite uncached input | $0.30 / million tokens |
| Reported cached input | $0.03 / million tokens |
| Output, including thinking | $2.50 / million tokens |

For each request, with server duration T, reported prompt P, cached input C, candidate output O and separately reported thinking H:

```text
CPU     = T × 1 × 0.0000216
Memory  = T × 4 × 0.0000024
Input   = (P − C) / 1,000,000 × 0.30
Cache   = C / 1,000,000 × 0.03
Output  = (O + H) / 1,000,000 × 2.50
Total   = CPU + Memory + Input + Cache + Output
```

All six calls reported usage. Per-call trace token totals were checked against request-log cumulative totals; reported total tokens equal prompt plus candidate output with no extra thinking tokens reported. Absent cache counts receive no assumed discount. Quality run 3 reported 26,153 cached tokens across its two generations; its prompt total of 57,459 includes those tokens.

- Successful fast run: $0.0244788; total across three fast attempts $0.0765305; observed cost per valid fast report $0.0765305.
- Successful quality requests: $0.0221533 and $0.0342087; median $0.0281810; all three quality attempts $0.0971221; observed cost per valid quality report $0.0485611.
- Six-attempt processing estimate: $0.1736527.

These estimates use actual reported tokens and allocated compute during the HTTP processing window, including network waits. They are not invoices or guaranteed prices. They exclude free allowances, credits, discounts, taxes, startup, idle time, build, artifact/source storage, networking, Secret Manager, logging and tracing. Unreported usage on failed transport attempts is unknown and is not invented.

Manual one-instance scaling allocates compute continuously: `(1 × 0.0000216 + 4 × 0.0000024) × 86,400 = $2.69568` per 24 hours before allowances. This daily baseline already includes request-window CPU/memory; do not add those portions twice when constructing a daily budget. Gemini usage is additional. The dual image keeps RoBERTa loaded even when fast skips local inference.

## Infrastructure and observability

- Deployment dashboard, API docs, JavaScript and health returned 200. The live health response advertises both modes.
- During analysis: health 200 in 173.5 ms client elapsed; concurrent collection 409 in 55.2 ms with Retry-After 5. These are single observations, not latency percentiles.
- Six request summaries correlated with six Cloud Trace traces. Hierarchy checks verified Gemini's parent stage and absence/presence of sentiment.classify for fast/quality. Trace-level input/output token sums matched request totals. No full reviews, prompts or model responses were added to logs or spans.
- New-revision model initialization, including quality imports, was 39.996 seconds; Cloud Monitoring observed one container startup of 50.141 seconds. These were excluded from the warm-request benchmark.
- Valid reports recorded process-lifetime peak RSS up to 907.18 MiB. This is a shared-instance high-water mark, not isolated per-mode memory consumption or billed memory.
- Native CPU, memory, instance count, startup, request count and request-latency series were retrieved successfully. The largest sampled mean CPU utilization was 93.61%; memory 22.11% of the 4 GiB limit. These sampled aggregates include rollout/probes and are not instantaneous per-request peaks. Individual request latency in the README comes from structured completion logs, not a mixed Monitoring histogram.
- The monitoring snapshot includes 14 requests across all paths, including checks, rather than only six benchmark requests. Native metrics can arrive later; exact values and snapshot time are retained in JSON.
- Both Linux container profiles passed Cloud Build smoke checks. Final local preflight: 180 Python tests plus 60 subtests and eight frontend tests. The six new tests validate the cloud experiment's source/metrics verification and payload hashing.

Quality remains the compatibility default. Fast remains opt-in and experimental based on this small experiment; no accuracy or production-success guarantee is claimed for either method. No queue, database, additional deployed service, export endpoint, or server-side report storage was introduced.

## Locate the experiment logs

In project `obrio-test-task-api`, select 16 October 2026, 10:53–11:02 UTC in Logs Explorer and use:

```text
resource.type="cloud_run_revision"
resource.labels.service_name="app-store-review-analyzer"
resource.labels.revision_name="app-store-review-analyzer-00005-x56"
jsonPayload.request_id:"cloud-benchmark-20261016T105340-58110f"
```

The JSON measurement file maps each attempt to its request_id and trace_id. Use those IDs to inspect exact stage durations, validation warnings and Gemini usage. Native request metrics include health/docs/probe requests, so they are not the denominator for the six-attempt analysis success fraction.
