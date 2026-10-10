# Snapshots Page Redesign — Pain-Point Diagnosis

> **Author:** designer
> **Date:** 2026-10-09
> **Input files (read-only inspection):**
>   - `frontend/src/app/pages/snapshots/snapshots.component.{html,ts,scss}` (342 + 592 + 364 lines)
>   - `frontend/src/app/pages/snapshots/snapshots-table.component.{html,scss}` (198 + 249 lines)
>   - `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.{html,scss}` (282 + 322 lines)
>   - `frontend/src/app/app.scss` (z-ladder, header height constants)
>   - `frontend/src/app/pages/jobs/jobs.component.{html,scss}` (the existing precedent for a "list page with full-height scrollable table")
>   - `.agents/shared/planning/snapshot-uiux/design/mockups/snapshots-page.html` (the previous v1 mockup that shipped in the original spec)
> **Related spec:** `snapshot-uiux::design-spec::v1` (pinned SHA `2ca69147…`; this redesign is a NEW spec, not an amendment — the layout changes are structural, not incremental)

---

## 1 · Inventory of the current page chrome (above the table)

The page's flex column at `:host` → `.snapshots-container` (line 11) renders
five siblings before the table area:

| # | Section | Approx height | Reference | Importance |
|---|---|---|---|---|
| 1 | Page header (h1 + 2-line subtitle paragraph) | ~80px | `snapshots.component.html:3-11` | Medium — subtitle repeats what the page already implies |
| 2 | Toggle section (title + 2-line hint + radios + Apply + dirty hint) | ~110px | `snapshots.component.html:15-81` | High (functional) but DOM-heavy |
| 3 | Metrics strip (2 cards, each up to 4 rows + footer caption) | ~150-180px | `snapshots.component.html:84-161` | Low — promotional/marketing-grade telemetry; doesn't drive any action |
| 4 | `<mat-divider>` | ~1px | `snapshots.component.html:163` | n/a |
| 5 | Filter bar (3 rows: searchables + sort; chips × 2 + tag-mode; tag input + chips + active-filter badge) | ~180-220px | `snapshots.component.html:166-291` | High (functional) but DOM-heavy |
| **Total chrome** | | **~520-590px** | | |

On a 900-px viewport (e.g. a 1080p display with the app header + IDE browser
chrome), this chrome consumes **60-65% of vertical space** before the user
sees a single row. The user-reported pain point #1 ("space cost too much for
less important things") is concretely traceable to:
- the page-subtitle paragraph (line 6-9) — repeats what the H1 says
- the 2-line hint paragraph inside the toggle section (line 19-24) — restates the radios' meaning
- the 2-card metrics strip (line 87-156) — informational only ("Monitoring only — these counters never feed the search ranking" per line 159), eats ~150-180px
- the 3-row filter bar with verbose labels and 2-line tag input (line 199-274) — high functionality, but laid out vertically

## 2 · The table-height problem (pain point #2)

**The table never owns its own height.** Three CSS layers conspire:

1. The page host `:host` (`snapshots.component.scss:3-9`) is `display: block; width: 100%; min-height: 100%`. It has no `height: 100%` to consume the parent's `.app-main` (which is `flex: 1; overflow: hidden` per `app.scss:163-169`).
2. The page container `.snapshots-container` (line 11-17) is `display: flex; flex-direction: column; padding: 24px 32px; gap: 16px; min-width: 1100px`. No `height: 100%` either.
3. The drawer container `.snapshots-drawer-container` (line 349-352) has only `min-height: 600px` — that's a hard floor, not a ceiling. Because the page container is content-height, the table area is too.
4. The table itself `.snapshots-table` (`snapshots-table.component.scss:8-10`) is `display: block; width: 100%` — no height constraint, no `overflow-y: auto` wrapper.
5. The mat-table inside is the default Material table; rows lay out by content, the table grows to fit them all, and the **whole page** scrolls.

**Result:** when a user has 100 snapshots across 4 pages, the page is tall, the user must scroll the whole page (header + chrome moves off-screen), and there's no fixed-headers stickiness on the mat-table itself. The `mat-header-row` has `sticky: true` set (line 168) but sticky only works inside a scrolling ancestor with a constrained height — the page scrolls, so the header isn't actually pinned in a useful way.

The `min-height: 600px` on the drawer container also means the table area is at LEAST 600px — but it never gets bigger, so the user just sees a few rows before the next page's chrome. This is what the user is calling "table height is too short."

## 3 · The detail-panel-height problem (pain point #3)

The drawer is rendered with `<mat-drawer position="end" mode="over">` (`snapshots.component.html:323-329`). Two structural facts:

1. **`mode="over"`** — the drawer overlays the table, doesn't push it. Width: `520px` (line 359). Height: inherits from `.snapshots-drawer-container` (the same container the table sits in).
2. **The drawer's height is bounded by the container's height, which is bounded by the table's height, which is bounded by the page-container's content height.** No `height: 100%` is threaded through. The drawer is at most ~600px tall, often less when the table is short.

The drawer component itself does have `height: 100%; overflow-y: auto` (`.snapshot-detail-drawer > :host`, `snapshot-detail-drawer.component.scss:3-10`), so it WILL scroll internally — but only if it has a real height. With the drawer container sitting at 600px, the user gets ~600px of detail panel for **7 fixed sections + the digest section** (1. task summary, 2. git anchor, 3. runtime/model, 4. supersedes chain, 5. tags, 6. timestamps, 7. context, plus a digest). Compressed, that's ~600-700px of content. The drawer either barely fits (one screenful) or requires significant internal scroll that hides the header — there's no way to "view multiple pieces of info at once" because all but ~3 sections are off-screen at any given height.

**Result:** the detail view is cramped vertically. The 7 sections are visible only by scrolling the drawer itself, which means losing the header (with the title + close + copy-id buttons).

## 4 · Tradeoffs in the existing layout (defensible decisions to preserve)

Not everything is broken. These decisions are good and we keep them:

- The page's information architecture is sound: header → toggle (configuration) → metrics (observability) → filters → list → detail. The redesign re-organizes VISUALLY but preserves the IA.
- The 5-column table (title / project / agent / status / tags / created) carries the right information. We don't add or remove columns.
- The detail drawer's 7-section structure is comprehensive and test-stable. The spec is pinned on it; we don't reorder.
- The `mode="over"` drawer is a reasonable design choice for screens < 1200px wide — but on wider viewports, a side-mode drawer is far more usable. The redesign makes the drawer `mode="side"` (pushes table left) so it has full available height.
- The toggle's radio+Apply+Unsaved-changes pattern is intentional (matches /settings; it makes the dirty-state explicit). We preserve the pattern but compress its footprint.

## 5 · Redesign goals (measurable)

| ID | Goal | Validation |
|---|---|---|
| G-1 | Top-of-page chrome (header + toggle + metrics + filter bar) is reduced to ≤ 200px total (from ~520-590px) without losing any control. | `static: inspect mockup; sum the heights of the four chrome regions above the table-area` |
| G-2 | The table area occupies the full remaining viewport height, with internal vertical scroll, sticky header, and a paginator pinned to the bottom. | `static: table-area is `flex: 1; min-height: 0; overflow: hidden`; mat-table content is `overflow-y: auto`; mat-paginator is below the scroll viewport` |
| G-3 | The detail panel takes the full available height when open. When `mode="side"` is engaged, it pushes the table left; the panel's own internal scroll handles the 7+1 sections without losing the panel header. | `static: drawer is `mode="side"`, height = content-area height; drawer header is `position: sticky; top: 0` inside the drawer` |
| G-4 | All existing controls (toggle, metrics, filter chips, sort, search, paginator, row click) remain accessible — none removed, all reachable within 1-2 clicks. | `static: every present control in v1 has a present control in v2; the e2e test-hook contract (13 `data-test` selectors) is preserved verbatim per the prior spec's amendment` |
| G-5 | Visual hierarchy is sharper: H1 + 1-line status strip at the top; controls on a single row; the table fills the screen. | `static: mockup shows the four regions stacked top-to-bottom in priority order: h1 → control row → stats row → table fills remainder` |

## 6 · What the redesign does NOT touch

- The route (`/snapshots`), the gear-menu entry, the lazy-load boundary.
- The R15 toggle's contract (R/W of `PUT /api/settings/snapshot-create`, dirty-state, spinner, error toast) — just compresses the visual footprint.
- The R16 metrics' contract (read-only display) — demotes them from a 2-card banner to a 1-line "X captures · Y warmed" pill, with the per-agent breakdown collapsed into a popover on click.
- The BE surface (44 + 13 test cases, /api/snapshots/* endpoints) — pure FE.
- The e2e test-hook contract (the 13 `data-test` selectors from fe-plan §5.6) — preserved verbatim.
- The 7-section drawer structure — preserved.

## 7 · The 1-line summary for the leader

> **The chrome is eating 60% of the viewport; the table grows by content so the page-scroll is the only scroll; the drawer is bounded by the table height so the 7-section detail is always cramped. Fix: thread `height: 100%` through the page so the table owns its own scroll viewport, collapse the chrome to ≤ 200px, switch the drawer to `mode="side"` so it takes the full available height, and pin the drawer header inside the drawer.**
