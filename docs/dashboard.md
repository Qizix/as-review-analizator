# Dashboard

Jinja2 serves buildless HTML, CSS, and vanilla JavaScript from the same FastAPI application. `/static/app.mjs` renders the page and holds browser state; `/static/data.mjs` validates reports and adapts the unified response. There is no chart library, bundler, or runtime CDN: bars and the sentiment line are CSS. System fonts only.

## Layout

One screen, insight first:

- **Start screen**: before any report the page shows only a centered Analyze an app title and the form (ID or URL, country, review count, Analyze). When a report installs, the same form element moves into the header popover and the Export JSON button appears.
- **Header**: the app chip (app, country, review count) opens the analyze form as a popover. Export JSON sits beside it.
- **Snapshot card**: ratings (average plus 1–5 star bars) and sentiment (one thin line plus three rows). Bars are scaled to the largest star count; sentiment percentages use classified reviews as the denominator.
- **What to fix**: issues sorted by priority, then frequency. Priority is a three-step signal plus a word, frequency a bar plus count. Filters: priority tabs and Repeated only (`filterIssues`). A final row, Not assigned to an issue, shows negative reviews outside every issue (`negative - covered`) and is hidden while filters are active.
- **Reviews pane**: sticky on wide screens, stacked below on narrow ones. It lists reviews for the current selection, with issue tags and source ID.

## Selection model

A single `lens` drives everything: `{type: "issue" | "star" | "sentiment" | "gap", value}` or `null`. Clicking a star row, sentiment row, issue row, or the unassigned row sets the lens; clicking the same item again clears it. Only one item can be selected at a time. The first issue is selected when a report opens. Selection updates the snapshot (non-matching sentiment segments fade), the selected row highlight, and the reviews pane. `keepFocus` restores keyboard focus after re-rendering by `data-key`.

## Workflow

Users supply an app ID or HTTPS Apple URL, country, and count. URL normalization accepts `apps.apple.com` and `itunes.apple.com`. The app input's `input` event suggests the URL country immediately when the URL parses, only while `countrySelected` is false. A user `change` event on the country dropdown sets that flag for the page session; subsequent app-input edits cannot overwrite the selection. Submission always passes the dropdown value to `normalizeAppInput`, overriding the URL country. A Polish URL followed by a United States selection therefore posts `country: "us"`. Server-side validation remains authoritative.

The page sends `POST /api/analyze`, shows an honest busy state, and validates the returned report before replacing current browser state. Failures show an error while retaining a previous report. Browser analysis requests abort after 290 seconds. Gemini credentials never enter frontend code.

JSON saves the full report locally in the browser without an export API call. Star ratings and textual sentiment are shown independently.

## Accessibility

Every control is a native `button`, `input`, or `select`; selected state uses `aria-pressed`. The sentiment line has a text alternative. Reduced-motion preferences disable smooth scrolling. At phone width the board stacks and the reviews pane becomes a fixed-height scroll area.

## Report data

Validation checks totals and distributions, averages, app/country consistency, issue/evidence agreement, union coverage, and review ratings/labels/counts. Undefined values display as an em dash. Empty issue lists and filters have explicit states. Untrusted strings are escaped before HTML insertion.

Browser state is transient. Reloading does not restore server-stored reports; export JSON to retain a result. No separate frontend deployment, background jobs, or polling is required. `npm test --prefix frontend` (no install needed) verifies the data contract and filters; browser QA verifies live rendering and selection.

The country dropdown uses the API collector’s accepted ISO codes and displays full English region names with browser `Intl.DisplayNames`, sorted alphabetically. United States is selected initially. App Store URLs suggest supported countries until the user chooses one; requests send lowercase two-letter codes from the dropdown.
