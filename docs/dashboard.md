# Dashboard

Jinja2 serves buildless HTML, CSS, and vanilla JavaScript from the same FastAPI application. `/static/app.mjs` renders the page and holds browser state; `/static/data.mjs` validates reports and adapts the unified response. There is no chart library, bundler, or runtime CDN: bars and the sentiment line are CSS. System fonts only.

## Layout

One screen, insight first:

- **Header**: the app chip (app, country, review count) opens the analyze form as a popover; before any report the form is shown inline. Open report and Export JSON sit beside it.
- **Snapshot card**: ratings (average plus 1–5 star bars) and sentiment (one thin line plus three rows). Bars are scaled to the largest star count; sentiment percentages use classified reviews as the denominator.
- **What to fix**: issues sorted by priority, then frequency. Priority is a three-step signal plus a word, frequency a bar plus count. Filters: priority tabs and Repeated only (`filterIssues`). A final row, Not assigned to an issue, shows negative reviews outside every issue (`negative - covered`) and is hidden while filters are active.
- **Reviews pane**: sticky on wide screens, stacked below on narrow ones. It lists reviews for the current selection, with issue tags and source ID.

## Selection model

A single `lens` drives everything: `{type: "issue" | "star" | "sentiment" | "gap", value}` or `null`. Clicking a star row, sentiment row, issue row, or the unassigned row sets the lens; clicking the same item again clears it. Only one item can be selected at a time. The first issue is selected when a report opens. Selection updates the snapshot (non-matching sentiment segments fade), the selected row highlight, and the reviews pane. `keepFocus` restores keyboard focus after re-rendering by `data-key`.

Reports opened without a reviews file have no source text: the pane says so and lists the evidence IDs of the selected issue.

## Workflow

Users supply an app ID or HTTPS Apple URL, country, and count. URL normalization accepts `apps.apple.com` and `itunes.apple.com`; a URL change populates country, and the country field at submission overrides that value. Server-side validation remains authoritative.

The page sends `POST /api/analyze`, shows an honest busy state, and validates the returned report before replacing current browser state. Failures show an error while retaining a previous report. Browser analysis requests abort after 290 seconds. Gemini credentials never enter frontend code.

JSON saves the full report locally in the browser without an export API call. Star ratings and textual sentiment are shown independently.

## Accessibility

Every control is a native `button`, `input`, or `select`; selected state uses `aria-pressed`. The sentiment line has a text alternative. The import dialog is a native `dialog`. Reduced-motion preferences disable smooth scrolling. At phone width the board stacks and the reviews pane becomes a fixed-height scroll area.

## Saved reports

Open report accepts the unified exported JSON. Compatibility inputs include separate legacy metrics/insights files with optional source reviews and an earlier exported bundle. Missing source text exposes evidence IDs rather than invented reviews. Parsing is local, with a 20 MB limit per file; opening a saved report does not call Gemini.

Validation checks totals and distributions, averages, app/country consistency, issue/evidence agreement, union coverage, and optional raw review ratings/labels/counts. Undefined values display as an em dash. Empty issue lists and filters have explicit states. Untrusted strings are escaped before HTML insertion.

Browser state is transient. Reloading does not restore server-stored reports; export JSON to retain a result. No separate frontend deployment, background jobs, or polling is required. `npm test --prefix frontend` (no install needed) verifies the data contract and filters; browser QA verifies live rendering and selection.

The country dropdown uses the API collector’s accepted ISO codes and displays full English region names with browser `Intl.DisplayNames`, sorted alphabetically. United States is selected initially. App Store URLs select their country when the code is supported; requests still send lowercase two-letter codes.
