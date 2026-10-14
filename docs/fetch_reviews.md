# Review collection

`scripts/fetch_reviews.py` is a standalone synchronous HTTPX collector. It uses argparse rather than Hydra, as requested for this script. `pyproject.toml` and `uv.lock` define its environment.

## CLI contract

`uv run scripts/fetch_reviews.py APP_ID --country us --count 100 --seed 42 --output PATH`

`APP_ID` is required and must contain only ASCII digits with a positive numeric value. Country codes are normalized to lowercase and checked against ISO alpha-2 codes; acceptance does not guarantee Apple provides that storefront. Count must be positive. Seed is an integer. Output is required; parent directories are created and an existing output file is overwritten.

## Collection and validation

`collect_reviews` fetches pages 1 through 10 sequentially from `https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json`. `fetch_page` allows three attempts per page, applies HTTPX's 20-second timeout to each network operation, and waits one and then two seconds between retries. These are operation timeouts, not a total collection deadline. Request errors, HTTP 429, HTTP 5xx, invalid JSON, and invalid feed structure are retried. Other HTTP errors are recorded immediately. Redirects are followed by the CLI's HTTPX client.

Failures are recorded per page and collection continues through page 10. A missing `feed.entry` means an empty page; a present entry field must be a list. `extract_review` accepts records only when ID, title, and content labels are nonempty strings and the rating label is one of `1` through `5`. It emits `{id, title, text, rating}` with an integer rating and preserves the original text. Application metadata entries and malformed reviews are skipped.

Records are deduplicated by ID before sampling, retaining the first valid occurrence in page order. `random.Random(seed).sample(pool, min(count, len(pool)))` samples uniformly without replacement. The pool is limited to the accessible ten most recent feed pages, not all historical reviews. Apple's feed can change during collection; the output is not a transactional snapshot.

## Output and exit status

The JSON document has `reviews` and `metadata`. Metadata includes app ID, normalized country, requested/available/actual counts, shortfall, seed, UTC collection timestamp, source, sampling scope, successful pages, failed pages with errors, and invalid/duplicate record counts.

If the pool is smaller than the requested count, every available unique review is returned in seeded sample order, and the CLI prints the exact shortfall to stderr. No padding, duplication, or fabricated records is performed. Shortfall alone returns exit status 0, since collection succeeded. Any failed page returns status 1 after saving partial results, even when enough records remain to meet the requested count. Output write failure returns status 1. Invalid arguments return argparse's status 2 before any HTTP requests.

## Verification

`tests/fixtures/apple_reviews.json` stores real raw responses from Instagram's US feed (app ID `389801252`), the capture timestamp, and the expected 100 review IDs for seed 42. `tests/test_fetch_reviews.py` replays those responses with HTTPX MockTransport and verifies page order, sample size, unique IDs, rating validity, repeatability, expected selected IDs, and a different sample for a different seed. Metadata timestamps are intentionally excluded from equality checks; sampling is deterministic only for an identical ordered pool.

Additional tests cover validation, duplicate and malformed records, shortfalls, bounded retries, HTTP 429/5xx recovery, non-retryable 404s, read timeouts, and malformed JSON. Run them with `uv run python -m unittest discover -s tests -v`.

To recheck the live feed, run the CLI example and assert that `len(reviews) == len({r['id'] for r in reviews}) == 100`, every rating is an integer between 1 and 5, and `metadata.failed_pages` is empty. Live results can differ from the saved fixture because new reviews arrive.
