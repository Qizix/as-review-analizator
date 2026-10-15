# Instagram review report

Live collection: **2026-10-15T17:51:56.665483+00:00** · App ID **389801252** · **US storefront**.

This is a real run of the current pipeline, not a synthetic fixture. The [full JSON report](instagram-report.json) includes all original reviews, sentiment scores, evidence IDs/quotes, model versions, prompt hash, timings, and provider-reported token usage. The checked-in example is a documentation artifact; the HTTP service does not persist reports.

## Sample and metrics

Collected **100 unique reviews** from Apple catalog pages 1–10, retaining the first unique records in response order. Shortfall: **0**; failed pages: **0**. This is not a random or representative sample of all historical reviews.

Average rating: **3.21/5** across 100 rated reviews.

| Stars | Reviews | Percentage |
|---|---:|---:|
| 1 | 21 | 21% |
| 2 | 14 | 14% |
| 3 | 22 | 22% |
| 4 | 9 | 9% |
| 5 | 34 | 34% |

| Sentiment | Reviews | Percentage |
|---|---:|---:|
| Positive | 22 | 22% |
| Neutral | 17 | 17% |
| Negative | 61 | 61% |

Ratings and sentiment are independent: a high star rating can accompany a textual complaint. Positive and neutral comments are context, not issue evidence.

**6 issue groups** cover **31/61 negative reviews (50.8%)**. The remaining **30** negative reviews were not assigned to an actionable issue. No extra issues were added to increase coverage.

## Prioritized actions

Each frequency counts unique negative reviews. A review can support more than one issue, so frequencies do not add up to coverage. Recurrence describes mentions in this sample, not independently reproduced bugs.

### High priority · Repeated mentions

- **Wrongful Account Suspensions — 7 reviews (11.5% of negative feedback):** Investigate and overhaul automated moderation systems to prevent false account suspensions and provide a clear human review process.
  Evidence IDs: `12121708410`, `12852567106`, `12952644986`, `12984943506`, `13724533726`, `14073430418`, `3939277903`.
- **Account Security and Hacking — 5 reviews (8.2% of negative feedback):** Improve account security features, implement reliable recovery options, and provide direct human support for hacked accounts.
  Evidence IDs: `12263274514`, `12824613852`, `13539617732`, `1856643354`, `8684593784`.
- **Inappropriate Content and Ineffective Reporting — 4 reviews (6.6% of negative feedback):** Enhance content moderation to effectively remove inappropriate material and ensure accurate review of reported user content.
  Evidence IDs: `10283660915`, `10883770917`, `3939277903`, `9585154526`.

### Medium priority · Repeated mentions

- **Excessive Ads and Algorithm Feed — 7 reviews (11.5% of negative feedback):** Reduce the frequency of sponsored ads and refine the feed algorithm to prioritize content from followed accounts over suggested posts.
  Evidence IDs: `11838405385`, `12266423021`, `12603831315`, `13506455961`, `3807229083`, `6015025104`, `9192706644`.
- **App Bugs and Crashing — 4 reviews (6.6% of negative feedback):** Investigate and resolve application crashes, freezing, and loading errors during regular use and media creation.
  Evidence IDs: `10883770917`, `4095983874`, `7359412437`, `9801173304`.

### Low priority · Repeated mentions

- **Unpopular Navigation and Layout Changes — 6 reviews (9.8% of negative feedback):** Redesign the user interface to restore intuitive navigation and revert unwanted layout changes to buttons and tabs.
  Evidence IDs: `13108172048`, `14384326138`, `3306646480`, `6652888712`, `6659540902`, `6748778355`.

All six groups recur; no single-mention issues were returned. Categories and actions above preserve the model output. Complaints are reviewer reports, not independently verified defects; priorities and semantic grouping require human judgment.

## Provenance and reproduction

The pipeline ran in **31.0 seconds**, excluding initial model loading, using `cardiffnlp/twitter-roberta-base-sentiment-latest` and `gemini-3.5-flash-lite`. No collection warnings or preprocessing truncations were reported.

```bash
uv run --env-file .env review-analyzer 389801252 --country us --count 100 --output output/instagram-report.json
```

This command performs a new live collection and Gemini request; review order, model interpretation, metrics, and timing can change. Exact source data and output for this sample remain in the linked JSON.
