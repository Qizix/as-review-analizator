# Apple Store Review Analysis API

A company test assignment to collect app reviews, calculate rating metrics, and generate sentiment insights through a REST API.

Review collection, sentiment classification, and Gemini-generated actionable insights are ready. The REST API is still pending.

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run scripts/fetch_reviews.py 389801252 --output output/reviews.json
uv run scripts/analyze_sentiment.py output/reviews.json
uv run scripts/generate_insights.py output/reviews_sentiment.json
```

Optional arguments: `--country us`, `--count 100`, and `--seed 42`.
The collector samples unique reviews from Apple's ten most recent feed pages and saves reviews plus collection metadata. It reports shortfalls and failed pages explicitly.

Sentiment classification uses CardiffNLP's model on CPU and adds a positive, negative, or neutral label with a confidence score to each review. The first run downloads the model. Results default to `output/reviews_sentiment.json`; use `--output PATH` to choose another file.

Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in your environment before generating insights. This step sends preprocessed reviews to Gemini 3.5 Flash-Lite, grouped by sentiment. Negative reviews support improvements; positive and neutral reviews provide context. It saves short actions grouped by priority and recurrence to `output/reviews_sentiment_insights.json` and a matching `.md` report. Use `--output PATH.json` to choose another location.

If your API key is in `.env`, run `uv run --env-file .env scripts/generate_insights.py output/reviews_sentiment.json`.

Run the offline tests with `uv run python -m unittest discover -s tests -v`.

- [Assignment requirements](TEST_TASK.md)
- [Research checklist](RESEARCH_TODO.md)
- [Project instructions for Claude](CLAUDE.md)
- [Collector behavior and verification](docs/fetch_reviews.md)
- [Sentiment classification](docs/analyze_sentiment.md)
- [Gemini insight generation](docs/generate_insights.md)

API run instructions and the sample insights report will be added as the API is built.
