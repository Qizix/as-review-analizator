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
let dashboard;
let sources;
let canonicalReport = null;
let charts = [];
let reviewPage = 0;
let analyzing = false;
let priorityFilter = "all";
let recurrenceFilter = "all";
const percent = (value) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;
const colors = { positive: "#4e9988", neutral: "#a6afb9", negative: "#d97371" };

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

function ratingChart(data) {
  return `<div class="rating-plot"><canvas id="rating-canvas" role="img" aria-label="Rating counts: ${data.stars.map((item) => `${item.star} stars ${item.count}`).join(", ")}"></canvas></div>
    <div class="rating-explore">${[...data.stars]
      .reverse()
      .map(
        (item) =>
          `<button data-rating="${item.star}" aria-label="Explore ${item.count} ${item.star}-star reviews">${item.star} ★ <span>${data.rated ? Math.round((item.count / data.rated) * 100) + "%" : "—"}</span></button>`,
      )
      .join("")}</div>`;
}

function sentimentChart(data) {
  return `<div class="donut"><canvas id="sentiment-canvas" role="img" aria-label="Sentiment: ${data.sentiments.map((item) => `${item.count} ${item.label}`).join(", ")}"></canvas>
    <div class="donut-center"><strong>${data.classified}</strong><span>classified reviews</span></div></div>
    <div class="legend">${data.sentiments.map((item) => `<button data-sentiment="${item.label}" aria-label="Explore ${item.count} ${item.label} reviews"><span class="key" style="background:${colors[item.label]}"></span><span>${item.label[0].toUpperCase() + item.label.slice(1)}<small>${item.count} reviews</small></span><strong>${percent(data.classified ? item.count / data.classified : null)}</strong></button>`).join("")}</div>`;
}

function installReport(metrics, report, reviews) {
  const next = prepareDashboard(metrics, report, reviews);
  sources = { metrics, report, reviews };
  canonicalReport = null;
  reviewPage = 0;
  dashboard = next;
  priorityFilter = recurrenceFilter = "all";
  const date = new Date(next.metadata.collected_at);
  const collected = Number.isNaN(date.getTime())
    ? "Collection date unavailable"
    : `Collected ${date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`;
  reportElement.innerHTML = `<section class="hero" id="overview"><div><div class="eyebrow">APP STORE REVIEW REPORT</div><h1>Your review snapshot.</h1><p>Understand the feedback. Find the next thing worth fixing.</p></div>
    <div class="dataset"><span class="pill"><span class="dot"></span>${h(next.metadata.app_name || `App ${next.metadata.app_id || "report"}`)} <span class="muted">· ${h((next.metadata.country || "").toUpperCase())}</span></span><span>${h(collected)} · ${next.total}-review sample</span></div></section>
    <section class="stats" aria-label="Report summary">
      <article class="stat"><div class="stat-title">Reviews analyzed</div><div class="stat-value">${next.total}</div><div class="stat-foot">${next.rated} rated · ${next.classified} classified</div><div class="stat-accent"></div></article>
      <article class="stat"><div class="stat-title">Average rating</div><div class="stat-value">${next.average == null ? "—" : next.average.toFixed(2)} <small>/ 5</small></div><div class="stat-foot">Across ${next.rated} rated reviews</div><div class="stat-accent"></div></article>
      <article class="stat"><div class="stat-title">Negative sentiment</div><div class="stat-value">${percent(next.classified ? next.negative / next.classified : null)}</div><div class="stat-foot">${next.negative} of ${next.classified} classified reviews</div><div class="stat-accent"></div></article>
      <article class="stat"><div class="stat-title">Issue coverage</div><div class="stat-value">${percent(next.coverage)}</div><div class="stat-foot">${next.covered} of ${next.negative} negative reviews</div><div class="stat-accent"></div></article>
    </section>
    <section class="charts" aria-label="Rating and sentiment charts">
      <article class="panel"><div class="panel-heading"><div><h2>Rating distribution</h2><p class="panel-sub">How your sample scores the app.</p></div><span class="tiny-label">1–5 stars</span></div><div class="rating-chart">${ratingChart(next)}</div><div class="chart-footer"><span>${next.rated} rated · ${next.total - next.rated} unrated</span><span>Select a bar to explore reviews ↗</span></div></article>
      <article class="panel"><div class="panel-heading"><div><h2>Sentiment mix</h2><p class="panel-sub">What the review text is telling you.</p></div><span class="tiny-label">3 classes</span></div><div class="sentiment-layout">${sentimentChart(next)}</div><div class="chart-note">Ratings and sentiment are independent. A five-star review can still describe a problem. ${next.total - next.classified ? `${next.total - next.classified} reviews are unclassified.` : ""}</div></article>
    </section>
    <section class="secondary" aria-label="Issue frequency and coverage">
      <article class="panel"><div class="panel-heading"><div><h2>Most-mentioned issues</h2><p class="panel-sub">Unique negative reviews mentioning each issue.</p></div><span class="tiny-label">${next.issues.length} issues</span></div><div class="issue-plot" style="height:${Math.max(150, next.issues.length * 44)}px">${next.issues.length ? '<canvas id="issue-canvas" role="img" aria-label="Issue frequency"></canvas>' : '<div class="empty">No supported actionable complaints.</div>'}</div><div class="chart-footer"><span>One review may mention multiple issues.</span><span>Click an issue for evidence ↗</span></div></article>
      <article class="panel coverage-panel"><div class="panel-heading"><div><h2>Feedback accounted for</h2><p class="panel-sub">How much negative feedback has an issue assigned?</p></div></div><div class="coverage-value">${next.coverage == null ? "—" : (next.coverage * 100).toFixed(1)}${next.coverage == null ? "" : "<small>%</small>"}</div><div class="coverage-track" role="img" aria-label="${next.covered} covered and ${next.negative - next.covered} unassigned negative reviews"><span style="width:${(next.coverage || 0) * 100}%"></span></div><div class="coverage-labels"><span><strong>${next.covered}</strong> assigned</span><span><strong>${next.negative - next.covered}</strong> unassigned</span></div><p class="coverage-description">${next.negative ? "Coverage counts each negative review once, even when it mentions several issues." : "There are no negative reviews. Coverage is not applicable."}</p></article>
    </section>
    <section id="issues"><div class="section-head"><div><div class="eyebrow">FROM FEEDBACK TO ACTION</div><h2>What’s worth improving</h2><p>Short, evidence-linked actions. Ordered by impact, then recurrence.</p></div><div class="filters"><div class="segmented" role="group" aria-label="Filter priority">${["all", ...PRIORITIES].map((value) => `<button data-priority="${value}" aria-pressed="${value === "all"}">${value === "all" ? "All priorities" : value[0].toUpperCase() + value.slice(1)}</button>`).join("")}</div><label><span class="sr-only">Filter recurrence</span><select id="recurrence"><option value="all">All recurrence</option><option value="repeated">Repeated reports</option><option value="single">Single reports</option></select></label></div></div><div id="actions" aria-live="polite"></div></section>
    <section class="panel raw-panel" id="raw-reviews"><div class="panel-heading"><div><h2>Source reviews</h2><p class="panel-sub">Keep the original feedback in view.</p></div><span class="tiny-label">${next.reviewMap.size} reviews</span></div>
      <div class="review-controls"><label class="sr-only" for="review-search">Search reviews</label><input id="review-search" type="search" placeholder="Search titles or review text"><label class="sr-only" for="review-filter">Filter review sentiment</label><select id="review-filter"><option value="all">All sentiments</option><option value="positive">Positive</option><option value="neutral">Neutral</option><option value="negative">Negative</option></select></div><div id="review-table"></div></section>
    <aside class="method-note"><strong>About this sample</strong><p>Ratings describe star scores; sentiment describes review text. Actions are model-generated and grounded in negative review IDs. These findings describe the collected sample, not all historical reviews.</p></aside>`;
  renderActions();
  renderReviews();
  reportElement.hidden = false;
  status.hidden = true;
  renderCharts();
  document.querySelector("#export").disabled = false;
}

function renderActions() {
  const visible = filterIssues(
    dashboard.issues,
    priorityFilter,
    recurrenceFilter,
  );
  document.querySelector("#actions").innerHTML =
    PRIORITIES.map((priority) => {
      const items = visible.filter((issue) => issue.priority === priority);
      if (!items.length) return "";
      return `<section class="issue-group"><div class="group-heading">${priority} priority<span class="count">${items.length} ${items.length === 1 ? "issue" : "issues"}</span></div>${items.map((issue) => `<article class="action-card"><div><div class="action-top"><h3>${h(issue.category)}</h3><span class="priority ${priority}">${priority}</span></div><p>${h(issue.action)}</p></div><div class="action-count"><span><strong>${issue.frequency}</strong><small>${issue.frequency === 1 ? "review" : "reviews"} · ${percent(issue.fraction)} of negative</small></span><button class="text-button" data-issue="${dashboard.issues.indexOf(issue)}">View evidence ↗</button></div></article>`).join("")}</section>`;
    }).join("") ||
    `<div class="empty">${dashboard.issues.length ? "No issues match these filters. Try another priority or recurrence." : "No supported actionable complaints were identified."}</div>`;
}

function showEvidence(title, reviews, ids = []) {
  document.querySelector("#evidence-title").textContent = title;
  document.querySelector("#evidence-content").innerHTML = reviews.length
    ? `<p class="muted">${reviews.length} ${reviews.length === 1 ? "review" : "reviews"} in this sample.</p>${reviews.map((review) => `<article class="review"><h3>${h(review.title || "Untitled review")}</h3><div class="review-meta"><span>${review.rating == null ? "Unrated" : `${h(review.rating)}/5 stars`}</span><span>${h(review.label || "Unclassified")}</span><span>ID ${h(review.id)}</span></div><p>${h(review.text)}</p></article>`).join("")}`
    : `<p class="muted">${dashboard.reviewMap.size ? "No matching reviews in this sample." : "Open this report with its reviews JSON file to read the source text."}</p>${ids.length ? `<p class="id-list">${ids.map(h).join("<br>")}</p>` : ""}`;
  document.querySelector("#evidence-dialog").showModal();
}

reportElement.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !dashboard) return;
  if (button.dataset.review) {
    const review = dashboard.reviewMap.get(button.dataset.review);
    showEvidence(review.title || "Review", [review]);
  } else if (button.dataset.page) {
    reviewPage += Number(button.dataset.page);
    renderReviews();
  } else if (button.dataset.priority) {
    priorityFilter = button.dataset.priority;
    document
      .querySelectorAll("[data-priority]")
      .forEach((item) =>
        item.setAttribute("aria-pressed", String(item === button)),
      );
    renderActions();
  } else if (button.dataset.issue !== undefined) {
    const issue = dashboard.issues[Number(button.dataset.issue)];
    showEvidence(
      issue.category,
      issue.evidence_ids
        .map((id) => dashboard.reviewMap.get(id))
        .filter(Boolean),
      issue.evidence_ids,
    );
  } else if (button.dataset.rating) {
    const star = Number(button.dataset.rating);
    showEvidence(
      `${star}-star reviews`,
      [...dashboard.reviewMap.values()].filter(
        (review) => review.rating === star,
      ),
    );
  } else if (button.dataset.sentiment) {
    showEvidence(
      `${button.dataset.sentiment[0].toUpperCase() + button.dataset.sentiment.slice(1)} reviews`,
      [...dashboard.reviewMap.values()].filter(
        (review) => review.label === button.dataset.sentiment,
      ),
    );
  }
});
reportElement.addEventListener("change", (event) => {
  if (event.target.id === "review-filter") {
    reviewPage = 0;
    renderReviews();
  }
  if (event.target.id === "recurrence") {
    recurrenceFilter = event.target.value;
    renderActions();
  }
});
document
  .querySelector("#open-report")
  .addEventListener("click", () =>
    document.querySelector("#import-dialog").showModal(),
  );
document
  .querySelectorAll("[data-close]")
  .forEach((button) =>
    button.addEventListener("click", () => button.closest("dialog").close()),
  );

async function readFile(id, required) {
  const file = document.querySelector(`#${id}`).files[0];
  if (!file) {
    if (required)
      throw new Error("Choose both metrics and insights JSON files.");
    return null;
  }
  if (file.size > 20 * 1024 * 1024)
    throw new Error("Each report file must be smaller than 20 MB.");
  return JSON.parse(await file.text());
}
document
  .querySelector("#import-form")
  .addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    document.querySelector("#import-error").textContent = "";
    try {
      const [metrics, report, reviews] = await Promise.all([
        readFile("metrics-file", true),
        readFile("insights-file", false),
        readFile("reviews-file", false),
      ]);
      if (metrics.app && metrics.metrics && metrics.insights)
        installAnalysis(metrics);
      else if (metrics.metrics && metrics.report)
        installReport(
          metrics.metrics,
          metrics.report,
          reviews || metrics.reviews,
        );
      else {
        if (!report)
          throw new Error(
            "Choose the insights JSON file, or open an exported report.",
          );
        installReport(metrics, report, reviews);
      }
      document.querySelector("#import-dialog").close();
      document.querySelector("#open-report").focus();
    } catch (error) {
      document.querySelector("#import-error").textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
document.querySelector("#export").addEventListener("click", () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(canonicalReport || sources, null, 2)], {
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

function renderCharts() {
  charts.forEach((chart) => chart.destroy());
  charts = [];
  if (!globalThis.Chart)
    throw new Error(
      "Charts are unavailable. Rebuild the frontend assets and reload.",
    );
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = "#63717d";
  Chart.defaults.font.size = 11;
  const base = {
    responsive: true,
    maintainAspectRatio: false,
    animation: reducedMotion ? false : { duration: 350 },
    plugins: { legend: { display: false } },
  };
  const ratingItems = [...dashboard.stars].reverse();
  charts.push(
    new Chart(document.querySelector("#rating-canvas"), {
      type: "bar",
      data: {
        labels: ratingItems.map((item) => `${item.star} stars`),
        datasets: [
          {
            data: ratingItems.map((item) => item.count),
            backgroundColor: "#597eae",
            borderRadius: 4,
            barThickness: 14,
          },
        ],
      },
      options: {
        ...base,
        indexAxis: "y",
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: "#eef1f4" },
            border: { display: false },
          },
          y: { grid: { display: false }, border: { display: false } },
        },
        plugins: {
          ...base.plugins,
          tooltip: {
            callbacks: {
              label: (context) =>
                `${context.raw} reviews · ${percent(dashboard.rated ? context.raw / dashboard.rated : null)} of rated`,
            },
          },
        },
        onClick: (_, elements) => {
          if (elements.length) {
            const star = ratingItems[elements[0].index].star;
            showEvidence(
              `${star}-star reviews`,
              [...dashboard.reviewMap.values()].filter(
                (review) => review.rating === star,
              ),
            );
          }
        },
      },
    }),
  );
  charts.push(
    new Chart(document.querySelector("#sentiment-canvas"), {
      type: "doughnut",
      data: {
        labels: dashboard.sentiments.map((item) => item.label),
        datasets: [
          {
            data: dashboard.sentiments.map((item) => item.count),
            backgroundColor: dashboard.sentiments.map(
              (item) => colors[item.label],
            ),
            borderColor: "#fff",
            borderWidth: 3,
          },
        ],
      },
      options: {
        ...base,
        cutout: "76%",
        plugins: {
          ...base.plugins,
          tooltip: {
            callbacks: {
              label: (context) =>
                `${context.label}: ${context.raw} reviews · ${percent(dashboard.classified ? context.raw / dashboard.classified : null)}`,
            },
          },
        },
        onClick: (_, elements) => {
          if (elements.length) {
            const label = dashboard.sentiments[elements[0].index].label;
            showEvidence(
              `${label} reviews`,
              [...dashboard.reviewMap.values()].filter(
                (review) => review.label === label,
              ),
            );
          }
        },
      },
    }),
  );
  if (dashboard.issues.length) {
    const issues = [...dashboard.issues].sort(
      (a, b) =>
        b.frequency - a.frequency || a.category.localeCompare(b.category),
    );
    charts.push(
      new Chart(document.querySelector("#issue-canvas"), {
        type: "bar",
        data: {
          labels: issues.map((issue) =>
            issue.category.length > 33
              ? `${issue.category.slice(0, 31)}…`
              : issue.category,
          ),
          datasets: [
            {
              data: issues.map((issue) => issue.frequency),
              backgroundColor: issues.map(
                (issue) =>
                  ({ high: "#d97371", medium: "#d4aa6b", low: "#597eae" })[
                    issue.priority
                  ],
              ),
              borderRadius: 3,
              barThickness: 10,
            },
          ],
        },
        options: {
          ...base,
          indexAxis: "y",
          scales: {
            x: {
              beginAtZero: true,
              ticks: { precision: 0 },
              grid: { color: "#eef1f4" },
              border: { display: false },
            },
            y: {
              grid: { display: false },
              border: { display: false },
              ticks: { font: { size: 10 } },
            },
          },
          plugins: {
            ...base.plugins,
            tooltip: {
              callbacks: {
                title: (contexts) => issues[contexts[0].dataIndex].category,
                label: (context) => `${context.raw} unique negative reviews`,
              },
            },
          },
          onClick: (_, elements) => {
            if (elements.length) {
              const issue = issues[elements[0].index];
              showEvidence(
                issue.category,
                issue.evidence_ids
                  .map((id) => dashboard.reviewMap.get(id))
                  .filter(Boolean),
                issue.evidence_ids,
              );
            }
          },
        },
      }),
    );
  }
}

function renderReviews() {
  const query = document
    .querySelector("#review-search")
    .value.toLowerCase()
    .trim();
  const sentiment = document.querySelector("#review-filter").value;
  const reviews = [...dashboard.reviewMap.values()].filter(
    (review) =>
      (sentiment === "all" || review.label === sentiment) &&
      `${review.title || ""} ${review.text}`.toLowerCase().includes(query),
  );
  reviewPage = Math.min(
    Math.max(0, reviewPage),
    Math.max(0, Math.ceil(reviews.length / 10) - 1),
  );
  const start = reviewPage * 10;
  const items = reviews.slice(start, start + 10);
  document.querySelector("#review-table").innerHTML = items.length
    ? `<div class="table-scroll"><table><thead><tr><th>Review</th><th>Rating</th><th>Sentiment</th><th>Source</th></tr></thead><tbody>${items.map((review) => `<tr><td><button class="review-title" data-review="${h(review.id)}">${h(review.title || "Untitled review")}</button><p class="review-excerpt">${h(review.text.slice(0, 180))}${review.text.length > 180 ? "…" : ""}</p></td><td>${review.rating == null ? "—" : `${h(review.rating)}/5`}</td><td><span class="sentiment-badge ${h(review.label || "unclassified")}">${h(review.label || "unclassified")}</span></td><td class="table-id">${h(review.id)}</td></tr>`).join("")}</tbody></table></div><div class="pagination"><span>${start + 1}–${start + items.length} of ${reviews.length} reviews</span><div><button class="button" data-page="-1" ${reviewPage === 0 ? "disabled" : ""}>Previous</button><button class="button" data-page="1" ${start + 10 >= reviews.length ? "disabled" : ""}>Next</button></div></div>`
    : `<div class="empty">${dashboard.reviewMap.size ? "No reviews match these filters." : "Open the report with its review file to browse source text."}</div>`;
}

reportElement.addEventListener("input", (event) => {
  if (event.target.id === "review-search") {
    reviewPage = 0;
    renderReviews();
  }
});

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
      document.querySelector("#country-input").value = normalizeAppInput(
        event.target.value,
      ).country;
    } catch {}
  }
});
document
  .querySelector("#analyze-form")
  .addEventListener("submit", async (event) => {
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
      document.querySelector("#open-report").disabled = true;
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
      document.querySelector("#open-report").disabled = false;
      button.textContent = "Analyze reviews →";
    }
  });

fetchJSON("/api/health")
  .then((health) => {
    if (!health.gemini_configured)
      status.textContent =
        "Gemini credentials are not configured on the server. You can still open saved reports.";
  })
  .catch(() => {
    status.textContent =
      "The API is unavailable. You can still open a saved report.";
  });
