# Review collection

`app/services/collector.py` owns the HTTPX collector shared by FastAPI, the pipeline, and `scripts/fetch_reviews.py`. The API keeps review data in memory and returns JSON. Only explicit CLI output and development test fixtures write files.

## CLI contract

`uv run scripts/fetch_reviews.py APP_ID --country us --count 100 --seed 42 --output PATH`

App IDs must be positive ASCII digit strings. Country codes are normalized to lowercase and checked against ISO alpha-2 codes; acceptance does not guarantee Apple provides that storefront. Count must be positive. The compatibility CLI requires an output path, creates parent directories, and overwrites an existing file.

## Catalog endpoint and pagination

Collection uses `https://apps.apple.com/api/apps/v1/catalog/{country}/apps/{app_id}/reviews` with `l=en-US`, `offset=0`, `platform=web`, and `additionalPlatforms=appletv,ipad,iphone,mac`.

Responses must contain a `data` list. Records have a string `id` and an `attributes` object containing `title`, `review`, and integer `rating`. The collector emits `{id, title, text, rating}`, preserves source text, skips malformed records, and accepts ratings only from 1 through 5. Boolean and string ratings are invalid.

The collector starts at offset zero and fetches up to ten pages sequentially. Apple's observed page size is ten. It reads the offset from the response's `next` link and reconstructs the request against the configured Apple endpoint, retaining the storefront, language, and platform parameters. It never requests an arbitrary host from a pagination link. Missing `next` or an empty data list ends collection. A missing, noninteger, or nonincreasing next offset records a pagination error and stops with the valid records already collected.

There is a 0.5-second pause before each subsequent page. Each page allows three attempts, with a maximum 20-second HTTPX operation timeout and one- then two-second retry delays. Request errors, HTTP 429/5xx, invalid JSON, and invalid response structure are retried. Other HTTP failures are recorded immediately. After a page fails, the next attempt advances by the observed default page size of ten. The optional pipeline deadline bounds pauses/timeouts; remaining unattempted pages are marked failed once the deadline expires.

Records are deduplicated by ID, retaining the first valid occurrence. `random.Random(seed).sample(pool, min(count, len(pool)))` samples uniformly without replacement. Sampling covers only the accessible pages, not all historical reviews. The catalog's default response order is preserved when building the pool; it is not described as most recent. Apple can change data during pagination, so the result is not a transactional snapshot.

## JSON metadata and shortfalls

Metadata includes app ID, country, requested/available/actual counts, shortfall, seed, collection timestamp, source, sampling scope, language, successful page numbers and offsets, failed pages with errors, and invalid/duplicate counts. The output review structure remains compatible with sentiment, insights, metrics, and dashboard consumers.

A shortfall never causes padding, duplicate selection, or fabricated reviews. Zero accessible entries do not prove the app has no reviews. Failed HTTP requests remain distinguishable from successful empty responses through `failed_pages`.

The compatibility CLI prints shortfalls explicitly. Shortfall alone returns status 0; failed pages return status 1 after saving partial results. Write failures return status 1, and invalid arguments return argparse status 2 before network access.

## Verification

`tests/test_fetch_reviews.py` replays `tests/fixtures/apple_catalog_reviews.json` (100 real Instagram US reviews from ten catalog responses) through HTTPX MockTransport to verify sequential pagination, unique IDs, valid ratings, seeded sampling, and expected selected IDs. Other tests cover malformed records/responses, empty and final pages, nonstandard next offsets, invalid pagination, partial collection, HTTP 429/5xx retries, timeout recovery, and bounded failures. The older RSS fixture remains historical input for other analysis tests.

Run `uv run --locked pytest -q`. Live collection depends on Apple's availability and rate limits; production requests never fall back to recorded fixtures.
