# Apple Store Review Analysis API

A company test assignment to collect app reviews, calculate rating metrics, and generate sentiment insights through a REST API.

The first collector is ready. API development and sentiment analysis are still pending.

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run scripts/fetch_reviews.py 389801252 --output output/reviews.json
```

Optional arguments: `--country us`, `--count 100`, and `--seed 42`.
The collector samples unique reviews from Apple's ten most recent feed pages and saves reviews plus collection metadata. It reports shortfalls and failed pages explicitly.

Run the offline tests with `uv run python -m unittest discover -s tests -v`.

- [Assignment requirements](TEST_TASK.md)
- [Research checklist](RESEARCH_TODO.md)
- [Project instructions for Claude](CLAUDE.md)
- [Collector behavior and verification](docs/fetch_reviews.md)

API run instructions and the sample insights report will be added as the API is built.
