# Research TODO

The user will provide research answers step by step. Record each answer under its question and check the item when answered.

Start with separate scripts that perform each task independently, then connect them into one project.

- [x] How to fetch reviews from the Apple Store?
  Use Apple’s App Store catalog reviews endpoint with HTTPX, following offsets for up to ten pages with timeouts, pacing, and bounded retries. Shared collector: `app/services/collector.py`; CLI: `scripts/fetch_reviews.py`.
- [x] How to handle missing data?
  Skip invalid review records, deduplicate by review ID, save available reviews, and explicitly report any shortfall or page failures in collection metadata.
- [x] How to extract data from the API or scraping results?
  Read ID, title, content, and rating labels from `feed.entry`; require nonempty text fields and ratings from 1 to 5.
- [x] How to preprocess data for analysis?
  Before Gemini, convert emoji to descriptive words, normalize whitespace, and limit titles to 200 characters and texts to 4,000 while preserving their beginning and end. Mark truncated fields and keep source files intact.
- [x] How to perform sentiment analysis (positive, negative, neutral) on the reviews?
  Use the Transformers text-classification pipeline with `cardiffnlp/twitter-roberta-base-sentiment-latest` on CPU, batch size 8, truncation enabled, and maximum length 512. Implemented in `scripts/analyze_sentiment.py`, adding a label and confidence score to each review.
- [x] How to identify common keywords or phrases in negative reviews?
  Replace phrase-frequency extraction with Gemini grouping of equivalent complaints into evidence-linked issue categories. Recurrence is calculated locally from unique supporting negative-review IDs.
- [x] How to provide actionable insights, such as areas of improvement based on review sentiment?
  Use Gemini 3.5 Flash-Lite with grouped negative/positive/neutral JSON and a system prompt. Negative reviews are evidence; other sentiments provide context. Validate structured output and evidence IDs, then generate short actions grouped by priority and recurrence. Empty issue lists are valid. Implemented in `scripts/generate_insights.py`.
- [x] What is the API design schema?
  FastAPI exposes analysis, collection, metrics, and health routes. Unified reports contain app/collection details, independent metrics, evidence-linked issues/recommendations, source reviews, and processing metadata. See `docs/architecture.md`.
- [x] How to visualize insights (e.g., sentiment distribution, rating distribution)?
  Jinja2 and vanilla JavaScript use bundled Chart.js to render API JSON, with distributions, issue frequencies/coverage, evidence drilldowns, raw reviews, and browser-local JSON downloads.
- [ ] How to deploy the API on a cloud platform (e.g., AWS, Heroku, or Google Cloud)?
  Docker and private Cloud Run files are prepared. Actual image build/deployment remain unverified; the user requested deployment files only. See `docs/deployment.md`.
