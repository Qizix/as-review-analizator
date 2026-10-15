export const PRIORITIES = ["high", "medium", "low"];
export const SENTIMENTS = ["positive", "neutral", "negative"];

export function normalizeAppInput(value, country = null) {
  const input = value.trim();
  if (/^[0-9]+$/.test(input) && !/^0+$/.test(input))
    return { app_id: input, country: country || "us" };
  let url;
  try {
    url = new URL(input);
  } catch {
    throw new Error("Enter a numeric app ID or an App Store URL.");
  }
  const match = url.pathname.match(/\/id([0-9]+)(?:\/|$)/);
  if (
    url.protocol !== "https:" ||
    !["apps.apple.com", "itunes.apple.com"].includes(url.hostname) ||
    !match ||
    /^0+$/.test(match[1])
  )
    throw new Error("Enter a valid Apple App Store URL containing an app ID.");
  const region = url.pathname.split("/")[1];
  return {
    app_id: match[1],
    country:
      country || (/^[a-z]{2}$/i.test(region) ? region.toLowerCase() : "us"),
  };
}

export function escapeHTML(value) {
  return String(value).replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
}

function count(value, name) {
  if (!Number.isSafeInteger(value) || value < 0)
    throw new Error(`${name} must be a nonnegative integer.`);
  return value;
}

export function prepareDashboard(response) {
  const { app, collection, metrics, insights, reviews } = response || {};
  if (!app || !collection || !metrics || !Array.isArray(reviews) ||
      !Array.isArray(metrics.issues?.items) || !Array.isArray(insights?.issues) ||
      !Array.isArray(insights?.recommendations))
    throw new Error("The server returned an incomplete analysis report.");
  for (const key of ["app_id", "country"])
    if (app[key] != null && collection[key] != null && app[key] !== collection[key])
      throw new Error("App information and collection describe different storefronts.");
  const total = count(metrics.total_reviews, "Total reviews");
  const rated = count(metrics.ratings?.rated_reviews, "Rated reviews");
  const classified = count(metrics.sentiment?.classified_reviews, "Classified reviews");
  const stars = [1, 2, 3, 4, 5].map((star) => ({
    star, count: count(metrics.ratings.counts?.[star], `${star}-star count`),
  }));
  const sentiments = SENTIMENTS.map((label) => ({
    label, count: count(metrics.sentiment.counts?.[label], `${label} count`),
  }));
  if (reviews.length !== total || rated > total || classified > total ||
      stars.reduce((sum, item) => sum + item.count, 0) !== rated ||
      sentiments.reduce((sum, item) => sum + item.count, 0) !== classified)
    throw new Error("Review totals and distribution counts do not match.");
  const average = rated ? stars.reduce((sum, item) => sum + item.star * item.count, 0) / rated : null;
  if (average === null ? metrics.ratings.average !== null :
      !Number.isFinite(metrics.ratings.average) || Math.abs(average - metrics.ratings.average) > 1e-8)
    throw new Error("Average rating does not match the rating distribution.");
  const reviewMap = new Map();
  for (const review of reviews) {
    const label = typeof review.sentiment === "string" ? review.sentiment : review.sentiment?.label;
    if (typeof review.id !== "string" || !review.id.trim() || reviewMap.has(review.id) ||
        typeof review.text !== "string" || !review.text.trim() ||
        (label != null && !SENTIMENTS.includes(label)) ||
        (review.rating != null && (!Number.isInteger(review.rating) || review.rating < 1 || review.rating > 5)))
      throw new Error("Review records contain invalid values or duplicate IDs.");
    reviewMap.set(review.id, { ...review, label });
  }
  for (const item of sentiments)
    if (reviews.filter((review) => reviewMap.get(review.id).label === item.label).length !== item.count)
      throw new Error("Source reviews have different sentiment counts.");
  for (const item of stars)
    if (reviews.filter((review) => review.rating === item.star).length !== item.count)
      throw new Error("Source reviews have different rating counts.");
  const actions = new Map();
  for (const action of insights.recommendations) {
    if (typeof action.category !== "string" || actions.has(action.category) ||
        typeof action.action !== "string" || !action.action.trim())
      throw new Error("Recommendations contain invalid or duplicate actions.");
    actions.set(action.category, action);
  }
  const supported = new Map(insights.issues.map((issue) => [issue.category, issue]));
  const covered = new Set();
  const categories = new Set();
  const negative = sentiments.find((item) => item.label === "negative").count;
  const issues = metrics.issues.items.map((issue) => {
    const action = actions.get(issue.category);
    const support = supported.get(issue.category);
    const ids = issue.evidence_ids;
    if (typeof issue.category !== "string" || !issue.category.trim() || categories.has(issue.category) ||
        !PRIORITIES.includes(issue.priority) || !action || !support ||
        action.priority !== issue.priority || support.priority !== issue.priority ||
        !Array.isArray(ids) || !ids.length || new Set(ids).size !== ids.length ||
        count(issue.frequency, "Issue frequency") !== ids.length ||
        !ids.every((id) => typeof id === "string" && reviewMap.get(id)?.label === "negative") ||
        [action, support].some((item) => !Array.isArray(item.evidence_ids) ||
          item.evidence_ids.length !== ids.length || new Set(item.evidence_ids).size !== ids.length ||
          !ids.every((id) => item.evidence_ids.includes(id))))
      throw new Error("Issue evidence must consistently reference negative source reviews.");
    categories.add(issue.category);
    ids.forEach((id) => covered.add(id));
    return { ...issue, action: action.action, fraction: ids.length / negative };
  }).sort((a, b) => PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority) ||
    b.frequency - a.frequency || a.category.localeCompare(b.category));
  if (actions.size !== issues.length || supported.size !== issues.length || insights.issues.length !== issues.length ||
      metrics.issues.covered_negative_reviews !== covered.size || metrics.issues.negative_reviews !== negative)
    throw new Error("Issue coverage does not match the evidence IDs.");
  return {
    total, stars, rated, average, classified, sentiments, negative, issues,
    covered: covered.size, coverage: negative ? covered.size / negative : null,
    reviewMap, metadata: { ...collection, ...app, app_name: app.name },
  };
}

export function filterIssues(issues, priority = "all", recurrence = "all") {
  return issues.filter(
    (issue) =>
      (priority === "all" || issue.priority === priority) &&
      (recurrence === "all" ||
        (recurrence === "repeated"
          ? issue.frequency > 1
          : issue.frequency === 1)),
  );
}
