# Apple Store Review Analysis API

A company test assignment to collect app reviews, calculate rating metrics, and generate sentiment insights through a REST API.

Review collection, sentiment classification, and negative-review phrase ranking are ready. API development and actionable insights are still pending.

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run scripts/fetch_reviews.py 389801252 --output output/reviews.json
uv run scripts/analyze_sentiment.py output/reviews.json
uv run scripts/extract_phrases.py output/reviews_sentiment.json
```

Optional arguments: `--country us`, `--count 100`, and `--seed 42`.
The collector samples unique reviews from Apple's ten most recent feed pages and saves reviews plus collection metadata. It reports shortfalls and failed pages explicitly.

Sentiment classification uses CardiffNLP's model on CPU and adds a positive, negative, or neutral label with a confidence score to each review. The first run downloads the model. Results default to `output/reviews_sentiment.json`; use `--output PATH` to choose another file.

Phrase extraction ranks words and phrases of up to three tokens by the number of negative reviews mentioning them. Generic candidates are filtered after extraction, preserving negation. Results default to `output/reviews_sentiment_phrases.json`; use `--output PATH` to choose another file.

Run the offline tests with `uv run python -m unittest discover -s tests -v`.

- [Assignment requirements](TEST_TASK.md)
- [Research checklist](RESEARCH_TODO.md)
- [Project instructions for Claude](CLAUDE.md)
- [Collector behavior and verification](docs/fetch_reviews.md)
- [Sentiment classification](docs/analyze_sentiment.md)
- [Negative-review phrase ranking](docs/extract_phrases.md)

API run instructions and the sample insights report will be added as the API is built.
