import {
  escapeHTML as h,
  filterIssues,
  prepareDashboard,
  PRIORITIES,
  dashboardSources,
  normalizeAppInput,
} from "/static/data.mjs";

const reportElement = document.querySelector("#report");
const status = document.querySelector("#status");
const switcher = document.querySelector(".app-switch");
const chip = document.querySelector("#app-chip");
const analyzeForm = document.querySelector("#analyze-form");
const countrySelect = document.querySelector("#country-input");
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
const countryOptions = [...countrySelect.options];
for (const option of countryOptions) {
  option.textContent = regionNames.of(option.value.toUpperCase());
}
countrySelect.replaceChildren(
  ...countryOptions.sort((a, b) => a.textContent.localeCompare(b.textContent, "en")),
);
countrySelect.value = "us";
let dashboard;
let canonicalReport = null;
let coveredIds = new Set();
let issueTags = new Map();
let lens = null;
let priorityFilter = "all";
let repeatedOnly = false;
let analyzing = false;
const SIGNAL = { high: 3, medium: 2, low: 1 };
const pct = (value) => (value == null ? "—" : `${Math.round(value * 100)}%`);
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
const cap = (value) => value[0].toUpperCase() + value.slice(1);
const validRating = (rating) => Number.isInteger(rating) && rating >= 1 && rating <= 5;
const isLens = (type, value) => lens?.type === type && lens.value === value;

function countryName(code) {
  try {
    return regionNames.of(String(code).toUpperCase());
  } catch {
    return String(code).toUpperCase();
  }
}

async function fetchJSON(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok)
      throw new Error(`Could not load report data (${response.status}).`);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}

function setSwitcher(open) {
  analyzeForm.hidden = !open;
  chip.setAttribute("aria-expanded", String(open));
}

function keepFocus(render) {
  const key = document.activeElement?.dataset?.key;
  render();
  if (key) reportElement.querySelector(`[data-key="${key}"]`)?.focus();
}

function renderAll() {
  renderSnapshot();
  renderIssues();
  renderReviews();
}

function setLens(type, value) {
  lens = isLens(type, value) ? null : { type, value };
  keepFocus(renderAll);
}

function updateChip(next) {
  const name = next.metadata.app_name || `App ${next.metadata.app_id || "report"}`;
  const date = new Date(next.metadata.collected_at);
  const details = [
    next.metadata.country ? countryName(next.metadata.country) : null,
    plural(next.total, "review"),
  ].filter(Boolean);
  document.querySelector("#app-label").innerHTML =
    `${h(name)} <span class="muted">· ${h(details.join(" · "))}</span>`;
  chip.title = Number.isNaN(date.getTime())
    ? ""
    : `Collected ${date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
}

function installReport(metrics, report, reviews) {
  const next = prepareDashboard(metrics, report, reviews);
  dashboard = next;
  coveredIds = new Set(next.issues.flatMap((issue) => issue.evidence_ids));
  issueTags = new Map();
  for (const issue of next.issues)
    for (const id of issue.evidence_ids)
      issueTags.set(id, [...(issueTags.get(id) || []), issue.category]);
  lens = next.issues.length ? { type: "issue", value: 0 } : null;
  priorityFilter = "all";
  repeatedOnly = false;
  reportElement.innerHTML = `<div class="board">
    <div class="board-main"><section class="card snapshot" id="snapshot" aria-label="Ratings and sentiment"></section><section class="card issues" id="issues" aria-label="Issues to fix"></section></div>
    <aside class="card reviews" id="reviews" aria-label="Reviews"></aside></div>`;
  renderAll();
  updateChip(next);
  if (!switcher.contains(analyzeForm)) {
    switcher.append(analyzeForm);
    analyzeForm.classList.replace("start-form", "pop");
    document.querySelector("#start").hidden = true;
    switcher.hidden = false;
    document.querySelector("#export").hidden = false;
  }
  setSwitcher(false);
  reportElement.hidden = false;
  status.hidden = true;
}

function renderSnapshot() {
  const data = dashboard;
  const maxStar = Math.max(...data.stars.map((item) => item.count), 1);
  const rows = [...data.stars]
    .reverse()
    .map(({ star, count }) => {
      const on = isLens("star", star);
      const dim = lens?.type === "star" && !on;
      const share = data.rated ? ` · ${pct(count / data.rated)}` : "";
      return `<button class="rate${on ? " on" : ""}${dim ? " dim" : ""}" data-key="star-${star}" data-rating="${star}" aria-pressed="${on}" title="${plural(count, "review")}${share}"><span class="label">${star} ★</span><span class="track"><span class="fill star${star}" style="width:${(count / maxStar) * 100}%"></span></span><span class="count">${count}</span></button>`;
    })
    .join("");
  const live = (label) =>
    !lens ||
    lens.type === "star" ||
    (lens.type === "sentiment" ? lens.value === label : label === "negative");
  const parts = data.classified
    ? data.sentiments
        .map(
          (item) =>
            `<span class="part ${item.label}${live(item.label) ? "" : " dim"}" style="flex:${item.count} 1 0"></span>`,
        )
        .join("")
    : '<span class="part none" style="flex:1"></span>';
  const legend = data.sentiments
    .map((item) => {
      const on = isLens("sentiment", item.label);
      return `<button class="legend${on ? " on" : ""}" data-key="sentiment-${item.label}" data-sentiment="${item.label}" aria-pressed="${on}" title="${plural(item.count, "review")}"><span class="dot ${item.label}"></span><span>${cap(item.label)}</span><b>${pct(data.classified ? item.count / data.classified : null)}</b></button>`;
    })
    .join("");
  const summary = data.sentiments
    .map((item) => `${item.count} ${item.label}`)
    .join(", ");
  document.querySelector("#snapshot").innerHTML = `<div class="column">
      <div class="column-head"><h2 title="${data.rated} rated · ${data.total - data.rated} unrated">Ratings</h2><div class="average"><strong>${data.average == null ? "—" : data.average.toFixed(2)}</strong><span>avg of 5</span></div></div>
      <div class="rates">${rows}</div></div>
    <div class="column">
      <div class="column-head"><h2 title="${data.classified} classified · ${data.total - data.classified} unclassified">Sentiment</h2></div>
      <div class="stack" role="img" aria-label="Sentiment: ${summary}">${parts}</div>
      <div class="legends">${legend}</div></div>`;
}

function renderIssues() {
  const data = dashboard;
  const visible = filterIssues(
    data.issues,
    priorityFilter,
    repeatedOnly ? "repeated" : "all",
  );
  const unassigned = data.negative - data.covered;
  const maxCount = Math.max(...data.issues.map((issue) => issue.frequency), unassigned, 1);
  const tabs = ["all", ...PRIORITIES]
    .map(
      (value) =>
        `<button data-key="priority-${value}" data-priority="${value}" aria-pressed="${priorityFilter === value}">${cap(value)}<span>${value === "all" ? data.issues.length : data.issues.filter((issue) => issue.priority === value).length}</span></button>`,
    )
    .join("");
  const rows = visible
    .map((issue) => {
      const index = data.issues.indexOf(issue);
      const on = isLens("issue", index);
      const signal = [1, 2, 3]
        .map((step) => `<i${step <= SIGNAL[issue.priority] ? ' class="on"' : ""}></i>`)
        .join("");
      return `<button class="issue${on ? " on" : ""}" data-key="issue-${index}" data-issue="${index}" aria-pressed="${on}" title="${plural(issue.frequency, "review")} · ${pct(issue.fraction)} of negative"><span class="priority"><span class="signal" aria-hidden="true">${signal}</span><span>${cap(issue.priority)}</span></span><span class="summary"><span class="category">${h(issue.category)}</span><span class="action">${h(issue.action)}</span></span><span class="frequency"><span class="track"><span class="fill negative" style="width:${(issue.frequency / maxCount) * 100}%"></span></span><span class="count">${issue.frequency}</span></span></button>`;
    })
    .join("");
  const gapOn = isLens("gap", 1);
  const gapRow =
    unassigned > 0 && priorityFilter === "all" && !repeatedOnly
      ? `<button class="issue${gapOn ? " on" : ""}" data-key="gap" data-gap="1" aria-pressed="${gapOn}" title="${plural(unassigned, "negative review")} without a specific issue"><span class="priority"><span>—</span></span><span class="summary"><span class="category muted">Not assigned to an issue</span></span><span class="frequency"><span class="track"><span class="fill gap" style="width:${(unassigned / maxCount) * 100}%"></span></span><span class="count">${unassigned}</span></span></button>`
      : "";
  const empty =
    rows || gapRow
      ? ""
      : `<div class="empty">${data.issues.length ? "No issues match these filters." : "No supported actionable complaints were identified."}</div>`;
  document.querySelector("#issues").innerHTML = `<div class="issues-head">
      <h2>What to fix</h2>
      <div class="filters"><div class="segmented" role="group" aria-label="Filter by priority">${tabs}</div>
      <button class="toggle" data-key="repeated" data-repeated="1" aria-pressed="${repeatedOnly}"><span class="box"><svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 5.2l2.1 2.1L8 3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>Repeated only</button></div>
    </div>${rows}${gapRow}${empty}`;
}

function reviewHTML(review) {
  const rated = validRating(review.rating);
  const stars = rated
    ? `<span class="stars" role="img" aria-label="${review.rating} out of 5 stars"><span class="on">${"★".repeat(review.rating)}</span>${"★".repeat(5 - review.rating)}</span>`
    : '<span class="stars muted">Unrated</span>';
  const label = review.label || "unclassified";
  const tags = (issueTags.get(review.id) || [])
    .map((category) => `<span class="tag">${h(category)}</span>`)
    .join("");
  return `<article class="review"><div class="review-top">${stars}<span class="sentiment"><span class="dot ${h(label)}"></span>${h(cap(label))}</span></div><div class="review-title">${h(review.title || "Untitled review")}</div><p class="review-text">${h(review.text)}</p><div class="review-foot"><div class="tags">${tags}</div><span class="mono">${h(review.id)}</span></div></article>`;
}

function lensReviews() {
  const all = [...dashboard.reviewMap.values()];
  if (!lens) return all;
  if (lens.type === "issue")
    return dashboard.issues[lens.value].evidence_ids
      .map((id) => dashboard.reviewMap.get(id))
      .filter(Boolean);
  if (lens.type === "star") return all.filter((review) => review.rating === lens.value);
  if (lens.type === "sentiment") return all.filter((review) => review.label === lens.value);
  return all.filter((review) => review.label === "negative" && !coveredIds.has(review.id));
}

function renderReviews() {
  const items = lensReviews();
  const body = items.length
    ? items.map(reviewHTML).join("")
    : `<div class="empty"><span>No reviews match.</span>${lens ? '<button class="btn" data-key="clear" data-clear="1">Show all reviews</button>' : ""}</div>`;
  document.querySelector("#reviews").innerHTML = `<div class="reviews-head"><h2>Reviews</h2><span class="muted">${plural(items.length, "review")}</span></div><div class="reviews-list">${body}</div>`;
}

reportElement.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !dashboard) return;
  const data = button.dataset;
  if (data.rating) setLens("star", Number(data.rating));
  else if (data.sentiment) setLens("sentiment", data.sentiment);
  else if (data.issue !== undefined) setLens("issue", Number(data.issue));
  else if (data.gap) setLens("gap", 1);
  else if (data.clear) {
    lens = null;
    keepFocus(renderAll);
  } else if (data.priority) {
    priorityFilter = data.priority;
    keepFocus(renderIssues);
  } else if (data.repeated) {
    repeatedOnly = !repeatedOnly;
    keepFocus(renderIssues);
  }
});
chip.addEventListener("click", () => setSwitcher(analyzeForm.hidden));
document.querySelector("#export").addEventListener("click", () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(canonicalReport, null, 2)], {
      type: "application/json",
    }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = "review-atlas-report.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function installAnalysis(response) {
  const { metrics, report, reviews } = dashboardSources(response);
  installReport(metrics, report, reviews);
  canonicalReport = response;
  const warnings = response.metadata?.warnings || [];
  if (warnings.length) {
    status.textContent = warnings.join(" ");
    status.setAttribute("role", "status");
    status.hidden = false;
  }
}

async function postJSON(url, body, timeout = 290000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      const detail = Array.isArray(error.detail)
        ? error.detail
            .map(
              (item) =>
                `${item.loc?.slice(1).join(".") || "Input"}: ${item.msg}`,
            )
            .join(" ")
        : error.detail;
      throw new Error(detail || `The request failed (${response.status}).`);
    }
    return response;
  } catch (error) {
    if (error.name === "AbortError")
      throw new Error(
        "The analysis timed out. Try fewer reviews or retry later.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

document.querySelector("#app-input").addEventListener("change", (event) => {
  if (event.target.value.trim().startsWith("https://")) {
    try {
      const country = normalizeAppInput(event.target.value).country;
      if (countryOptions.some((option) => option.value === country)) {
        countrySelect.value = country;
      }
    } catch {}
  }
});
analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (analyzing) return;
  const button = document.querySelector("#analyze-button");
  try {
    const normalized = normalizeAppInput(
      document.querySelector("#app-input").value,
      document.querySelector("#country-input").value.toLowerCase(),
    );
    analyzing = true;
    button.disabled = true;
    button.textContent = "Analyzing…";
    status.hidden = false;
    status.setAttribute("role", "status");
    status.textContent =
      "Collecting reviews and analyzing feedback. This can take a few minutes.";
    const response = await postJSON("/api/analyze", {
      ...normalized,
      review_count: Number(document.querySelector("#count-input").value),
    });
    installAnalysis(await response.json());
  } catch (error) {
    status.hidden = false;
    status.setAttribute("role", "alert");
    status.textContent = error.message;
  } finally {
    analyzing = false;
    button.disabled = false;
    button.textContent = "Analyze reviews";
  }
});

fetchJSON("/api/health")
  .then((health) => {
    if (!health.gemini_configured)
      status.textContent =
        "Gemini credentials are not configured on the server.";
  })
  .catch(() => {
    status.textContent =
      "The API is unavailable.";
  });
