import assert from "node:assert/strict";
import test from "node:test";
import {
  escapeHTML,
  filterIssues,
  prepareDashboard,
  normalizeAppInput,
} from "../data.mjs";

function fixture() {
  const issue = { category: "Crashes", priority: "high", frequency: 2, evidence_ids: ["a", "b"] };
  return {
    app: { app_id: "123", country: "us", name: "Example" },
    collection: { app_id: "123", country: "us" },
    metrics: {
      total_reviews: 3,
      ratings: { rated_reviews: 3, average: 3, counts: { 1: 1, 2: 0, 3: 1, 4: 0, 5: 1 } },
      sentiment: { classified_reviews: 3, counts: { positive: 1, neutral: 0, negative: 2 } },
      issues: { items: [issue], covered_negative_reviews: 2, negative_reviews: 2 },
    },
    insights: {
      issues: [{ ...issue, evidence_quotes: [] }],
      recommendations: [{ ...issue, action: "Investigate crashes." }],
    },
    reviews: [
      { id: "a", title: "Good but crashes", text: "Crashes sometimes\nPlease fix", rating: 5, sentiment: { label: "negative" } },
      { id: "b", title: "Broken", text: "Crashes constantly", rating: 1, sentiment: "negative" },
      { id: "c", title: "Useful", text: "I like it", rating: 3, sentiment: "positive" },
    ],
    metadata: {},
  };
}

test("consumes canonical reports and keeps ratings independent from sentiment", () => {
  const response = fixture();
  const before = JSON.stringify(response);
  const result = prepareDashboard(response);
  assert.equal(result.average, 3);
  assert.equal(result.coverage, 1);
  assert.equal(result.issues[0].action, "Investigate crashes.");
  assert.equal(result.reviewMap.get("a").rating, 5);
  assert.equal(result.reviewMap.get("a").label, "negative");
  assert.equal(result.reviewMap.get("a").text, "Crashes sometimes\nPlease fix");
  assert.equal(result.metadata.app_name, "Example");
  assert.equal(JSON.stringify(response), before);
});

test("empty reports produce null averages and coverage", () => {
  const response = fixture();
  response.metrics.total_reviews = 0;
  response.metrics.ratings = { rated_reviews: 0, average: null, counts: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 } };
  response.metrics.sentiment = { classified_reviews: 0, counts: { positive: 0, neutral: 0, negative: 0 } };
  response.metrics.issues = { items: [], covered_negative_reviews: 0, negative_reviews: 0 };
  response.insights = { issues: [], recommendations: [] };
  response.reviews = [];
  const result = prepareDashboard(response);
  assert.equal(result.average, null);
  assert.equal(result.coverage, null);
  assert.deepEqual(result.issues, []);
});

test("rejects inconsistent distributions, duplicate records, and invalid evidence", () => {
  const changes = [
    (r) => { r.metrics.ratings.average = 5; },
    (r) => { r.metrics.ratings.counts[1] = -1; },
    (r) => { r.metrics.total_reviews = 4; },
    (r) => { r.app.country = "gb"; },
    (r) => { r.insights.recommendations[0].evidence_ids = ["wrong", "b"]; },
    (r) => { r.metrics.issues.items[0].frequency = 8; },
    (r) => { r.metrics.issues.covered_negative_reviews = 1; },
    (r) => { r.reviews[0].id = "b"; },
    (r) => { r.reviews[0].sentiment = "positive"; },
    (r) => { r.insights.recommendations.push(r.insights.recommendations[0]); },
    (r) => { r.insights.issues.push(r.insights.issues[0]); },
    (r) => { r.metrics.issues.items[0].priority = "urgent"; },
    (r) => { r.reviews = null; },
  ];
  for (const change of changes) {
    const response = fixture();
    change(response);
    assert.throws(() => prepareDashboard(response));
  }
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

test("mixed-sentiment issue support uses negative-only coverage", () => {
  const response = fixture();
  for (const issue of [response.metrics.issues.items[0], response.insights.issues[0], response.insights.recommendations[0]])
    issue.evidence_ids = ["a", "c"];
  response.metrics.issues.items[0].negative_review_count = 1;
  response.metrics.issues.items[0].negative_review_fraction = 0.5;
  response.metrics.issues.covered_negative_reviews = 1;
  response.metadata.mode = "fast";
  const result = prepareDashboard(response);
  assert.equal(result.issues[0].frequency, 2);
  assert.equal(result.issues[0].negative_review_count, 1);
  assert.equal(result.issues[0].fraction, 0.5);
  assert.equal(result.coverage, 0.5);
  assert.equal(result.metadata.mode, "fast");
  response.metrics.issues.items[0].negative_review_count = 2;
  assert.throws(() => prepareDashboard(response));
});

test("supported issues with zero negative reviews have null fractions", () => {
  const response = fixture();
  for (const review of response.reviews) review.sentiment = "positive";
  response.metrics.sentiment.counts = { positive: 3, neutral: 0, negative: 0 };
  response.metrics.issues.negative_reviews = 0;
  response.metrics.issues.covered_negative_reviews = 0;
  response.metrics.issues.items[0].negative_review_count = 0;
  response.metrics.issues.items[0].negative_review_fraction = null;
  const result = prepareDashboard(response);
  assert.equal(result.issues[0].frequency, 2);
  assert.equal(result.issues[0].fraction, null);
  assert.equal(result.covered, 0);
  assert.equal(result.coverage, null);
});
