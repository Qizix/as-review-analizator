import assert from "node:assert/strict";
import test from "node:test";
import {
  escapeHTML,
  filterIssues,
  prepareDashboard,
  normalizeAppInput,
  dashboardSources,
} from "../data.mjs";

function fixture() {
  const issue = {
    category: "Crashes",
    priority: "high",
    frequency: 2,
    evidence_ids: ["a", "b"],
  };
  const metrics = {
    metadata: { app_id: "123", country: "us" },
    total_reviews: 3,
    ratings: {
      rated_reviews: 3,
      average: 3,
      counts: { 1: 1, 2: 0, 3: 1, 4: 0, 5: 1 },
    },
    sentiments: {
      classified_reviews: 3,
      counts: { positive: 1, neutral: 0, negative: 2 },
    },
    issues: {
      items: [issue],
      covered_negative_reviews: 2,
      negative_reviews: 2,
    },
  };
  const report = {
    metadata: { app_id: "123", country: "us" },
    summary: {
      total_reviews: 3,
      sentiments: { positive: 1, neutral: 0, negative: 2 },
    },
    insights: {
      high: {
        repeated: [{ ...issue, action: "Investigate crashes." }],
        single: [],
      },
      medium: { repeated: [], single: [] },
      low: { repeated: [], single: [] },
    },
  };
  const reviews = {
    reviews: [
      {
        id: "a",
        title: "Good but crashes",
        text: "Crashes sometimes",
        rating: 5,
        sentiment: { label: "negative" },
      },
      {
        id: "b",
        title: "Broken",
        text: "Crashes constantly",
        rating: 1,
        sentiment: "negative",
      },
      {
        id: "c",
        title: "Useful",
        text: "I like it",
        rating: 3,
        sentiment: "positive",
      },
    ],
  };
  return { metrics, report, reviews };
}

test("joins real evidence and keeps ratings independent from sentiment", () => {
  const { metrics, report, reviews } = fixture();
  const before = JSON.stringify({ metrics, report, reviews });
  const result = prepareDashboard(metrics, report, reviews);
  assert.equal(result.average, 3);
  assert.equal(result.coverage, 1);
  assert.equal(result.issues[0].action, "Investigate crashes.");
  assert.equal(result.reviewMap.get("a").rating, 5);
  assert.equal(result.reviewMap.get("a").label, "negative");
  assert.equal(JSON.stringify({ metrics, report, reviews }), before);
});

test("supports reports without source text", () => {
  const { metrics, report } = fixture();
  const result = prepareDashboard(metrics, report);
  assert.equal(result.reviewMap.size, 0);
  assert.equal(result.issues[0].frequency, 2);
});

test("empty samples produce null values, not invented zero percentages", () => {
  const { metrics, report } = fixture();
  metrics.total_reviews = report.summary.total_reviews = 0;
  metrics.ratings = {
    average: null,
    rated_reviews: 0,
    counts: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
  };
  metrics.sentiments = {
    classified_reviews: 0,
    counts: { positive: 0, neutral: 0, negative: 0 },
  };
  report.summary.sentiments = { positive: 0, neutral: 0, negative: 0 };
  metrics.issues = {
    items: [],
    covered_negative_reviews: 0,
    negative_reviews: 0,
  };
  report.insights.high.repeated = [];
  const result = prepareDashboard(metrics, report, { reviews: [] });
  assert.equal(result.average, null);
  assert.equal(result.coverage, null);
  assert.deepEqual(result.issues, []);
});

test("rejects stale counts, mixed samples, and mismatched evidence", () => {
  const changes = [
    (m, r) => {
      m.ratings.average = 5;
    },
    (m, r) => {
      m.ratings.counts[1] = -1;
    },
    (m, r) => {
      r.summary.total_reviews = 4;
    },
    (m, r) => {
      r.metadata.app_id = "456";
    },
    (m, r) => {
      r.insights.high.repeated[0].evidence_ids = ["wrong", "b"];
    },
    (m, r) => {
      m.issues.items[0].frequency = 8;
    },
    (m, r) => {
      m.issues.covered_negative_reviews = 1;
    },
  ];
  for (const change of changes) {
    const { metrics, report } = fixture();
    change(metrics, report);
    assert.throws(() => prepareDashboard(metrics, report));
  }
});

test("rejects positive evidence and mixed review files", () => {
  const { metrics, report, reviews } = fixture();
  reviews.reviews[0].sentiment = "positive";
  assert.throws(
    () => prepareDashboard(metrics, report, reviews),
    /negative reviews/,
  );
});

test("priority and recurrence filters keep their intended subsets", () => {
  const issues = [
    { priority: "high", frequency: 2 },
    { priority: "high", frequency: 1 },
    { priority: "medium", frequency: 3 },
  ];
  assert.equal(filterIssues(issues).length, 3);
  assert.deepEqual(filterIssues(issues, "high", "single"), [issues[1]]);
  assert.deepEqual(filterIssues(issues, "all", "repeated"), [
    issues[0],
    issues[2],
  ]);
  assert.deepEqual(filterIssues(issues, "low"), []);
});

test("review text and categories are escaped before HTML rendering", () => {
  assert.equal(
    escapeHTML('<img src=x onerror="alert(1)"> &'),
    "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; &amp;",
  );
});

test("normalizes Apple URLs and rejects unrelated URLs", () => {
  assert.deepEqual(normalizeAppInput("389801252"), {
    app_id: "389801252",
    country: "us",
  });
  assert.deepEqual(
    normalizeAppInput(
      "https://apps.apple.com/gb/app/example/id389801252?foo=bar",
    ),
    { app_id: "389801252", country: "gb" },
  );
  assert.equal(
    normalizeAppInput("https://apps.apple.com/gb/app/example/id389801252", "us")
      .country,
    "us",
  );
  for (const value of [
    "0",
    "not-an-id",
    "https://evil.test/id123",
    "http://apps.apple.com/us/app/id123",
  ])
    assert.throws(() => normalizeAppInput(value));
});

test("adapts the unified REST response without changing denominators or evidence", () => {
  const { metrics, reviews } = fixture();
  const response = {
    app: { app_id: "123", country: "us", name: "Example" },
    collection: metrics.metadata,
    metrics: {
      total_reviews: 3,
      ratings: metrics.ratings,
      sentiment: metrics.sentiments,
      issues: metrics.issues,
    },
    insights: {
      issues: [{ ...metrics.issues.items[0], evidence_quotes: [] }],
      recommendations: [
        { category: "Crashes", action: "Investigate crashes." },
      ],
    },
    reviews: reviews.reviews,
    metadata: {},
  };
  const sources = dashboardSources(response);
  const data = prepareDashboard(
    sources.metrics,
    sources.report,
    sources.reviews,
  );
  assert.equal(data.metadata.app_name, "Example");
  assert.equal(data.coverage, 1);
  assert.equal(data.issues[0].action, "Investigate crashes.");
});
