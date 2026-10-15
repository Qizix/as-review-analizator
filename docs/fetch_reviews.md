# Review collection

`app/services/collector.py` owns the HTTPX collector shared by FastAPI, the pipeline, and `scripts/fetch_reviews.py`. The API keeps review data in memory and returns JSON. Only explicit CLI output and development test fixtures write files.

## CLI contract

`uv run scripts/fetch_reviews.py APP_ID --country us --count 100 --output PATH`

App IDs must be positive ASCII digit strings. Country codes are normalized to lowercase and checked against ISO alpha-2 codes; acceptance does not guarantee Apple provides that storefront. Count must be positive. The compatibility CLI requires an output path, creates parent directories, and overwrites an existing file.

## Source order

`collect_reviews` tries catalog JSON, RSS JSON, then embedded webpage JSON. It advances when the accumulated unique valid reviews fall short of the requested count, including after empty responses or failures. Later sources are skipped once enough reviews are available. Partial results survive fallback failures. All sources use the requested country; collection never broadens into other storefronts.

### Catalog JSON

Collection uses `https://apps.apple.com/api/apps/v1/catalog/{country}/apps/{app_id}/reviews` with `l=en-US`, `offset=0`, `platform=web`, and `additionalPlatforms=appletv,ipad,iphone,mac`.

Responses must contain a `data` list. Records have a string `id` and an `attributes` object containing `title`, `review`, and integer `rating`. The collector emits `{id, title, text, rating}`, preserves source text, skips malformed records, and accepts ratings only from 1 through 5. Boolean and string ratings are invalid.

The collector starts at offset zero and fetches up to ten pages sequentially, stopping as soon as the requested number of unique valid reviews is found. Apple's observed page size is ten. It reads the offset from the response's `next` link and reconstructs the request against the configured Apple endpoint, retaining the storefront, language, and platform parameters. It never requests an arbitrary host from a pagination link. Missing `next` or an empty data list ends collection. A missing, noninteger, or nonincreasing next offset records a pagination error and stops with the valid records already collected, unless the requested count was already reached.

The first failed page ends this source so fallback can start. Later failed pages advance by the observed default page size of ten. Remaining catalog pages are marked failed once the source deadline expires.

### RSS JSON

The first fallback requests `https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json`, starting at page one, for at most ten pages. A `feed` object is required; missing `entry` means a successful empty feed, and a singleton entry object is accepted. A `next` link allows the next sequential page, but its URL is never requested. Missing `next`, empty entries, a failure, or enough unique reviews ends RSS collection. A page containing only duplicates can still lead to a subsequent page with new reviews.

Records use `id.label`, `title.label`, `content.label`, and `im:rating.label`. Rating labels are converted only when they are exactly strings `1` through `5`; other values are counted as invalid. Some live feeds return zero entries even for popular apps, so this source is opportunistic.

### Embedded webpage JSON

The last fallback resolves the real app slug through `https://itunes.apple.com/lookup?id={app_id}&country={country}`. It verifies the returned app ID, HTTPS Apple host, and storefront path, then constructs `https://apps.apple.com/{country}/app/{slug}/id{app_id}`. This avoids relying on the `/_/id...` shortcut.

`ServerDataParser` locates the `application/json` script with ID `serialized-server-data`, regardless of attribute order. `parse_web_page` recursively walks its JSON for `$kind: Review` objects and reads `id`, `title`, `contents`, and `rating`. Positive integer IDs are converted to strings; ratings are validated without coercing booleans or strings. Missing server state or malformed JSON is an error rather than an empty result. Only one page is fetched: Apple-selected reviews, generally most helpful, rather than a historical archive.

The JSON walk is adapted from [aivars/app-store-review-scraper](https://github.com/aivars/app-store-review-scraper/tree/36d8e8f38563af35a21db9606e6cd672e3db0d5a). No package dependency, subprocess, or browser is added. The adaptation uses HTMLParser instead of the original attribute-order-dependent regular expression. The MIT license is retained in [third-party notices](third-party-notices.md).

## Request budgets

Subsequent pages pause 0.5 seconds. `fetch_page` handles all requests, including lookup and HTML, with three attempts, maximum 20-second HTTPX operation timeouts, and one- then two-second retry delays. Request errors, HTTP 429/5xx, malformed JSON, and unexpected response structures are retried. Other HTTP failures fail immediately.

Each source has at most 60 seconds. With a caller's monotonic deadline, each source is additionally limited to an equal share of the remaining time among itself and the remaining fallbacks, reserving time for later attempts. Timeouts and delays shrink with that budget. Expired deadlines prevent further HTTP requests and record failures. HTTPX timeouts apply per network operation; this remains a cooperative budget rather than forced cancellation.

## Selection order

Records are deduplicated by ID across all attempted sources, retaining the first valid occurrence in encounter order: catalog, then RSS, then webpage. Original text, including line breaks, is preserved. Processing stops immediately at the requested count, including in the middle of a response page. No additional pages or fallback sources are requested once the count is reached. If source/page limits, failures, empty responses, or deadlines prevent reaching the count, all collected reviews are returned with an explicit shortfall.

The default is the first 100 unique valid reviews. This is a deliberate user-approved change from the original assignment's random-sampling requirement; `TEST_TASK.md` retains the original brief. The legacy `seed` API field, CLI option, collector argument, and metadata field remain accepted/echoed for compatibility but have no effect. Metadata marks the strategy as `selection: first_unique`; `available_count` counts retained reviews, not an estimate of all reviews available from Apple.

This collection is not a random or representative sample of all historical reviews. Catalog response order, recent RSS, and most-helpful webpage selections create selection bias. Apple can change data between requests, so results are not a transactional snapshot. `l=en-US` controls the catalog UI locale; review language is not filtered.

## JSON metadata and shortfalls

Metadata includes app ID, country, requested/available/actual counts, shortfall, seed, collection timestamp, source, sampling scope, language, successful page numbers and offsets, failed pages with errors, and invalid/duplicate counts. The output review structure remains compatible with sentiment, insights, metrics, and dashboard consumers.

`sources` lists only attempted sources, in order, with `source`, `added_count`, `fetched_pages`, `failed_pages`, `invalid_records`, and `duplicate_records`. Contributions count retained unique reviews. Invalid/duplicate counts include only records examined before reaching the requested count. Top-level `fetched_pages` and `fetched_offsets` remain catalog-only. Top-level `failed_pages` aggregates failures tagged with their source; invalid and duplicate counts aggregate all sources. `source` and the legacy `sampling_pool` field describe source order and fallback use. Failures remain visible even when a later source recovers enough reviews, preserving existing CLI exit semantics and consumer warnings.

A shortfall never causes padding, duplicate selection, or fabricated reviews. Zero accessible entries do not prove the app has no reviews. Failed HTTP requests remain distinguishable from successful empty responses through `failed_pages`.

The compatibility CLI prints shortfalls explicitly. Shortfall alone returns status 0; failed pages return status 1 after saving partial results. Write failures return status 1, and invalid arguments return argparse status 2 before network access.

## Verification

`tests/test_fetch_reviews.py` replays the real catalog fixture for pagination, source order, seed independence, and stopping mid-page at the requested count. `tests/test_review_fallbacks.py` covers source order, skipped fallbacks, recovery, partial top-ups, duplicate-only RSS pages followed by new reviews, cross-source deduplication, real RSS fixture parsing, nested server state, malformed records, unexpected lookup URLs, all-source failures, and expired deadlines. Oversized catalog, RSS, and webpage responses verify ordered truncation at the requested count.

Run `uv run --locked pytest -q`. Live collection depends on Apple's availability and rate limits; production requests never fall back to recorded fixtures.

## Implementation ownership

`catalog_pages`, `rss_pages`, and `web_pages` adapt the real sources to `SourcePage` results. Source-specific pagination and failures remain internal to each adapter. `ReviewCollection` retains unique reviews, counts examined invalid/duplicate records, stops exactly at the requested count, and derives metadata once from source attempts. `collect_catalog_reviews` preserves the catalog-only tooling contract using the same accumulator.
