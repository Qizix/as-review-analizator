# Research TODO

The user will provide research answers step by step. Record each answer under its question and check the item when answered.

Start with separate scripts that perform each task independently, then connect them into one project.

- [x] How to fetch reviews from the Apple Store?
  Use Apple's RSS JSON endpoint with HTTPX, fetching pages 1–10 sequentially with timeouts and bounded retries. Implemented in `scripts/fetch_reviews.py`.
- [x] How to handle missing data?
  Skip invalid review records, deduplicate by review ID, save available reviews, and explicitly report any shortfall or page failures in collection metadata.
- [x] How to extract data from the API or scraping results?
  Read ID, title, content, and rating labels from `feed.entry`; require nonempty text fields and ratings from 1 to 5.
- [ ] How to preprocess data for analysis?
- [x] How to perform sentiment analysis (positive, negative, neutral) on the reviews?
  Use the Transformers text-classification pipeline with `cardiffnlp/twitter-roberta-base-sentiment-latest` on CPU, batch size 8, truncation enabled, and maximum length 512. Implemented in `scripts/analyze_sentiment.py`, adding a label and confidence score to each review.
- [ ] How to identify common keywords or phrases in negative reviews?
- [ ] How to provide actionable insights, such as areas of improvement based on review sentiment?
- [ ] What is the API design schema?
- [ ] How to visualize insights (e.g., sentiment distribution, rating distribution)?
- [ ] How to deploy the API on a cloud platform (e.g., AWS, Heroku, or Google Cloud)?
