# Nebula: Spiritual Guidance — sample report

App ID: **1459969523** · Storefront: **United States** · Collected: **2026-10-15T19:04:18.293116+00:00**.

This report summarizes the supplied application export. The [original JSON](nebula-report.json) is preserved unchanged, including source reviews, evidence quotations, model metadata, and timings. No new collection or Gemini request was made for this example.

![Nebula dashboard with the paywall issue selected](nebula-dashboard.png)

## Ratings and sentiment

**100 unique reviews**, no collection shortfall, and no failed pages. Average rating: **2.86/5**.

| Stars | Reviews | Percentage |
|---|---:|---:|
| 1 | 41 | 41% |
| 2 | 8 | 8% |
| 3 | 8 | 8% |
| 4 | 10 | 10% |
| 5 | 33 | 33% |

| Sentiment | Reviews | Percentage |
|---|---:|---:|
| Positive | 35 | 35% |
| Neutral | 16 | 16% |
| Negative | 49 | 49% |

Ratings and sentiment are independent. Positive and neutral reviews provide context; only negative reviews support issue counts.

**Five issues cover 33/49 negative reviews (67.3%).** The other 16 negative reviews remain unassigned. Counts may overlap across issues; coverage counts each review once.

## Prioritized improvements

### High priority · Repeated mentions

- **Hidden Subscriptions and Difficult Cancellation Process — 15 reviews (30.6% of negative feedback):** Simplify the cancellation process within the app and ensure subscription terms and free trials are transparently communicated without forcing unexpected annual or recurring plans.
  Evidence IDs: `10226790734`, `11206406865`, `11582897081`, `11864621987`, `11889644785`, `12051799393`, `12091047645`, `12136193056`, `12190264147`, `12359073037`, `12888127585`, `5950653524`, `7550467798`, `7559050403`, `9087926809`.
- **Billing and Refund Disputes — 10 reviews (20.4% of negative feedback):** Investigate payment processing practices and ensure clear, hassle-free refund policies for users who request cancellations immediately after unintentional subscription charges.
  Evidence IDs: `11970977550`, `12190264147`, `12359073037`, `12414601556`, `13005999915`, `13254291446`, `13619149817`, `5998819267`, `7546839366`, `9238108781`.

### Medium priority · Repeated mentions

- **Aggressive Paywalls and Excessive Monetization — 8 reviews (16.3% of negative feedback):** Review the pricing model to make core features like birth charts and basic readings more accessible instead of paywalling almost all content.
  Evidence IDs: `11434430922`, `12000783686`, `12170359896`, `12186255683`, `12336334249`, `12338461977`, `13600420685`, `4811839528`.
- **Distressing or Inaccurate Astrological Content — 2 reviews (4.1% of negative feedback):** Review daily horoscope generation algorithms to remove alarming predictions such as potential deaths and ensure astrological calculations are accurate.
  Evidence IDs: `11830779768`, `12186255683`.

### Low priority · Single mentions

- **Inappropriate Content Filtering on Community Forums — 1 reviews (2.0% of negative feedback):** Adjust the automated content moderation rules to prevent standard, helpful messages from being falsely flagged as inappropriate data.
  Evidence IDs: `10055138343`.

## Reading the screenshot

The selected paywall issue has eight supporting reviews, so the review panel shows eight rather than all 100. The unassigned row represents 16 negative reviews without an issue. All original review text remains available in the JSON.

The exported pipeline time is about 20.5 seconds, excluding initial model loading. The export records CardiffNLP sentiment classification and Gemini insights. Collection keeps the first unique accessible reviews; this is not a representative random sample. Issue labels, priorities, and actions are model interpretations of reviewer reports, not verified findings.

To run a new analysis (results may change):

```bash
uv run --env-file .env review-analyzer 1459969523 --country us --count 100 --output output/nebula-report.json
```
