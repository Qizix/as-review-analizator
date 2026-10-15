# Dashboard

Jinja2 serves buildless HTML, CSS, and vanilla JavaScript from the same FastAPI application. `/static/app.mjs` manages requests and browser state; `/static/data.mjs` validates reports and adapts the unified response. Chart.js renders browser-side plots. The API produces no plot files.

## Assets and workflow

Run `npm ci --prefix frontend` and `npm run --prefix frontend build`. The helper copies pinned Chart.js 4.5.1 and its license into `frontend/vendor/`. Assets and system fonts require no runtime CDN. Docker builds the assets in its Node stage.

Users supply an app ID or HTTPS Apple URL, country, and count. URL normalization accepts `apps.apple.com` and `itunes.apple.com`; a URL change populates country, and the country field at submission overrides that value. Server-side validation remains authoritative.

The page sends `POST /api/analyze`, shows an honest busy state, and validates the returned report before replacing current browser state. Failures show an error while retaining a previous report. Browser analysis requests abort after 290 seconds. Gemini credentials never enter frontend code.

The page includes summary cards, rating bars, a sentiment doughnut, issue-frequency bars with hover tooltips, coverage/unassigned feedback, prioritized actions with recurrence filters, evidence dialogs, and a searchable/paginated source-review table. JSON saves the full report locally in the browser without an export API call. Star ratings and textual sentiment are shown independently.

Chart clicks open matching reviews. Visible rating/sentiment controls and issue action buttons supply keyboard alternatives. Native dialogs support Escape/focus behavior; canvas elements have accessible labels; reduced-motion preferences disable animation. Responsive CSS rearranges the page on narrow screens.

## Saved reports

Open report accepts the unified exported JSON. Compatibility inputs include separate legacy metrics/insights files with optional source reviews and an earlier exported bundle. Missing source text exposes evidence IDs rather than invented reviews. Parsing is local, with a 20 MB limit per file; opening a saved report does not call Gemini.

Validation checks totals and distributions, averages, app/country consistency, issue/evidence agreement, union coverage, and optional raw review ratings/labels/counts. Undefined values display as an em dash. Empty issue lists and filters have explicit states. Untrusted strings are escaped before HTML insertion; chart labels are canvas text.

Browser state is transient. Reloading does not restore server-stored reports; export JSON to retain a result. No separate frontend deployment, background jobs, or polling is required. `npm test --prefix frontend` verifies the data contract and filters; browser QA verifies live rendering and drilldowns.
