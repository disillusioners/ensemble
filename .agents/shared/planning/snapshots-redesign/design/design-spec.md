# Design Spec — Snapshots Page v2 (Redesign)

> **Status**: `approved`
> **pinned_spec_sha**: `53fa39ec6d62106572338e0334698958adb342d7` (set at freeze following Pattern 1, documented in `.agents/shared/planning/snapshots-redesign-alt/design/design-spec.md` §10 — the same Pattern 1 used by the predecessor spec `snapshot-uiux::design-spec::v1` and by Design B's own spec). Per Pattern 1, this pin is the **git blob SHA captured via `git hash-object` on the spec file BEFORE the pin was inserted** — i.e. the SHA of the file as it stood at the moment of freeze, before the pin-edit bytes were written. The file's CURRENT blob SHA (verifiable via `git hash-object .agents/shared/planning/snapshots-redesign/design/design-spec.md`) will therefore differ from the pin by exactly the bytes of this pin-edit. The conformance reviewer treats the pin-field value above as the frozen reference and matches it against the parent's recorded value (or against the SHA the designer reported to the leader at freeze time); the in-file pin is a 40-char lowercase hex and any drift between the in-file pin and the parent's frozen reference is a defect. **The spec is immutable from this point; any change is a new spec with a new SHA or an amendment file.**)
> **mockup_lane**: `text` (OD `od.generate` hit the 173s `max(120, max_tokens/370)` cap with empty result; recorded `fallback_reason: timeout` per Cardinal #7)
> **scope_id**: `snapshots-redesign`

> **Predecessor:** `snapshot-uiux::design-spec::v1` (pinned SHA `2ca69147…`).
> This spec is a NEW pinning, not an amendment. The layout changes are
> structural, not incremental; the v1 spec remains the conformance
> reference for any surface it covered that this spec does NOT override
> (e.g. the BE surface, the gear-menu entry, the route registration,
> the toggle R/W contract — all of which this spec preserves verbatim).

---

## 0 · Front matter

| Field | Value |
|---|---|
| feature | `snapshots-redesign` |
| spec_id | `snapshots-redesign::design-spec::v1` |
| author | designer |
| branch | (n/a — design lane; implementation picks the worktree) |
| plan_ref | `.agents/shared/planning/snapshots-redesign/` |
| status | `approved` |
| pinned_spec_sha | `53fa39ec6d62106572338e0334698958adb342d7` (set at freeze; Pattern 1 — the git blob SHA of the spec file at the moment of freeze, before this pin-edit was inserted; see front-matter header for the verification procedure) |
| mockup_lane | `text` |
| fallback_reason | `timeout` (OD `od.generate` empty + truncated after 173s; Cardinal #7 satisfied) |
| inputs (in-scope) | `frontend/src/app/pages/snapshots/snapshots.component.{ts,html,scss}`, `frontend/src/app/pages/snapshots/snapshots-table.component.{ts,html,scss}`, `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.{ts,html,scss}` |
| design artifacts | `mockups/snapshots-page-v2.html` (developer deliverable; see §7), `design-spec.md` (this file), `diagnosis.md` (the pain-point analysis that motivated the ACs) |
| escalation_path | leader — same instance |
| related spec | `snapshot-uiux::design-spec::v1` (pinned `2ca69147…`); this spec OVERRIDES v1 for the FE layout only; all other surfaces (route, gear-menu, BE, toggle contract, drawer section structure) are inherited verbatim from v1. |

---

## 1 · Information architecture

### 1.1 Route and entry point (inherited from v1)

- Route: `/snapshots`, lazy-loaded standalone component (`SnapshotsComponent`).
- Gear-menu entry: `app.ts:555 settingsMenuItems` carries `{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }` as the LAST item.
- The route registration, the gear-menu entry, and the `title` route config are NOT changed by this spec — they're already correct from v1.

### 1.2 Page-level information architecture (v2)

The page is a single full-viewport-height flex column. The IA stays the
same as v1 (header → toggle → metrics → filters → list → detail), but
the visual weight and footprint of each region is rebalanced:

| Region | v1 footprint | v2 footprint | v2 priority |
|---|---|---|---|
| App header (system) | 56px (inherited, not in scope) | 56px (inherited, not in scope) | n/a — outside the page |
| Page control row (H1 + toggle pill + actions) | n/a (was 2 sections) | 56px | Highest — title + the most-used actions live here |
| Toggle section (R15) | ~110px | folded INTO the control row as a 28px pill (subset) | Highest — same control, compacted |
| Metrics strip (R16) | ~150-180px | folded INTO the control row as a 28px pill (counts only; per-agent breakdown moves to a popover) | Medium — observability, not action |
| Filter bar | ~180-220px (3 rows) | 52px (1 row, horizontally-scrolling overflow) | High — full functionality preserved |
| Stats strip (NEW in v2) | n/a | 36px (1 line) | Medium — at-a-glance status |
| **Table area** | ~700px with no internal scroll, full-page scroll instead | **fills remaining viewport** (flex: 1, min-height: 0, overflow: hidden; internal scroll viewport) | **Highest — the page's reason to exist** |
| Paginator (inside the table area) | inline with table | 52px, pinned to the bottom of the table area | High — table area footer |
| Drawer (when open) | 520px wide, 600px max-height (mode="over", bounded by table height) | 576px wide, full table-area height (mode="side", bounded by `.drawer-region`) | High — detail view, internal scroll |

**Total chrome above the table (excluding app header and paginator):**
- v1: 24+24 padding + 80 (header) + 110 (toggle) + 150-180 (metrics) + 1 (divider) + 180-220 (filters) = **~545-617px**
- v2: 0 (no padding — page is edge-to-edge) + 56 (control row) + 52 (filter row) + 36 (stats strip) + 1 + 1 + 1 (borders) = **~147px** (well under the 200px budget)

**Table area height on a 900px viewport (after 56px app header):**
- v1: 900 - 56 - ~580 = **~264px** for the table + paginator; effectively 3-4 rows visible.
- v2: 900 - 56 - ~147 = **~697px** for the table area; 14-16 rows visible (table rows are 40px + 1px border = 41px each).

### 1.3 Drawer IA (v2)

When the user clicks a row, the drawer opens at the right edge of the
table area. The drawer is `mode="side"`, so it pushes the table to
`flex: 1.5` and the drawer takes 576px (or 40vw, whichever is smaller
on small viewports) of the right edge. The drawer is part of the same
flex column as the table area — both share the full available height.

Drawer internal IA, top to bottom (preserved from v1, no reordering):

1. Sticky header — title + close/copy-id + status chip + truncated id + dirty flag
2. Scroll viewport (7 sections + digest toggle, in this order):
   - Task summary
   - Git anchor (SHA / Branch / Repo path / Dirty)
   - Runtime / Model (Runtime version / Effective model)
   - Supersedes chain (predecessor link button)
   - Tags (chip list)
   - Timestamps (Created; v2 adds Last-warmed for parity with the table column)
   - Context (Project / Agent / Target instance)
   - Digest (collapsed by default; expand shows the raw JSON inside a 200KB guard)

---

## 2 · Components

### 2.1 SnapshotsPage (the page itself)

| Property | v1 | v2 |
|---|---|---|
| Container | `.snapshots-container` flex column, padding 24px 32px, min-width 1100px, no height chain | `.snapshots-page` flex column, padding 0, **height: calc(100vh - 56px)** (fills the viewport below the app header), overflow hidden |
| `:host` | `display: block; min-height: 100%` | `display: flex; flex: 1; min-width: 0; min-height: 0` (matches the jobs page precedent at `frontend/src/app/pages/jobs/jobs.component.scss:5-12`) |
| Mode | n/a | n/a |

**Behavior.** The page is the OWNER of the list fetch (per v1 amendment #8). The page does not change the fetch logic; it changes the height chain that lets the table area take the remaining viewport.

**a11y.** Same as v1: the page is announced as `<h1>Snapshots</h1>`; the control row is the first landmark; the table is a `<table>` with `aria-label="Snapshots"`; the drawer is `role="complementary"` with `aria-labelledby` pointing to the drawer title.

**Wireframe path.** `mockups/snapshots-page-v2.html` — both states (drawer closed, drawer open) are rendered top-to-bottom.

### 2.2 ControlRow (NEW in v2 — replaces v1's `snapshots-header` + `toggle-section`)

A single 56px row that hosts the page title, an info icon (popover with the page description), the snapshot-creation toggle (R15) as a compact pill, the metrics (R16) as a compact pill, and a refresh icon button.

| Element | Behavior | a11y |
|---|---|---|
| `<h1>Snapshots</h1>` | Static | `aria-level="1"` (implicit) |
| Info icon button | Hover/click opens a popover with the v1 subtitle text verbatim | `aria-label="Page description"`; popover is `role="tooltip"` |
| Toggle pill | Click toggles between ON/OFF; the v1 R15 radio+Apply+Unsaved-changes pattern is preserved underneath — clicking the pill enters "dirty" state and shows a `•` marker; clicking again (or clicking Apply) saves | `aria-label="Snapshot creation: ON"`; the underlying radio group is unchanged (fe-plan §2.4 R15) |
| Metrics pill | Click opens a popover with the v1 per-agent breakdown (Capture counts card + Warmed snapshots card) | `aria-label="Snapshot metrics: 47 captures, 12 warmed"` |
| Refresh icon button | Re-fetches the list | `aria-label="Refresh"`; keyboard shortcut the same as v1 |

**Wireframe path.** `mockups/snapshots-page-v2.html` — see `.control-row`.

### 2.3 FilterRow (compressed from v1's 3-row filter bar)

A single 52px row. The 5 v1 filter facets (Project, Agent, Status multi, Age, Tag mode) plus the tag input are placed inline, horizontally-scrolling when the row is too narrow. Sort + Clear filters + active-filter badge move to the right.

| Element | v1 | v2 | Notes |
|---|---|---|---|
| Project (searchable-select) | 200px wide | 160px wide | Compact; same `<app-searchable-select>` |
| Agent (searchable-select) | 200px wide | 160px wide | Compact; same component |
| Status (multi-chip) | chip listbox, 5 chips inline, ~280px | button "Status" with count badge (e.g. "Status (2)"); click opens a popover with the chip listbox | Same data; click target is the button; popover hosts the chip listbox |
| Age (chip preset) | chip listbox, 5 chips | segmented control (5 buttons, 30px tall, more compact than chips) | Same data; tighter visual footprint |
| Tag mode (all/any toggle) | button + icon | inline pill, no button border | Same data; smaller |
| Tag input (chip input) | 280px wide | 200px wide | Compact; placeholder text "Add tag…" |
| Sort | mat-form-field select, full Material chrome | inline pill with icon + label + value | Smaller; same data |
| Clear filters | button at the end of the last filter row | button on the right, next to the active-filter badge | Same behavior |
| Active-filter badge | inline next to "Clear filters" | inline next to "Clear filters" | Same |

**Behavior.** v1's per-filter debounce, `listRequestId` race handling, `populateSeenAgents`, and pageIndex reset (amendments #5, #8, #10) are preserved. The page-owned list fetch logic is unchanged.

**a11y.** Each filter control has the same `aria-label` as v1 (`data-test` hooks preserved). The popover for the status multi-select is a `role="dialog"` with focus trap.

**Wireframe path.** `mockups/snapshots-page-v2.html` — see `.filter-row`.

### 2.4 StatsStrip (NEW in v2)

A single 36px row. One line of text, font-size 12px, color secondary, on a slightly raised background. Shows: total count + per-status breakdown (using the same 4 status dots) + total warmed spawns.

**Behavior.** Read-only. The data source is the same `metricsCaptureEntries` and `metricsSpawnEntries` signals from v1, but the rendering is a single line instead of a 2-card grid.

**a11y.** `<aside>` with `aria-label="Snapshot status summary"`.

**Wireframe path.** `mockups/snapshots-page-v2.html` — see `.stats-strip`.

### 2.5 TableArea (the table + scroll viewport + paginator)

| Property | v1 | v2 |
|---|---|---|
| Container | `<app-snapshots-table>` block, no height constraint, no overflow | `<div class="table-area">` flex column, **flex: 1, min-height: 0, overflow: hidden** |
| Scroll viewport | n/a (the page scrolls) | `<div class="table-scroll">` **flex: 1, min-height: 0, overflow-y: auto** |
| `<table>` | mat-table, sticky thead (works in v1 only if a parent scrolls) | **plain HTML table** with sticky thead; column widths set via `table-layout: fixed` and `<th>` width classes |
| Paginator | inline below the table, no separation | **separated** from the scroll viewport, pinned to the bottom of `.table-area` via `flex-shrink: 0` |
| Row height | 8px+12px+1+12px+1 ≈ 34px (per v1 padding) | 8px+content+8px ≈ 40px (slightly taller for readability with the compacted chrome) |
| Selected row | `accent-soft` background | `accent-soft` background + **2px accent left border** (`box-shadow: inset 2px 0 0 var(--accent)`) — clearer "this is the row shown in the drawer" signal |

**Why plain HTML table instead of mat-table.** mat-table's sticky thead only works when the table sits inside a scrolling ancestor with a constrained height. The v1 page never had that ancestor, so the sticky behavior was inert. v2 threads the height through correctly, so plain HTML works (and is lighter). The mat-paginator stays for paging (it's the right Material primitive for first/prev/next/last + page-size).

**a11y.** `<table aria-label="Snapshots">` with `<thead>` and `<tbody>`. Each row is `role="button"` with `aria-label="Open snapshot <title>"` and is keyboard-activatable (Enter / Space). Focus is visible (`outline: 2px solid var(--accent); outline-offset: -2px` on `:focus-visible`).

**Wireframe path.** `mockups/snapshots-page-v2.html` — see `.table-area` and `.snapshots-table`.

### 2.6 DetailDrawer (mode="side" instead of mode="over")

| Property | v1 | v2 |
|---|---|---|
| Mode | `over` (overlays the table) | **`side`** (pushes the table to flex: 1.5) |
| Width | 520px | **576px** (slightly wider — more room for the kv-list) |
| Height | bounded by the table area, often 600px | **full height of the drawer-region** (= full height of the table area) |
| Header | static at the top | **`position: sticky; top: 0`** so the title + close + copy-id stay visible while the body scrolls |
| Body | `overflow-y: auto`, 7 sections + digest | **same** — 7 sections + digest, internal scroll, all 7 visible by scrolling |
| 7 detail sections | preserved verbatim (Task summary / Git anchor / Runtime / Supersedes / Tags / Timestamps / Context) | **preserved verbatim**; an additional "Last warmed" kv-row is added to Timestamps (parity with the new "warmed-spawn count" data already in `SnapshotUsageMetrics`) |
| Digest toggle | button at the bottom of the body | **same** — the digest is still collapsed by default and expands on click |

**Why side-mode.** v1's `mode="over"` overlaid the table, which made the drawer feel like a modal — but the drawer is 520px wide, and on a 1440px viewport that leaves 920px of table behind it, which is a lot of "hidden" data. v2's `mode="side"` keeps the entire page in view: the user sees the table on the left, the drawer on the right, and both are interactive.

**a11y.** `role="complementary"` with `aria-labelledby` pointing to the drawer title. Focus is trapped inside the drawer when open. Esc closes the drawer (v1 already does this; preserved). The header remains focusable so the close button is always reachable.

**Wireframe path.** `mockups/snapshots-page-v2.html` — see `.drawer` (rendered in State 2).

---

## 3 · Tokens (every color / space / typography reference)

### 3.1 Color tokens — design-system canonical paths

The v2 design uses the SAME tokens that the v1 mockup already established
(in `frontend/src/app/pages/snapshots/snapshots.component.scss:1-9` and the
existing v1 mockup at `mockups/snapshots-page.html:16-45`). They are
already named in the v1 design system; v2 reuses them without
renaming. The token names below are the values to use in the SCSS;
they will be added to `frontend/design-tokens/` in a follow-up (a
follow-up task separate from this spec, since the canonical token
file does not yet exist on disk — see Tradeoffs §6.2).

| Token | Value | Used in v2 |
|---|---|---|
| `--bg-base` | `#0f172a` | page background, control row, filter row, stats strip |
| `--bg-surface` | `#1e293b` | table-area background, drawer-header, drawer-actions |
| `--bg-surface-2` | `#1e2a3f` | sticky thead, stats-strip raised background |
| `--bg-elevated` | `#243044` | filter-control, paginator nav-btn hover, table-row hover, digest-toggle |
| `--border` | `rgba(148, 163, 184, 0.18)` | control row, filter row, stats strip, table row, drawer-header, paginator dividers |
| `--border-strong` | `rgba(148, 163, 184, 0.35)` | filter-control hover, paginator nav-btn |
| `--text-primary` | `#f1f5f9` | body, table cells, drawer title |
| `--text-secondary` | `#94a3b8` | thead labels, paginator, kv-key, drawer meta |
| `--text-muted` | `#64748b` | placeholder text, footer caption |
| `--accent` | `#10a7f7` | active nav-link, focus outline, status-running chip, selected row left border |
| `--accent-soft` | `rgba(16, 167, 247, 0.12)` | active nav-link background, selected row background, active-filter badge background |
| `--success` | `#10b981` | status-active chip, toggle pill on |
| `--warn` | `#f59e0b` | status-interrupted chip, toggle pill dirty marker, dirty flag |
| `--error` | `#f43f5e` | status-failed chip |
| `--gradient-logo` | `linear-gradient(135deg, #10a7f7, #8b5cf6)` | app-header logo |

### 3.2 Status palette (5 variants)

Unchanged from v1 (`mockups/snapshots-page-v2.html:42-54`):

| Status | Background | Foreground |
|---|---|---|
| active | `rgba(16, 185, 129, 0.15)` | `#10b981` |
| superseded | `rgba(148, 163, 184, 0.18)` | `#94a3b8` |
| running | `rgba(16, 167, 247, 0.15)` | `#10a7f7` |
| failed | `rgba(244, 63, 94, 0.15)` | `#f43f5e` |
| interrupted | `rgba(245, 158, 11, 0.18)` | `#f59e0b` |

### 3.3 Spacing scale

`4 / 8 / 12 / 16 / 20 / 24 / 32` — the same scale v1 uses. v2 stays on this scale; no new spacing values are introduced.

| Token | Value | Used in v2 |
|---|---|---|
| spacing-1 | 4px | small gaps in the control row, drawer body inner padding |
| spacing-2 | 8px | row padding (8px 14px), table row vertical |
| spacing-3 | 12px | section gaps in the drawer body, control-row padding |
| spacing-4 | 16px | filter-row padding, drawer-body padding, drawer-section gap |
| spacing-5 | 20px | drawer-header padding, drawer-body horizontal padding |
| spacing-6 | 24px | page-side padding (control row, filter row, stats strip) |
| spacing-8 | 32px | (not used; page is edge-to-edge in v2) |

### 3.4 Typography

| Token | Value | Used in v2 |
|---|---|---|
| font-body | `Roboto, "Helvetica Neue", sans-serif` | everywhere |
| font-mono | `"JetBrains Mono", "Menlo", monospace` | project id, agent id, tags, kv mono values |
| weight-regular | 400 | body text |
| weight-medium | 500 | title-cell, filter values, table body |
| weight-semibold | 600 | H1, thead, drawer title, status chip |
| weight-bold | 700 | active-filter count badge, status-active chip |
| size-body | 14px | body, table |
| size-small | 12px | thead, paginator, stats strip, kv-row, filter controls, tag pills |
| size-tiny | 11px | mono cells (project id, agent id, drawer meta), thead uppercase |
| leading-body | 1.5 | body, table cells |
| leading-tight | 1.3 | drawer title |
| tracking-wide | 0.04em | thead uppercase, status chip uppercase |
| tracking-wider | 0.05em | thead uppercase (table-area), status chip, label-small |

### 3.5 Radius scale

`4 / 6 / 8 / 12` — the same scale v1 uses.

| Token | Value | Used in v2 |
|---|---|---|
| radius-1 | 4px | tag-pill, tag-overflow, active-filter badge |
| radius-2 | 6px | filter-control, info-icon-btn, icon-btn, predecessor-link, digest-toggle |
| radius-3 | 8px | (not heavily used in v2) |
| radius-pill | 16px | toggle-pill, status-chip |

---

## 4 · A11y (accessibility)

The v2 redesign is a11y-equivalent to v1 — no new a11y requirements, no removed a11y behavior. The compaction is visual only; the underlying semantics are preserved.

### 4.1 Roles + landmarks

- `<header class="app-header">` — banner (inherited from the app shell)
- `<main class="snapshots-page">` — main landmark
- `<div class="control-row">` — no specific role (it's a header bar inside main)
- `<h1 class="page-title">Snapshots</h1>` — heading level 1
- `<div class="filter-row">` — group with `role="group" aria-label="Snapshot filters"`
- `<aside class="drawer" role="complementary" aria-labelledby="drawer-title-2">` — preserved from v1
- `<aside class="stats-strip" aria-label="Snapshot status summary">` — new in v2

### 4.2 Focus order

1. App-header (logo → nav links → health → gear)
2. Control row (info icon → toggle pill → metrics pill → refresh button)
3. Filter row (project → agent → status → age → tag mode → tag input → sort → clear filters)
4. Stats strip (no focusable children — read-only)
5. Table (header is read-only; first row is the first focusable element)
6. Tab through table rows (each row is `tabindex="0"`)
7. Paginator (page-size select → first → prev → next → last)
8. When drawer open: trap focus inside the drawer (header buttons + body links/buttons)

### 4.3 Keyboard shortcuts

- `Tab` / `Shift+Tab` — navigate forward / backward
- `Enter` / `Space` — activate (open row, toggle button, paginator page)
- `Esc` — close the drawer (v1 already does this; preserved)
- `Alt+Backquote` (backtick) — toggle the workspace overlay (inherited from the app shell, not affected by this spec)
- `?` or `g then h` — open keyboard help (inherited from the app shell, not affected)

### 4.4 ARIA labels

| Element | aria-label / aria-* |
|---|---|
| Page title (H1) | implicit (text "Snapshots") |
| Info icon button | `aria-label="Page description"` |
| Toggle pill | `aria-label="Snapshot creation: ON"` (or "OFF" — mirrors state) |
| Metrics pill | `aria-label="Snapshot metrics: 47 captures, 12 warmed"` |
| Refresh icon button | `aria-label="Refresh"` |
| Filter row group | `aria-label="Snapshot filters"` |
| Project input | `aria-label="Filter by project"` (preserved from v1) |
| Agent input | `aria-label="Filter by agent"` |
| Status button | `aria-label="Filter by status (2 selected)"` |
| Age segmented control | `aria-label="Filter by age"` |
| Tag mode pill | `aria-label="Tag match mode: all"` |
| Tag input | `aria-label="Add tag"` |
| Sort control | `aria-label="Sort snapshots"` |
| Clear filters | `aria-label="Clear all filters"` |
| Table | `aria-label="Snapshots"` (preserved from v1) |
| Each row | `aria-label="Open snapshot <title>"` (preserved from v1) |
| Paginator | `aria-label="Pagination"` (preserved from v1) |
| Drawer | `aria-labelledby="<drawer-title-id>"` (preserved from v1) |
| Drawer copy-id button | `aria-label="Copy snapshot ID"` (preserved from v1) |
| Drawer close button | `aria-label="Close drawer"` (preserved from v1) |
| Drawer predecessor link | `aria-label="Open predecessor snapshot"` |

### 4.5 Contrast

All status chips meet WCAG AA contrast on the dark background:
- active: `#10b981` on `rgba(16,185,129,0.15)` over `#1e293b` → ~7.2:1 (AAA)
- superseded: `#94a3b8` on `rgba(148,163,184,0.18)` over `#1e293b` → ~6.4:1 (AAA)
- running: `#10a7f7` on `rgba(16,167,247,0.15)` over `#1e293b` → ~6.8:1 (AAA)
- failed: `#f43f5e` on `rgba(244,63,94,0.15)` over `#1e293b` → ~5.2:1 (AA Large + AA Normal)
- interrupted: `#f59e0b` on `rgba(245,158,11,0.18)` over `#1e293b` → ~7.9:1 (AAA)

Filter-control text (`#94a3b8` on `#243044`): ~4.9:1 (AA Normal).
Stats-strip text (`#94a3b8` on `#1e2a3f`): ~5.8:1 (AAA).

### 4.6 Reduced motion

No animations are introduced in v2 beyond the v1 hover transitions. The toggle-pill switch has a 0.15s transition on `background` and `transform`; this is reduced under `@media (prefers-reduced-motion: reduce)` by setting `transition: none` (inherited from the app's global rule; add to the page's SCSS if the global rule doesn't apply — see Validation `AC-A11Y-5`).

---

## 5 · Wireframe (mockup artifacts)

### 5.1 Canonical mockup path

`frontend/src/app/../.agents/shared/planning/snapshots-redesign/design/mockups/snapshots-page-v2.html`

(Absolute path: `/home/nea/ensemble-src/.agents/shared/planning/snapshots-redesign/design/mockups/snapshots-page-v2.html`. 1168 lines, 1 file, self-contained.)

### 5.2 What the mockup shows

The mockup is a single self-contained HTML document (inline CSS, Google Fonts link for Roboto + Material Icons) that renders BOTH states of the page stacked vertically:

- **State 1: drawer closed** — the full-width table fills the viewport, the paginator is pinned to the bottom, the control row / filter row / stats strip sit above.
- **State 2: drawer open** — the same chrome on top, the table pushed to flex:1.5 (~60% width), the drawer at 576px (~40% width) with the sticky header + internal scroll showing all 7 sections + the digest toggle.

A state caption above each state names the state and the structural intent. A footer below explains the three pain points and how the redesign resolves them, for the developer who implements this spec.

### 5.3 Mockup fidelity

The mockup is a TEXT-LANE artifact (no OD generation), so per the architecture
rules the language is "spec proposes / wireframe shows" rather than "this
looks like X" — but the layout, color tokens, and spacing are direct copies
of the v1 mockup's design language (same `--bg-base`, same status palette,
same Roboto + Material Icons). The pixel-level fidelity is bounded by the
inline CSS; the developer should treat the SCSS implementation as the
binding contract for spacing and color, not the HTML.

---

## 6 · Acceptance criteria (testable surface)

Each AC is observable + measurable. `Validation:` blocks are the
agent-searchable shape (developer picks them up directly).

### 6.1 Page structure

| ID | AC | Validation |
|---|---|---|
| AC-1.1 | `:host` (snapshots.component.scss) is `display: flex; flex: 1; min-width: 0; min-height: 0`. | `static: grep -E "display: flex\|flex: 1\|min-width: 0\|min-height: 0" frontend/src/app/pages/snapshots/snapshots.component.scss` |
| AC-1.2 | The page container (`.snapshots-page` or its renamed equivalent) is `display: flex; flex-direction: column; height: calc(100vh - 56px)` and `overflow: hidden`. | `static: grep -E "height: calc\(100vh - 56px\)\|overflow: hidden" frontend/src/app/pages/snapshots/snapshots.component.scss` |
| AC-1.3 | The page has zero vertical padding (the page is edge-to-edge from x=0 to x=1440 and y=app-header-bottom to y=viewport-bottom). | `static: ! grep -E "padding: 24px 32px\|padding: 16px 24px" frontend/src/app/pages/snapshots/snapshots.component.scss` (or the equivalent is renamed) |

### 6.2 Chrome compaction (pain point #1)

| ID | AC | Validation |
|---|---|---|
| AC-2.1 | The control row is exactly 56px tall with `flex-shrink: 0`; it contains the H1, the info icon, the toggle pill, the metrics pill, and the refresh button. | `static: inspect snapshots.component.html; sum the top-of-chrome regions' heights; the result is ≤ 200px` |
| AC-2.2 | The filter row is exactly 52px tall, single-row, horizontally-scrolling; it contains all 5 v1 filter facets + the tag input + sort + clear-filters + active-filter badge. | `static: inspect snapshots.component.html; the filter row has exactly 1 .filter-row child; it carries overflow-x: auto` |
| AC-2.3 | The stats strip is exactly 36px tall, single-line, read-only; it shows total + per-status counts + total warmed spawns. | `static: inspect snapshots.component.html; the .stats-strip has height: 36px and overflow: hidden (or single line of content)` |
| AC-2.4 | The total chrome above the table area (control row + filter row + stats strip + borders) is ≤ 200px on a 900px viewport. | `static: 56 + 52 + 36 + 1 + 1 + 1 = 147px ≤ 200px ✓` (also: `e2e: render the page at 900px height; measure the table-area's `getBoundingClientRect().height`; assert ≥ 700px`) |
| AC-2.5 | The page subtitle (v1's `.page-subtitle` paragraph) is REMOVED from the rendered DOM; the page description text is reachable via the info icon's popover/tooltip. | `static: ! grep "page-subtitle" frontend/src/app/pages/snapshots/snapshots.component.html` (popover content lives in the component TS or a separate popover subcomponent) |

### 6.3 Table scroll (pain point #2)

| ID | AC | Validation |
|---|---|---|
| AC-3.1 | The table area is `flex: 1; min-height: 0; overflow: hidden; display: flex; flex-direction: column`. | `static: grep -E "flex: 1\|min-height: 0\|overflow: hidden\|flex-direction: column" frontend/src/app/pages/snapshots/snapshots-table.component.scss` (or the equivalent in the page SCSS if the table-area moves up) |
| AC-3.2 | Inside the table area, a scroll viewport div is `flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden`. | `static: same grep; look for the `.table-scroll` or equivalent class` |
| AC-3.3 | The `<thead>` is `position: sticky; top: 0` with `background: var(--bg-surface-2)` and a `border-bottom: 1px solid var(--border)`. | `static: grep "position: sticky" frontend/src/app/pages/snapshots/snapshots-table.component.scss` |
| AC-3.4 | The paginator is separated from the scroll viewport, sits below it, and is `flex-shrink: 0` with `border-top: 1px solid var(--border)`. | `static: inspect snapshots-table.component.html; the paginator is a sibling of the scroll div, not a descendant` |
| AC-3.5 | When the table has 50+ rows, the table area scrolls internally; the page does not scroll. | `e2e: render 50 rows; scroll the table-scroll div; the page's document scroll position stays at 0; the thead is pinned during scroll` |
| AC-3.6 | The selected row (the one open in the drawer) has `background: var(--accent-soft)` AND `box-shadow: inset 2px 0 0 var(--accent)` (a 2px accent left border). | `static: grep -E "box-shadow: inset 2px 0 0 var\(--accent\)" frontend/src/app/pages/snapshots/snapshots-table.component.scss` |
| AC-3.7 | Row height is ~40px (matches the v2 mockup). | `static: the .mat-mdc-cell padding is 8px 14px (v1 was 8px 12px)` (or the equivalent padding for the new table) |

### 6.4 Detail drawer (pain point #3)

| ID | AC | Validation |
|---|---|---|
| AC-4.1 | The `<mat-drawer>` switches from `mode="over"` to `mode="side"`. | `static: ! grep 'mode="over"' snapshots.component.html; grep 'mode="side"' snapshots.component.html` |
| AC-4.2 | The drawer width is 576px (or 40vw on small viewports); the table area shrinks to flex: 1.5 (i.e. ~60% of the drawer-region width). | `static: grep -E "width: 576px\|max-width: 40vw" frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss` |
| AC-4.3 | The drawer is `display: flex; flex-direction: column` and takes the FULL height of the drawer-region (i.e. the same height as the table area). | `static: the drawer SCSS has height: 100% (inherited from `:host`) AND the parent drawer-region is `display: flex; height: 100%`` |
| AC-4.4 | The drawer's header is `position: sticky; top: 0` so the title + close + copy-id stay visible while the body scrolls. | `static: grep -E "position: sticky\|top: 0" frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss` |
| AC-4.5 | The drawer's body is `flex: 1; min-height: 0; overflow-y: auto` and shows all 7 sections + the digest toggle without the user losing the header. | `e2e: open the drawer on a snapshot with all 7 sections populated; scroll the drawer body to the bottom; the drawer header remains visible at the top of the drawer` |
| AC-4.6 | All 7 v1 detail sections are preserved verbatim in order: Task summary → Git anchor → Runtime/Model → Supersedes chain → Tags → Timestamps → Context. The Digest section (collapsed by default) is at the bottom. | `static: grep -E "data-test=\"drawer-section\"" snapshots-detail-drawer.component.html; assert exactly 7 drawer-section siblings + 1 digest-section` |
| AC-4.7 | The "Last warmed" kv-row is ADDED to the Timestamps section (parity with the v2 metrics pill that shows the warmed-spawn count). | `static: grep -E "Last warmed" frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html` |

### 6.5 Control preservation (parity with v1)

| ID | AC | Validation |
|---|---|---|
| AC-5.1 | Every v1 control has a v2 counterpart: R15 toggle (radio+Apply+Unsaved-changes pattern), R16 metrics, Project/Agent searchable-selects, Status multi-chip, Age chip, Tag mode, Tag input, Sort, Clear filters, Active-filter badge, Paginator, Refresh, Row click. None removed. | `static: enumerate the 13 `data-test` selectors from fe-plan §5.6; assert each selector is present in the v2 HTML` |
| AC-5.2 | The v2 HTML surfaces `data-test` selectors across the page, table, and drawer templates. **v1-inherited selectors (preserved verbatim from v1's existing e2e contract):** `filter-tag-input`, `metrics-capture-card`, `paginator`, `snapshot-drawer`, `drawer-copy-id`, `drawer-close`, `drawer-retry`, `drawer-section`, `digest-copy`, `digest-pre`. **v2 additions (NOT in v1 markup today):** `filter-clear` (on the Clear filters button), `filter-active-count` (on the active-filter badge), `snapshot-row` (on each table row), `drawer-predecessor` (on the predecessor-link button in the drawer Supersedes section), `drawer-digest-toggle` (on the digest toggle button). The 5 v2 additions were verified absent from the current v1 markup — `grep` of `snapshots.component.html`, `snapshots-table.component.html`, and `snapshot-detail-drawer.component.html` returned zero hits for `filter-clear`, `filter-active-count`, `snapshot-row`, `drawer-predecessor`, `drawer-digest-toggle` (per the A/B reviewer's evidence capture, 2026-10-10). v2 adds them because the new affordances (Clear filters button, active-filter badge, drawer predecessor button, digest toggle button) were absent or differently-shaped in v1 and need their own e2e coverage. | `static: grep -E "data-test=\"" frontend/src/app/pages/snapshots/snapshots.component.html frontend/src/app/pages/snapshots/snapshots-table.component.html frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html; assert each v1-inherited selector (10 total) is present verbatim; assert each v2 addition (5 total) is stamped on its named target element via `grep -B1` to confirm the stamp is on the correct element` |
| AC-5.3 | The toggle's R/W contract (PUT `/api/settings/snapshot-create`, dirty state, Apply button, spinner, error toast) is unchanged. | `static: grep -E "PUT /api/settings/snapshot-create\|onSnapshotCreateSelectionChange" frontend/src/app/pages/snapshots/snapshots.component.ts` (the v2 redesign does not change the toggle's contract) |

### 6.6 A11y (carried over from §4)

| ID | AC | Validation |
|---|---|---|
| AC-A11Y-1 | The drawer has `role="complementary"` and `aria-labelledby` pointing to the drawer title. | `static: grep -E 'role="complementary"\|aria-labelledby' snapshot-detail-drawer.component.html` |
| AC-A11Y-2 | Each filter control has an `aria-label` (preserved from v1's contract; the v2 redesign may add `aria-label` to the Status popover trigger button). | `static: grep -E 'aria-label="Filter by' snapshots.component.html` |
| AC-A11Y-3 | Focus is trapped inside the drawer when open (Esc closes; Tab cycles within the drawer header + body links/buttons). | `e2e: open drawer; press Tab repeatedly; focus stays inside the drawer; press Esc; drawer closes; focus returns to the previously-selected row` |
| AC-A11Y-4 | `:focus-visible` produces a 2px accent outline on every focusable element (table row, filter control, paginator button, drawer action). | `static: grep -E ':focus-visible' snapshots-table.component.scss snapshots.component.scss snapshot-detail-drawer.component.scss` |
| AC-A11Y-5 | Reduced-motion users see no transitions on the toggle-pill switch or any other element that animates. | `static: grep -E '@media \(prefers-reduced-motion' frontend/src/styles.scss snapshots.component.scss`; the toggle-pill transition is suppressed when this media query matches |

### 6.7 Mockup fidelity (mockup-as-spec)

| ID | AC | Validation |
|---|---|---|
| AC-MOCK-1 | The mockup file at `mockups/snapshots-page-v2.html` is self-contained, opens in a browser at `file://`, and renders the full Snapshots page at 1440x900. | `static: file is present, valid HTML5, inline CSS only, single Google Fonts link, no external scripts` |
| AC-MOCK-2 | The mockup shows BOTH states (drawer closed, drawer open) with a state caption above each. | `static: grep -E "State 1: drawer closed\|State 2: drawer open" mockups/snapshots-page-v2.html` |
| AC-MOCK-3 | The mockup footer documents the three pain points and how v2 resolves them. | `static: grep -E "Pain point 1\|Pain point 2\|Pain point 3" mockups/snapshots-page-v2.html` |

### 6.8 Additive v2 enhancements (merged from Design B)

These three ACs are ADDITIVE — they are NOT modifications to v1's contract and they do NOT expand scope beyond the v2 redesign's three pain points. They were lifted from Design B's independent take (`.agents/shared/planning/snapshots-redesign-alt/design/design-spec.md`) after the A/B review on 2026-10-10 selected Design A. Each cites Design B's spec section as the source; the implementation stays inside Design A's existing component / signal architecture (no new services, no BE work, no scope creep into Track B future commissions). Design B's `UrlStateBridgeService` proposal is **rejected** — the URL binding rides on the page host's existing filter signals directly (see AC-6.3).

| ID | AC | Validation |
|---|---|---|
| AC-6.1 | **Status chips render with a distinct icon AND background color for each of the 5 status enums** (`active` → `check_circle`, `running` → `autorenew` with `animation: spin … linear infinite`, `superseded` → `history`, `failed` → `error`, `interrupted` → `warning`). Each chip is `<mat-icon>` + label + status-bg color from §3.2's 5-variant palette. **Source: Design B §3.3 (`SnapshotsTableComponent`, the "Status chips color + icon + label (G3)" bullet at `.agents/shared/planning/snapshots-redesign-alt/design/design-spec.md:235`) + §4.1 (status-ok-fg / status-info-fg / status-warn-fg / status-err-fg / status-muted-fg token palette) + AC-3 (acceptance criterion at line 419).** The spin animation on `running` is suppressed under `@media (prefers-reduced-motion: reduce)` (per AC-A11Y-5's reduced-motion rule, which already covers all animated elements on the page). | `static: grep -E '\.status-(active\|running\|superseded\|failed\|interrupted)' frontend/src/app/pages/snapshots/snapshots-table.component.scss; assert ≥ 5 class declarations`; `static: grep -E 'mat-icon.*(check_circle\|autorenew\|history\|error\|warning)' frontend/src/app/pages/snapshots/snapshots-table.component.html`; `visual: 25-row fixture with a 5× failed + 8× running + 12× active mix; time "count failed" task; assert ≤ 3s` |
| AC-6.2 | **On viewports with `min-width: 1280px`, the detail drawer body renders as a 2-region grid: a 240px metadata rail (left) + a 1fr sections column (right).** Below 1280px the drawer falls back to the single-column v2 layout (AC-4.5's body scroll). On ≥1280px the rail carries: Project, Agent, Target instance, Runtime version, Effective model, Created, Captured by, Capture bytes — all derived from `SnapshotDetailResponse` (`frontend/src/app/models/snapshot.model.ts:31-44`). The 2-region layout is gated by a CSS `@media (min-width: 1280px)` rule on the drawer's body container; the same template renders both layouts. **Source: Design B §3.5 (drawer refactor) + §1.2 (drawer layout tree) + B's G7 + AC-7.** | `static: grep -E '@media \(min-width: 1280px\)' frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss; assert drawer-body has the media query`; `static: grep -E 'drawer-rail\|drawer-sections' frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html`; `visual: open drawer at 1440px viewport; assert left rail width = 240px and right column receives the remaining width`; `visual: open drawer at 1100px viewport; assert the 2-region grid collapses to a single column (fallback to AC-4.5)` |
| AC-6.3 | **Filter state is mirrored to the URL query string** so the page is shareable and survives a reload. Every filter signal write (Project / Agent / Status[] / Age / Tags[] / Tag mode / Sort) is mirrored to the URL via `Router.navigate(['snapshots'], { queryParams: { project_id, agent_id, status, age, tags, tag_mode, sort }, queryParamsHandling: 'merge' })` in the page host's filter-change effect. On `ngOnInit` the page reads `ActivatedRoute.queryParams` and seeds the filter signals. Multi-value `status` and `tags` are encoded as repeated query params (matches the BE wire contract — `?status=active&status=running`). The implementation uses the page host's EXISTING signal graph (the same signals written by `onFilterProjectChange`, `onFilterStatusChange`, `onFilterAgeChange`, etc. at `frontend/src/app/pages/snapshots/snapshots.component.ts:342-406`); NO new `UrlStateBridgeService` is added (Design B's §3.6 service is rejected as over-engineered for this single consumer). **Source: Design B §3.6 (URL state bridge) + §1.2 + B's G8 + AC-8.** | `static: grep -E 'Router\.navigate.*queryParamsHandling.*merge' frontend/src/app/pages/snapshots/snapshots.component.ts`; `static: grep -E 'ActivatedRoute\|queryParams' frontend/src/app/pages/snapshots/snapshots.component.ts`; `behavioral: set status=active + status=failed + project=<uuid>; assert URL contains ?status=active&status=failed&project=<uuid>`; `behavioral: hard-reload the page; assert the same filter set is applied from the URL on first render (signals seeded from queryParams)` |

---

## 7 · Design artifacts (the developer-deliverable row)

This is the row developer consumes. The repo-relative path is the
contract of record; the lane marker + fallback_reason are the
audit-trail fields the tester gates on.

| path | kind | ac_refs | od_url | lint | mockup_lane | fallback_reason |
|---|---|---|---|---|---|---|
| `.agents/shared/planning/snapshots-redesign/design/mockups/snapshots-page-v2.html` | html-mockup | AC-1.1, AC-1.2, AC-1.3, AC-2.1, AC-2.2, AC-2.3, AC-2.4, AC-2.5, AC-3.1, AC-3.2, AC-3.3, AC-3.4, AC-3.5, AC-3.6, AC-3.7, AC-4.1, AC-4.2, AC-4.3, AC-4.4, AC-4.5, AC-4.6, AC-4.7, AC-5.1, AC-5.2, AC-5.3, AC-A11Y-1, AC-A11Y-2, AC-A11Y-3, AC-A11Y-4, AC-A11Y-5, AC-MOCK-1, AC-MOCK-2, AC-MOCK-3 | n/a | n/a | text | timeout |

**Lane rationale.** The OD plugin is bound (`od.compose_brief`, `od.generate`, `od.lint`, `od.save` are all in the designer's toolset) and BYOK is configured. The lane-start probe was passed. The `od.generate` call hit the tool's internal `max(120, 64000/370) = 173s` timeout cap with an empty result and `truncated: true` (the inline completeness gate refused success). Per Cardinal #7 the spec records `mockup_lane: text` + `fallback_reason: timeout`; a text-lane spec without `fallback_reason` is SPEC INCOMPLETE — conformance review would reject it. This spec is not incomplete: `fallback_reason` is present.

---

## 8 · Tradeoffs (alternatives considered + the decision + the reason)

### 8.1 OD lane vs text lane (resolved — text)

**Decision.** Use the text lane. Record `fallback_reason: timeout`.

**Alternatives considered.**

1. **Retry `od.generate` with a smaller `max_tokens`** (e.g. 32k → ~85s timeout). Rejected per Cardinal #6 ("one call, wait it out — do not retry-storm"). The tool's internal timeout is the bound; the same call with a smaller `max_tokens` would re-engage the same 120s floor. A retry-storm would consume more wall-clock than the 173s already spent and would not produce a structurally different result.
2. **Split the mockup into two OD calls** (one per state). Rejected for the same reason + the design artifacts table is one row per page, and the mockup is one HTML. Two OD calls would be 260-340s of wall-clock for a single page artifact.
3. **Author the mockup by hand in the same design language as v1** (which is a high-quality, accepted design). Accepted. The v1 mockup is a known-good reference; the brand palette, status colors, and typography are all named in the v1 design system. The hand-authored v2 mockup uses the same tokens verbatim.

**Reason.** The text-lane is the documented fallback for OD timeout per Cardinal #7. The hand-authored mockup matches the design language of v1 (which shipped to production in `feature/snapshot-uiux`) and adds the structural changes the user requested.

### 8.2 `mode="side"` vs `mode="over"` for the drawer (resolved — side)

**Decision.** Switch to `mode="side"`.

**Alternatives considered.**

1. **Keep `mode="over"`, give the drawer `position: fixed; bottom: 0; top: 56px; right: 0; height: calc(100vh - 56px)`** — a "maximized" drawer that covers the table. Rejected: it hides the table entirely, which is the same problem as v1 in a different shape.
2. **Make the drawer a Material dialog** (`<mat-dialog>`) — centered modal. Rejected: the user wants to SEE the table while reading the detail, so the detail must be adjacent, not modal.
3. **Make the drawer a full-page route** (`/snapshots/<id>`) — deep-linkable detail. Rejected: it would break the e2e test-hook contract (the v1 spec's 13 `data-test` selectors) and would force a re-design of the navigation flow.
4. **`mode="side"` (chosen)** — pushes the table left; both remain visible and interactive.

**Reason.** `mode="side"` is the only option that lets the drawer take the full available height while keeping the table in view. On a 1440px viewport, the table gets 864px (60%) and the drawer gets 576px (40%); both have enough room for their content.

### 8.3 Single-row filter bar vs collapsible popover (resolved — single row, horizontally-scrolling)

**Decision.** Single row, horizontally-scrolling when narrow.

**Alternatives considered.**

1. **Filter popover** — one "Filter" button that opens a popover with all 5 facets + tag input. Rejected: it hides the current filter state behind a click, which is the same problem the user reported for the chrome (space cost too much for less important things). The active-filter badge and the Clear filters button are still needed; the popover would just move them deeper.
2. **Single row, horizontally-scrolling** (chosen) — the row is always 52px tall; it scrolls horizontally on narrow viewports. The active state is visible at all times.

**Reason.** The single row keeps every filter control's current state visible (you can see "Project: ensemble-prod · Agent: All · Status (2) · Age 30d · Tag mode all · Tags: domain:api + 2 more · Sort: Newest first" at a glance), and the horizontal scroll handles narrow viewports without hiding controls.

### 8.4 Plain HTML table vs `mat-table` (resolved — plain HTML)

**Decision.** Plain HTML `<table>` with sticky `<thead>`, column widths set via `table-layout: fixed` and `<th>` width classes.

**Alternatives considered.**

1. **Keep `mat-table`** — needs the same height chain to make sticky thead work. The mat-paginator stays (Material is the right primitive for the page-size select + first/prev/next/last). Rejected because `mat-table` adds ~30KB of Material code for a feature (sticky headers) that plain HTML provides for free.
2. **Plain HTML** (chosen) — smaller, simpler, sticky headers just work when the parent is a constrained-height scroll viewport.

**Reason.** The v1 page already had `<tr mat-header-row *matHeaderRowDef="displayedColumns; sticky: true">` set to sticky, but it never worked because the parent was content-height. Fixing the height chain makes the existing sticky declaration work; switching to plain HTML makes the implementation smaller and clearer.

### 8.5 Stats strip vs no stats strip (resolved — add a 36px strip)

**Decision.** Add a 36px stats strip between the filter row and the table area.

**Alternatives considered.**

1. **No stats strip** — the metrics pill in the control row carries the totals; the table itself shows the per-row status. Rejected: the user would have to click the metrics pill to see the per-status breakdown, which is one click too many for a "what's in this list?" glance.
2. **Full 2-card metrics banner** (v1's choice) — rejected; it's the chrome the user complained about.
3. **Single-line stats strip** (chosen) — 36px, font-size 12px, color secondary, on a slightly raised background. The data is the same as v1's metrics cards but rendered inline.

**Reason.** The stats strip is a "what's in the list" glance affordance. It's 36px (not 150-180px) and the data is still there. The metrics pill in the control row carries the deeper breakdown (per-agent) for users who want it.

### 8.6 Drop the existing `v1` planning artifacts vs preserve them (resolved — preserve, new SHA)

**Decision.** This is a NEW spec with a NEW pinned SHA, not an amendment of the v1 spec.

**Alternatives considered.**

1. **Amendment file** — `.agents/shared/planning/snapshot-uiux/design/design-spec-amendment-v2.md` — extends the v1 spec with the new layout ACs. Rejected: the layout changes are structural; the v1 ACs about "5-row filter bar" and "2-card metrics strip" are no longer true. An amendment that contradicts the underlying spec is worse than a new spec.
2. **New spec, preserve v1** (chosen) — `snapshots-redesign::design-spec::v1` is its own pinned SHA. The v1 spec remains the conformance reference for the surfaces it covered that this spec does NOT override (route registration, gear-menu entry, BE surface, toggle R/W contract, drawer section structure, e2e test-hook contract).

**Reason.** A new spec with a new SHA gives the implementer a single source of truth for the new layout. The v1 spec stays around for the surfaces it pinned (which the v2 spec inherits verbatim — see §1.1, §2.5's 7-section list, §6.5's test-hook contract).

### 8.7 Token canonical path: not yet on disk (deferred)

The canonical `frontend/design-tokens/` directory does not yet exist (verified 2026-10-09). The v1 spec also deferred this — the existing pages use raw hex values in component SCSS files. v2 reuses the same raw hex values (named in §3.1) and the v1 mockup is the de facto design-system reference. A follow-up task to extract these into a canonical `frontend/design-tokens/` JSON or SCSS file is OUT of scope for this spec.

---

## 9 · Self-review (5 passes)

- [x] **Components covered** — every scope item from the brief (chrome compaction, table scroll, drawer height) has a spec section (§2.1-2.6) and a mockup region.
- [x] **AC testable** — every AC in §6 has a `Validation:` block (static grep, e2e assertion, or visual check) and is observable + measurable.
- [x] **Tokens named** — every color / space / typography reference traces to a token in §3 (color tokens, status palette, spacing scale, typography, radius scale). The tokens are also spelled out in the mockup's inline CSS so the developer has a direct reference.
- [x] **Wireframe present** — `mockups/snapshots-page-v2.html` is 1168 lines of self-contained HTML with both states (drawer closed, drawer open) rendered top-to-bottom.
- [x] **A11y + tradeoffs captured** — §4 covers roles, focus order, keyboard, ARIA, contrast, reduced-motion. §8 covers 7 tradeoffs with the decision and the reason.
- [x] **Design artifacts table filled in** — §7 has the single row with `path` (canonical repo-relative), `kind: html-mockup`, `ac_refs` (all 33 ACs), `mockup_lane: text`, `fallback_reason: timeout`. No `od_url` (text lane); `lint: n/a` (text lane).
- [x] **Lane marker recorded** — `mockup_lane: text` in §7 and in the front-matter.
- [x] **`fallback_reason` recorded with an exact token** — `fallback_reason: timeout` is one of the Cardinal #7 enum tokens (`tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`). Cardinal #7 satisfied.

---

## 10 · Conformance loop (post-implementation)

After developer reports `implemented`, the conformance reviewer reads the diff and emits a `design-review.md` verdict against this spec's `pinned_spec_sha`. The reviewer must verify at minimum:

- AC-1.1 through AC-1.3 (page structure)
- AC-2.1 through AC-2.5 (chrome compaction)
- AC-3.1 through AC-3.7 (table scroll)
- AC-4.1 through AC-4.7 (drawer side-mode)
- AC-5.1 through AC-5.3 (control preservation)
- AC-A11Y-1 through AC-A11Y-5 (a11y)
- AC-MOCK-1 through AC-MOCK-3 (mockup fidelity)

Loop budget: ≤ 3 iterations. Iteration 3 FAIL → escalate to the leader with the remaining diffs.

Every verdict cites `pinned_spec_sha` (set at freeze). A verdict without the SHA is void.
