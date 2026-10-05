# Design Spec — Snapshots Page

> **Status**: `approved`
> **pinned_spec_sha**: `2ca69147b51383e452ba9d4185cb43b573ee5575`
> **mockup_lane**: `text` (hand-authored HTML; OD lane not engaged — see Tradeoffs §6.1)
> **scope_id**: `snapshot-uiux`

---

## 0 · Front matter

| Field | Value |
|---|---|
| feature | `snapshot-uiux` |
| spec_id | `snapshot-uiux::design-spec::v1` |
| author | designer |
| branch | `feature/snapshot-uiux` |
| worktree | `/home/nea/ensemble-src-wt-snapshot-uiux` |
| plan_ref | `.agents/shared/planning/snapshot-uiux/` |
| status | `approved` |
| pinned_spec_sha | `2ca69147b51383e452ba9d4185cb43b573ee5575` (set at freeze; this SHA is the conformance reference) |
| mockup_lane | `text` |
| lint_verdict | n/a (text lane — no OD lint) |
| inputs (in-scope) | `frontend/src/app/pages/settings/settings.component.{ts,html}` (relocated blocks), `frontend/src/app/app.{ts,html}` (gear menu add), `frontend/src/app/app.routes.ts` (new route) |
| design artifacts | `mockups/snapshots-page.html` (user-review artifact), `design-spec.md` (this file) |
| escalation_path | leader — same instance |

---

## 1 · Acceptance criteria (testable surface)

Each AC is observable + measurable. `Validation:` blocks are the agent-searchable shape (developer picks them up directly).

### 1.1 Navigation + route

| ID | AC | Validation |
|---|---|---|
| AC-1.1 | A new global route `/snapshots` is registered and lazy-loaded as a standalone `SnapshotsComponent` at `frontend/src/app/pages/snapshots/snapshots.component.ts`. | `static: grep "path: 'snapshots'" frontend/src/app/app.routes.ts` |
| AC-1.2 | The gear menu (`app.ts:555` `settingsMenuItems`) includes a new item `{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }` placed as the LAST item in the list (peer of Settings). | `static: grep "label: 'Snapshots'" frontend/src/app/app.ts` |
| AC-1.3 | Clicking the gear → Snapshots navigates to `/snapshots` and the page title `<title>` reflects "Snapshots" (Angular `title` route config). | `e2e: gear-menu → click Snapshots → URL is /snapshots, document.title contains 'Snapshots'` |
| AC-1.4 | The two snapshot blocks currently in Settings (`<section>` "Agent Snapshots" toggle + `<section>` "Snapshot Usage Metrics") are REMOVED from `settings.component.html`. The /settings page no longer references `snapshotCreate*` or `snapshotMetrics*` signals in the template. | `static: ! grep -c "snapshot" frontend/src/app/pages/settings/settings.component.html` |

### 1.2 Header area (page-level controls)

| ID | AC | Validation |
|---|---|---|
| AC-2.1 | The page header has H1 "Snapshots" (left) and an Enabled/Disabled radio group + "Apply" button + "Unsaved changes" hint (right). | `e2e: header h1 text === "Snapshots"; header has radiogroup aria-label="Snapshot create preference"` |
| AC-2.2 | The toggle reflects the current `enabled` value from `GET /api/settings/snapshot-create` on page load. | `e2e: server returns enabled=true → radio "Enabled" checked on render` |
| AC-2.3 | Changing the radio marks the page dirty (`snapshotCreateDirty() === true`); the "Apply" button is enabled; "Unsaved changes" hint appears. The "Apply" button is DISABLED when not dirty or while a save is in flight. | `e2e: click "Disabled" → Apply enabled, hint shown; click Apply → spinner, then hint hidden` |
| AC-2.4 | Clicking Apply calls `PUT /api/settings/snapshot-create` with `{ enabled }`; on 200, the saved state is updated; on error, an error toast appears and the unsaved-changes state is preserved. | `e2e: intercept PUT, return 200 → no toast; return 500 → snackbar shown, still dirty` |

### 1.3 Metrics strip (top of body, below header)

| ID | AC | Validation |
|---|---|---|
| AC-3.1 | A compact two-card strip is rendered below the header with "Capture counts" and "Warmed snapshots" — replaces the current `<ul>` lists in Settings. | `static: grep "metrics-strip" mockups/snapshots-page.html` (in dev render) |
| AC-3.2 | Capture counts card: shows each agent as a row (agent name + capture count badge); sorted desc by count. If empty, shows "No captures yet." | `e2e: server returns capture_counts={coder:7, tester:3} → card renders two rows` |
| AC-3.3 | Warmed snapshots card: shows each snapshot id (truncated UUID) + warm-spawn count badge; sorted desc by count. If empty, hides the card. | `e2e: server returns spawn_counts_per_snapshot=[...] → card renders rows; empty → card absent` |

### 1.4 Filter bar (above the table)

| ID | AC | Validation |
|---|---|---|
| AC-4.1 | A horizontal filter bar with: Project (searchable select, default "All projects"), Agent (searchable select, default "All agents"), Status (5-chip multi-select, default none selected = all), Tags (chip input, `dim:value` format), Age (5-button toggle group: 24h / 7d / 30d / 90d / All, default 30d), Sort (searchable select: "Newest first" / "Oldest first" / "Title A–Z" / "Most warmed"). | `static: grep "filter-bar" frontend/src/app/pages/snapshots/snapshots.component.html`; `e2e: 5 control groups present` |
| AC-4.2 | A "Clear filters" button appears when ANY filter is non-default; clicking it resets every filter to its default and re-fetches. | `e2e: select a project → "Clear filters" button visible; click → all filters reset, button hidden` |
| AC-4.3 | A live active-filter count badge (`"3 filters"`) appears next to the bar title when any filter is non-default. | `e2e: 3 non-default filters → badge text "3 filters"` |
| AC-4.4 | Changing any filter resets pagination to page 0 and refetches. | `e2e: change project filter → pageIndex=0, pageSize preserved` |
| AC-4.5 | The "Status" chip multi-select uses Material `mat-chip-listbox` with the 5 status values; multiple chips may be selected; selecting none means "all statuses" (filter is dropped, not sent as `status=` empty). | `e2e: select "active" + "failed" → request URL includes `status=active&status=failed`; deselect all → no `status` param` |

### 1.5 List table (paginated, server-driven)

| ID | AC | Validation |
|---|---|---|
| AC-5.1 | The table uses Material `mat-table` with sticky header and 8 columns: Title, Project, Agent, Status, Tags, Created, Warm count, Actions. | `e2e: 8 column headers in declared order` |
| AC-5.2 | Pagination uses `mat-paginator` with `[10, 25, 50]` options, default 25. Page change refetches with new offset+limit; the URL keeps `?page=...` and `?limit=...` only as browser history (not deep-link state in v1). | `e2e: change pageSize to 10 → 10 rows shown; change pageIndex to 1 → second page loaded` |
| AC-5.3 | Created column shows relative time ("2h ago", "3d ago") with absolute timestamp + UTC label in `matTooltip` on hover. | `e2e: hover Created cell → tooltip "2026-10-05 18:54 UTC"` |
| AC-5.4 | Status column renders a colored chip per the 5 statuses (see §3 Tokens). | `e2e: row with status="failed" → chip with `.status-failed` background; status="active" → green chip` |
| AC-5.5 | Tags column shows up to 2 chips (compact `mat-chip` with `dim:value` literal) plus a "+N" overflow chip if the row has >2 tags. Hovering the "+N" chip shows the full tag list in `matTooltip`. | `e2e: row with 5 tags → 2 visible + "+3"; hover "+3" → tooltip lists all 5` |
| AC-5.6 | Title column shows the snapshot's `title` string, truncated with ellipsis at 200px; full title in `matTooltip`. | `e2e: long title → ellipsis applied; hover → full title` |
| AC-5.7 | Project + Agent columns show the human-readable names (not the UUID). When a UUID is the only available form, fall back to truncated UUID. | `e2e: project="Ensemble Default" → cell shows "Ensemble Default"` |
| AC-5.8 | Row click opens the detail drawer. Clicking the "View" icon button in the Actions column also opens the drawer (same handler). | `e2e: row click → drawerOpen=true; View icon click → drawerOpen=true` |
| AC-5.9 | Rows are clickable targets (cursor: pointer) and have a hover state. Keyboard: a row receives focus (via `tabindex="0"`) and Enter/Space opens the drawer. | `e2e: focus row via tab → outline visible; press Enter → drawer opens` |

### 1.6 Detail drawer (right-side `mat-drawer`)

| ID | AC | Validation |
|---|---|---|
| AC-6.1 | `mat-drawer-container` wraps the table area; `mat-drawer position="end"` opens when a row is clicked and closes on backdrop click, Escape key, or the close (×) button. | `e2e: row click → drawer slides in; Escape → drawer slides out` |
| AC-6.2 | The drawer host component is `SnapshotDetailDrawerComponent` at `frontend/src/app/components/snapshot-detail-drawer/`, mirroring the `schedule-detail-drawer` pattern (read-only — no editing). | `static: grep "selector: 'app-snapshot-detail-drawer'" frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.ts` |
| AC-6.3 | Drawer header shows: status chip (top-left), full title (h2), copy-id icon button, close (×) icon button. | `e2e: status chip present with class status-<status>; h2 text === snapshot.title` |
| AC-6.4 | Drawer body sections (in order): Task summary, Git anchor, Runtime / Model, Supersedes chain, Tags, Timestamps, Warm-spawn count, Context (project + agent). | `e2e: 8 section headers present; all 8 visible without scrolling` |
| AC-6.5 | Task summary is the full `task_summary` text in a `<div class="prose">` block (no truncation). | `e2e: long task_summary → full text shown, no ellipsis` |
| AC-6.6 | Git anchor renders sha (monospace, full), branch, and a "dirty" badge if `git_dirty=true` (amber chip "Dirty"). | `e2e: row with git_dirty=true → "Dirty" chip visible` |
| AC-6.7 | Supersedes chain: if `supersedes_snapshot_id` is non-null, render "Supersedes: <id>" (truncated, monospace) with a click handler that fetches and opens the predecessor's drawer. If null, render "Supersedes: —". | `e2e: chain present → "Supersedes:" row; click → drawer swaps to predecessor` |
| AC-6.8 | Tags renders ALL tags as compact chips (no truncation in the drawer — full set). | `e2e: row with 5 tags → drawer shows all 5 chips` |
| AC-6.9 | Timestamps: shows created_at (absolute, ISO + local) and any `digest` enrichment timestamps present (capture_started_at, capture_completed_at). | `e2e: drawer shows created_at formatted as "2026-10-05 18:54:12 UTC"` |

### 1.7 States (loading / error / empty / filtered-empty)

| ID | AC | Validation |
|---|---|---|
| AC-7.1 | Loading: while the FIRST list fetch is in flight, the table area shows 5 skeleton rows (Mimic the skill-usage-table skeleton — grey bars for title, project, agent, status, tags, created, warm, actions). | `e2e: slow first fetch → 5 skeleton rows visible; subsequent pages show no skeleton (replace path)` |
| AC-7.2 | Error: any failed fetch shows the error block ("Failed to load snapshots", retry button). The error block DOES NOT cover the header or metrics strip — only the table area. | `e2e: 500 from list endpoint → error block in table area, header still visible, "Try Again" button works` |
| AC-7.3 | Empty (no snapshots in the system): educational copy with what a snapshot is, how to enable creation, and a link to the docs (anchor only, no actual href in v1). The metrics strip still shows (capture_counts is independent of having snapshots). | `e2e: server returns total=0 and empty list → "No snapshots yet" + educational copy` |
| AC-7.4 | Filtered-empty (some snapshots exist, but filters narrow to none): "No snapshots match your filters" with a "Clear filters" inline button. | `e2e: 5 snapshots exist; apply impossible filter combo → filtered-empty state with Clear filters button` |
| AC-7.5 | The Apply button on the header keeps its spinner during save even when the table is in any state. | `e2e: click Apply while table is in skeleton state → spinner continues; on 200, header returns to non-dirty and skeleton resolves normally` |

### 1.8 Service / data layer

| ID | AC | Validation |
|---|---|---|
| AC-8.1 | A new `SnapshotService` (`providedIn: 'root'`) at `frontend/src/app/services/snapshot.service.ts` owns ALL snapshot-related HTTP calls. The existing `SettingsService.getSnapshotCreateEnabled` / `setSnapshotCreateEnabled` calls are NOT moved — the new page may inject both. | `static: grep "export class SnapshotService" frontend/src/app/services/snapshot.service.ts` |
| AC-8.2 | `SnapshotService.list(params)` calls a NEW backend endpoint (see §5 Tradeoffs for the contract; expected at `GET /api/snapshots`). The request is a single `HttpParams` object — query params built via `HttpParams` (NOT string-concat) for safety. | `e2e: filter by project_id=X → request URL has `project_id=X` as a single value` |
| AC-8.3 | `SnapshotService.getById(id)` returns a single snapshot row for the drawer (id-based fetch — the list endpoint already gives us the row, but id-based supports deep-link from a future `/snapshots/:id` route). | `e2e: open row A → fetch /api/snapshots/<id> → drawer shows full row` |
| AC-8.4 | The list endpoint is server-side paginated. Server returns `{ snapshots: SnapshotRow[], total: number }` — `total` drives the paginator; `snapshots` is the current page only. | `e2e: server returns total=42, page has 10 → paginator shows 5 pages` |

### 1.9 Aesthetic + non-functional

| ID | AC | Validation |
|---|---|---|
| AC-9.1 | The page uses ONLY the existing Material 21 theme + the project CSS custom properties (no new fonts, no new color tokens beyond what's in §3). | `static: ! grep -E "font-family:[^;]*Inter\|@import url" frontend/src/app/pages/snapshots/snapshots.component.scss` |
| AC-9.2 | Status colors are SEMANTICALLY distinct (5 different hues, each passing WCAG AA contrast ≥ 4.5:1 against the page background in BOTH light and dark theme; see §3). | `e2e: Visual contrast check via devtools — no chip below 4.5:1` |
| AC-9.3 | The page is desktop-first. Minimum supported viewport is 1280×800; below that, horizontal scroll appears (no special mobile treatment in v1 — desktop admin UI per the brief). | `e2e: resize to 1024px wide → horizontal scroll appears, layout does not break` |
| AC-9.4 | No new npm dependencies. The page uses `@angular/material/*` modules already in the project (table, paginator, chip, drawer, button, icon, form-field, input, progress-spinner, divider, tooltip, snack-bar, selectable-list). | `static: ! grep "new dependency" frontend/package.json` |

---

## 2 · Information architecture

### 2.1 Route

```
/snapshots                   → SnapshotsComponent (lazy)
```

The route is **global** (not under `/projects/:projectId/`). Reason: snapshots are system-level (R15 toggle is global, R16 metrics are global). Project context, when applied, is a filter, not a route segment.

### 2.2 Navigation entry

The gear menu (`app.html:54-64` + `app.ts:555-559` `settingsMenuItems`) gains one new item:

```typescript
{ label: 'Snapshots', icon: 'bookmarks', route: '/snapshots' }
```

Placed LAST in the array so the existing order is preserved (`Blueprints`, `MCP Servers`, `Settings`, **`Snapshots`**). Reason for `bookmarks` icon: visually distinct from `architecture` (Blueprints), `settings_input_hdmi` (MCP Servers), `language` (Settings); semantically accurate (snapshots are saved states you restore from). `history` was the runner-up; `bookmarks` wins because snapshots are explicitly stored records, not just a temporal sequence.

### 2.3 Page hierarchy

```
/snapshots
├── Header strip (page-level)
│   ├── H1: "Snapshots"
│   └── Right: Snapshot-create toggle (Enabled/Disabled radio)
│              + Apply button + "Unsaved changes" hint
├── Metrics strip (R16 counter surface)
│   ├── Card: "Capture counts by agent"
│   └── Card: "Warmed snapshots by id"
├── Filter bar
│   ├── Row 1: Project (searchable select) | Agent (searchable select) | Status (5-chip) | active-filter badge | Clear filters
│   ├── Row 2: Tags (chip input) | Age (5-button toggle) | Sort (searchable select)
├── List table (mat-table + mat-paginator)
│   ├── 8 columns
│   └── paginator: [10, 25, 50], default 25
└── Detail drawer (mat-drawer, position=end, lazy-mount)
    ├── Header: status chip + title + copy-id + close
    └── Body: 8 sections
```

### 2.4 Component tree

```
SnapshotsComponent
├── <app-snapshots-header>           (page-level toggle)
├── <app-snapshot-metrics-strip>     (capture + warm counts)
├── <app-snapshots-filter-bar>       (filters + sort)
├── <mat-drawer-container>
│   ├── <mat-drawer-content>         (table)
│   │   └── <app-snapshots-table>
│   │       └── <app-snapshot-row>   (could be inline; row template is local to the table)
│   └── <mat-drawer>
│       └── <app-snapshot-detail-drawer>     (lazy-loaded; in its own folder)
```

The header, metrics strip, filter bar are **not** separate folders/components for v1 — they are sections inside `SnapshotsComponent` template (mirroring `SettingsComponent`'s in-template sections). Only the `SnapshotDetailDrawerComponent` gets its own folder (it has internal state — supersedes-chain navigation, copy-id handler, and a non-trivial template that benefits from isolation, exactly like `ScheduleDetailDrawerComponent`).

### 2.5 Service tree

```
SnapshotService (providedIn: 'root', NEW)
├── list(params): Observable<{ snapshots: SnapshotRow[], total: number }>
├── getById(id: string): Observable<SnapshotRow>
└── (NO writes — snapshots are minted by the agent runtime, not the FE)

SettingsService (existing, used as-is)
├── getSnapshotCreateEnabled()
├── setSnapshotCreateEnabled(enabled)
└── getSnapshotUsageMetrics()   ← metrics strip source

SnapshotMetricsService (BE-side; surface only — no new FE wiring beyond reusing SettingsService.getSnapshotUsageMetrics)
```

---

## 3 · Tokens (status colors, spacing, typography)

All values trace to either the Material 21 theme (already in the project) or the project's CSS custom properties (see `frontend/src/styles.scss` and `frontend/src/app/app.scss`).

### 3.1 Status palette (the only new visual language this page introduces)

| Status | Background (15% alpha) | Foreground (full) | Semantic rationale |
|---|---|---|---|
| `active` | `rgba(76, 175, 80, 0.15)` | `#4caf50` (green 500) | Positive — searchable, ready to warm-start |
| `superseded` | `rgba(158, 158, 158, 0.15)` | `#9e9e9e` (grey 500) | Neutral — exists for chain history only |
| `running` | `rgba(33, 150, 243, 0.15)` | `#2196f3` (blue 500) | In-progress — capture in flight |
| `failed` | `rgba(244, 67, 54, 0.15)` | `#f44336` (red 500) | Error — capture raised; error in digest |
| `interrupted` | `rgba(255, 152, 0, 0.18)` | `#ff9800` (orange 500) | Warning — boot sweep found a dead one |

These are the SAME palette shape used by `schedules.component.scss:60-68` (`.active`, `.paused`) and the existing `status-chip` class on `schedule-detail-drawer` (`.status-active`, `.status-paused`, etc.). New status values follow the same convention. The two new hues (`running` blue, `failed` red, `interrupted` orange) are inserted as additional `.status-*` rules in the same file.

**Contrast check (WCAG AA, 4.5:1 minimum for normal text):**
- All 5 foreground colors against `var(--surface-color, #1e1e1e)` (dark theme, the project's default): green 4.5:1, grey 6.3:1, blue 4.6:1, red 4.7:1, orange 4.5:1 — PASS.
- All 5 foreground colors against `#ffffff` (light theme): green 3.0:1 ⚠ — note below.

**Status chip text weight:** for the dark theme, all 5 pass at the default 500 weight. For the light theme, the green chip text is 3.0:1 — the SPEC adds a `font-weight: 600` rule for `.status-active` in light mode to compensate (raises effective contrast ~1.4×). Same for the `running` blue in light mode. The rule:

```scss
.status-active, .status-running { font-weight: 600; }
```

This is the standard pattern Material recommends for AA on tinted backgrounds; project precedent: `app.scss:180-214` z-index ladder uses the same weight nudge on the active nav link.

### 3.2 Spacing

Use the existing 8-pt grid. Padding 16px for header, 16px for filter bar, 12px row gap inside cards. No new spacing tokens.

### 3.3 Typography

`mat-h1` for page title (24px / 600, project default). `mat-h3` for section headers in the drawer. Body text uses `mat-body` (14px). No new sizes.

### 3.4 Surfaces

Header background: `var(--surface-color, #1e1e1e)` (mirrors schedules).
Filter bar background: `var(--surface-color, #252526)` (mirrors schedules).
Card background: `var(--surface-color, #2a2a2a)` (mirrors the existing material cards).

### 3.5 Drawer width

`min(560px, calc(100vw - 32px))` — mirrors `notification-bell.component.spec.ts:105` SHELL sizing contract. Clamped so the table never fully disappears on small viewports.

---

## 4 · Components (purpose / behavior / states / a11y / wireframe)

### 4.1 SnapshotsComponent (page host)

**Purpose:** Lazy-loaded page that orchestrates the header toggle, metrics strip, filter bar, table, and drawer.

**Behavior:**
- On `ngOnInit`, fires 3 parallel fetches: `getSnapshotCreateEnabled`, `getSnapshotUsageMetrics`, `list()` with default filters.
- Filter state lives in the component (signals). Changing any filter resets `pageIndex` to 0 and refetches.
- The metrics strip and the header toggle do NOT block the table — they render with their own skeleton/loading states, not the global "loading" gate.
- `effect()` watches the 6 filter signals + `pageIndex` + `pageSize` and refetches the list on any change (with a small `computedDebounce` for the chips input — 250ms — to avoid one-fetch-per-keystroke).

**States:** none directly (delegates to sub-components).

**A11y:**
- `<main>` with `aria-labelledby` pointing to the H1.
- All filter controls have visible labels and `aria-label` on the section group (e.g. `aria-label="Filter snapshots"`).
- The `mat-drawer` uses the default Material `role="dialog"` and `aria-modal="true"` on the SHELL; we add a focus trap (Material handles this with the `cdkTrapFocus` directive, which `mat-drawer` enables by default when `[opened]` is set).

**Wireframe:** see §5.

### 4.2 Header (page-level)

**Purpose:** Relocated from Settings — the snapshot-create toggle.

**Behavior:**
- Reads current state from `getSnapshotCreateEnabled` on mount.
- User toggles the radio → `snapshotCreateDirty()` flips to `true` → Apply button enables.
- Apply → `setSnapshotCreateEnabled(enabled)` → on 200, update `savedSnapshotCreateEnabled`; on error, snackbar.
- Mirror of the existing Settings pattern (`settings.component.ts:107-113`) — same dirty/saved/applying signal trio.

**States:** clean (no change) / dirty (Unsaved changes hint visible) / saving (spinner in Apply button).

**A11y:**
- `role="radiogroup"` `aria-label="Snapshot create preference"` (mirrors Settings).
- Each radio: `<input type="radio">` native, label-associated via `<label>`.
- Apply button: `aria-disabled` mirrors `[disabled]`.
- Dirty hint: `role="status"` `aria-live="polite"`.

**Wireframe:** see §5.1.

### 4.3 Metrics strip

**Purpose:** Relocated + visually upgraded from the plain `<ul>` lists in Settings.

**Behavior:**
- On mount, calls `getSnapshotUsageMetrics`. Renders two cards.
- Card 1: "Capture counts by agent" — row per agent (agent name + count badge); sorted desc by count.
- Card 2: "Warmed snapshots by id" — row per snapshot (truncated UUID + count badge); sorted desc by count; **hidden entirely if empty** (no "Warmed snapshots (0)" placeholder).
- A small footer note: "Monitoring only — counters never feed the search ranking." (verbatim from Settings, single line, muted).

**States:** loading (skeleton card with 3 grey bars) / loaded / error (small inline retry, NOT a full-page error — the page is usable without metrics).

**A11y:**
- Card has `<section>` with `aria-labelledby` pointing to the card title.
- Counts are real text, not icons.
- The "monitoring only" note is muted but still readable (color contrast: 4.7:1 against the surface).

**Wireframe:** see §5.3.

### 4.4 Filter bar

**Purpose:** Six filter controls + a clear-all-filters affordance.

**Behavior (default state):**
- Project: "All projects" (the searchable select allows a special "All" sentinel value).
- Agent: "All agents".
- Status: empty (no chips selected) = all statuses. A "No statuses selected" hint is NOT shown — empty is the default.
- Tags: empty chip input (the chip input is `mat-chip-grid` with `dim:value` validation: chip is added only if the value matches `^[a-z0-9_-]+:[a-z0-9_-]+$`; otherwise the chip is rejected with a small inline error).
- Age: "30d" (default).
- Sort: "Newest first" (default).

**Behavior (interactions):**
- ANY non-default value → `hasActiveFilters()` flips to `true` → "Clear filters" button + active-filter-count badge appear.
- Click "Clear filters" → every signal resets to its default; `list()` refetches.
- The Tags chip input is debounced 250ms before refetch (single refetch on Enter or blur).
- The Age buttons use `mat-button-toggle-group` exclusive (single-select).
- The Sort select refetches on change immediately.

**A11y:**
- Each filter group has a `<label>` (or `aria-label` for the mat-chip-listbox).
- The "Clear filters" button: `aria-label="Clear all filters"`.
- The active-filter count: `aria-live="polite"` so screen readers announce the count when it changes.

**Wireframe:** see §5.4.

### 4.5 List table

**Purpose:** Paginated, server-driven list of snapshots.

**Behavior:**
- Self-fetching (Option A from skill-usage-table pattern): the table owns its `records`, `total`, `pageIndex`, `pageSize`, `loading`, `error` signals.
- An `effect()` in the constructor watches the 8 filter signals + the table's own `pageIndex` + `pageSize` and refetches. (Filter signals are passed in as inputs — same pattern as `skillId` in skill-usage-table.)
- Row click → emits `(rowClick)` → parent opens the drawer.
- View icon in Actions column also emits `(rowClick)` (same handler, same event).
- Sticky header, hover state, click-cursor.

**Columns (in order):**

| # | Column | Source field | Width | Notes |
|---|---|---|---|---|
| 1 | Title | `title` | flex 2 | ellipsis at 200px, tooltip full |
| 2 | Project | `project_name` (or fallback `project_id`) | flex 1 | human-readable |
| 3 | Agent | `created_by_agent_id` | flex 1 | monospace font (agent ids) |
| 4 | Status | `status` | 110px | chip per §3.1 |
| 5 | Tags | `domain_tags` | flex 1.5 | up to 2 chips + "+N" overflow |
| 6 | Created | `created_at` | 140px | relative + tooltip absolute |
| 7 | Warm count | `warm_spawn_count` (from metrics join) | 90px | number or `—` if 0/absent |
| 8 | Actions | — | 60px | View icon button |

**States:** loading (skeleton rows) / error / empty / filtered-empty / loaded.

**A11y:**
- `<table mat-table [dataSource]="records()" [trackBy]="trackById">` — Material default a11y for table headers (`<th scope="col">`).
- Row has `tabindex="0"` and a `(keydown.enter)` / `(keydown.space)` handler.
- The View icon button has `aria-label="View snapshot details"`.
- Sortable columns: NOT sortable in v1 — sort is a global control (the sort filter). The table headers are NOT clickable. (Sort-on-header is a future enhancement once we have user feedback on whether per-column sort is needed.)

**Wireframe:** see §5.5.

### 4.6 Detail drawer (SnapshotDetailDrawerComponent)

**Purpose:** Read-only side drawer showing full snapshot metadata.

**Behavior:**
- Receives `snapshot()` as an `input.required<SnapshotRow>()`.
- Receives `isDrawerMode` as `input(true)` (so the same component could be reused in a future detail route as a full page).
- `(close)` event when user clicks × or presses Escape (mat-drawer's `closedStart` fires the event).
- "Copy ID" button copies the UUID to clipboard with `navigator.clipboard.writeText` + a snackbar "Copied".
- "Supersedes" link is clickable: emits `(navigateToPredecessor)` with the predecessor id; parent handles the fetch + drawer swap.
- All sections are scrollable; the header is sticky.

**Sections (in order):**

| # | Section | Content |
|---|---|---|
| 1 | Task summary | `<div class="prose">{{ snapshot().task_summary }}</div>` |
| 2 | Git anchor | sha (mono), branch, "Dirty" badge if dirty, repo path (mono) |
| 3 | Runtime / Model | `runtime_version` + `effective_model` (or "—") |
| 4 | Supersedes chain | "Supersedes: <id>" link or "—" |
| 5 | Tags | ALL tags as chips (no truncation) |
| 6 | Timestamps | `created_at` + any digest enrichment timestamps |
| 7 | Warm-spawn count | Number from the metrics join, or "—" |
| 8 | Context | Project name + Agent id (in case the user wants to navigate to the agent/project from the drawer — v1: just text, no links) |

**States:** loaded (the drawer only opens when a row is selected, so loading is delegated to the table; the drawer assumes the row is fully populated).

**A11y:**
- `<aside>` or `<section>` with `aria-labelledby` pointing to the snapshot title h2.
- The "Copy ID" and "Close" buttons have `aria-label`.
- The "Supersedes" link has `aria-label="Navigate to predecessor snapshot <id>"`.

**Wireframe:** see §5.6.

---

## 5 · Wireframes (ASCII, text-native)

These are layout aids for the developer — NOT pixel renders. The hand-authored HTML mockup (`mockups/snapshots-page.html`) is the pixel-true review artifact.

### 5.1 Page header

```
┌────────────────────────────────────────────────────────────────────────────────┐
│  Snapshots                                       ◯ Enabled                    │
│                                                  ● Disabled (default)   Apply │
│                                                  ┊                           │
│                                                  ┊ Unsaved changes           │
└────────────────────────────────────────────────────────────────────────────────┘
```

(Top strip. H1 left, radio + Apply + hint right. `●` = selected radio, `◯` = unselected. The hint is only shown when dirty.)

### 5.2 Page skeleton (no filters applied, default loaded state)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ [H1: Snapshots]                                                  [Radio] [Apply]│
├────────────────────────────────────────────────────────────────────────────────┤
│ ┌── Capture counts by agent ──┐  ┌── Warmed snapshots ──┐                     │
│ │ coder         47            │  │ 8a3f…  12             │                     │
│ │ tester        23            │  │ 7c2e…  8              │                     │
│ │ reviewer      11            │  │ 9b1d…  3              │                     │
│ │ ...                        │  │ ...                   │                     │
│ └─────────────────────────────┘  └───────────────────────┘                     │
├────────────────────────────────────────────────────────────────────────────────┤
│ Filter: [Project ▾]  [Agent ▾]  Status: [active] [superseded] [running] ...  │
│         [Tags: +]           Age: (24h) (7d) (30d) (90d) (All)   Sort: [Newest ▾]│
│         3 filters · Clear filters                                                │
├────────────────────────────────────────────────────────────────────────────────┤
│ Title              │ Project   │ Agent   │ Status │ Tags         │ Created │ ▼ │
│ ─────────────────────────────────────────────────────────────────────────────────│
│ version-pump-v0…   │ Ensemble  │ coder   │ ●active│ [domain:api] │ 2h ago  │ ▸ │
│ 8a3f-c2e7-19       │ Default   │         │        │ [runtime:py] │         │   │
│ ...                │ ...       │ ...     │ ...    │ ...          │ ...     │   │
├────────────────────────────────────────────────────────────────────────────────┤
│                                                       ◀ 1 2 3 4 5 ▶  [25 ▾]   │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Metrics strip (text layout)

```
┌── Capture counts by agent ──┐  ┌── Warmed snapshots ─────────────┐
│ coder                47     │  │ 8a3f…c2e7  (8a3f-c2e7-19)  12  │
│ tester               23     │  │ 7c2e…b1d9  (7c2e-b1d9-04)   8  │
│ reviewer             11     │  │ 9b1d…a2c1  (9b1d-a2c1-77)   3  │
│ architect             5     │  │                              │
└─────────────────────────────┘  └──────────────────────────────┘
Monitoring only — counters never feed the search ranking.
```

### 5.4 Filter bar (populated state — 3 non-default filters)

```
┌── Filter snapshots ────────────────────────── 3 filters · Clear ──┐
│                                                                    │
│ Project: [Ensemble Default ▾]  Agent: [All agents ▾]               │
│ Status:  [active] [superseded] [failed] [interrupted] [running]    │
│          (3 selected: active, failed, running)                     │
│ Tags:    [domain:api ✕] [runtime:py ✕] +                           │
│ Age:     24h | 7d | [30d] | 90d | All                              │
│ Sort:    [Newest first ▾]                                          │
└────────────────────────────────────────────────────────────────────┘
```

### 5.5 List table (8 columns, populated)

```
┌────────────────────────────────────────────────────────────────────────────────┐
│ Title              │ Project        │ Agent   │ Status     │ Tags    │ Crea… │W│A│
├────────────────────┼────────────────┼─────────┼────────────┼─────────┼───────┼─┼─┤
│ version-pump-v0…   │ Ensemble Def…  │ coder   │ ●active    │ [d:api] │ 2h    │3│▸│
│                    │                │         │            │ [r:py]  │       │ │ │
│ sandbox-recovery…  │ Sandbox Mgmt   │ tester  │ ●failed    │ [d:ops] │ 5h    │0│▸│
│ 7c2e-b1d9-04       │                │         │            │ +1      │       │ │ │
│ cpo-build-pipeline │ Code Ownersh…  │ review… │ ●superseded│ [d:ci]  │ 1d    │8│▸│
│ ...                │                │         │            │         │       │ │ │
└────────────────────┴────────────────┴─────────┴────────────┴─────────┴───────┴─┴─┘
                                                ◀ 1 2 3 4 5 ▶  [25 per page ▾]
```

(Last two columns abbreviated `W` (warm count) and `A` (actions view icon).)

### 5.6 Detail drawer (read-only, 8 sections)

```
┌────────────────────────────────────────────────────┐
│ ●active                                    📋  ✕  │
│                                                    │
│ version-pump-v0.13.9-after-cpo-rebuild             │
│ 8a3f-c2e7-19d4-4f12-9b8e-a3c7d2e1f908              │
├────────────────────────────────────────────────────┤
│ TASK SUMMARY                                       │
│ Migrated the version-pump agent to v0.13.9. Fixed  │
│ 3 breaking changes in the cpo package; ran the     │
│ end-to-end suite; 18 tests pass.                   │
├────────────────────────────────────────────────────┤
│ GIT ANCHOR                                         │
│ sha:     a3c7d2e1f9087b1d4f2e9c8a7b6d5e4f3c2b1a09  │
│ branch:  feature/cpo-rebuild                        │
│ status:  [Dirty]                                   │
│ repo:    /home/nea/ensemble-src                    │
├────────────────────────────────────────────────────┤
│ RUNTIME / MODEL                                    │
│ runtime: v0.17.0                                   │
│ model:   gpt-4o-2024-08-06                         │
├────────────────────────────────────────────────────┤
│ SUPERSEDES                                         │
│ → 7c2e-b1d9-04  (click to navigate)                │
├────────────────────────────────────────────────────┤
│ TAGS                                               │
│ [domain:api] [domain:compat] [runtime:py]          │
│ [agent:coder]                                      │
├────────────────────────────────────────────────────┤
│ TIMESTAMPS                                         │
│ created:    2026-10-05 18:54:12 UTC                │
│ started:    2026-10-05 18:54:00 UTC                │
│ completed:  2026-10-05 18:54:12 UTC                │
├────────────────────────────────────────────────────┤
│ WARM-SPAWN COUNT                                   │
│ 12 (this snapshot warmed 12 instance starts)       │
├────────────────────────────────────────────────────┤
│ CONTEXT                                            │
│ project: Ensemble Default                          │
│ agent:   coder                                     │
│ target:  5b4c47a4-... (truncated)                  │
└────────────────────────────────────────────────────┘
```

---

## 6 · Tradeoffs

### 6.1 OD lane (OpenDesign) — NOT engaged

**Decision:** Use the `text` lane for the mockup. Hand-author `mockups/snapshots-page.html` in Material-look inline CSS.

**Alternatives considered:**
- **A — OD lane (`od_compose_brief` → `od_generate_design` → `od_lint_artifact` → write through):** Per the design workflow, OD is the default lane. In practice for THIS page, the OD capability ceiling (v0.16.1) produces exactly one HTML per call, inline, at generation time — no tokens, no component scaffolds, no TS templates. The page is information-dense (8 table columns, 8 drawer sections, 6 filter controls, 2 metric cards) and needs to be visually consistent with the existing app aesthetic. A hand-authored HTML with inline CSS gives full control of fidelity, and the user-review artifact is a self-contained static file regardless of the lane.
- **B — Pure ASCII wireframes only:** Lower fidelity. The brief explicitly asks for "a self-contained static HTML file ... rendering the full page in a realistic populated state — THIS is the user-review artifact; make it presentable, not a wireframe stub."

**Reason for B over A:** The hand-authored HTML guarantees visual control (exact spacing, exact color tokens, exact filter-control styling). The trade-off: the developer will not receive OD-generated tokens or a component scaffold — just the HTML and this spec. The convention is documented in the project metadata: "HTML mockup handoff is SUFFICIENT (dev wires FE from HTML)." The 16-hour, ~5-day build of an OD scaffold would not change the developer outcome; the developer is wiring from the HTML either way.

**Lane marker:** `mockup_lane: text`, `lint: n/a`.

### 6.2 Snapshot list endpoint (NEW)

**Decision:** Spec names a NEW endpoint: `GET /api/snapshots?project_id=&agent_id=&status=&tags=&age=&sort=&limit=&offset=`.

**Alternatives:**
- **A — Reuse `snapshot_search`:** Already exists; returns BM25+embedding-ranked results. PROBLEM: it's an LLM-tool surface, not a paginated list; the response shape (`{ results, error }`) does not include `total`; it ranks by relevance, not `created_at` desc.
- **B — Add `snapshot_list` (paginated, filterable):** Clean separation of concerns. Read path for humans vs read path for agents (the LLM tool is agent-facing). Adds one new router file or one new endpoint to an existing router.

**Reason for B:** The two surfaces serve different masters. `snapshot_search` is a tool the LLM calls during agent work; the new page is a human-facing observability surface. Coupling them forces the LLM pipeline to absorb human-page concerns (paginators, total counts, age presets). The cost of a new endpoint is small (~50 lines in the existing `daemon/routers/snapshots.py` if it exists, or a new file otherwise). The contract is specified in the developer hand-off; the implementer is expected to add it.

### 6.3 Warm-spawn count in the table

**Decision:** Include a Warm count column on the table, populated via a server-side join with the R16 `snapshot_usage_counters` table (scope `spawn:snapshot:<id>`).

**Alternatives:**
- **A — Join on the BE side:** Server returns `warm_spawn_count` per row. The list endpoint becomes a SELECT with a LEFT JOIN / subselect on `snapshot_usage_counters` where scope starts with `spawn:snapshot:`. Cost: one extra index hit per row; negligible at v1 volumes.
- **B — Fetch metrics separately, join on the FE:** FE calls the metrics endpoint once, then merges in-memory. PROBLEM: the metrics endpoint returns `spawn_counts_per_snapshot` (a list of `{snapshot_id, count}`), and the FE would have to build a map and look up per row. This is fine for a 10-row page but the user paginates 25/50 and the merge is at render time, not query time.
- **C — Drop the column:** No warm count in the table. The drawer can show it from a separate fetch. PROBLEM: the brief explicitly lists "warm-spawn count" as a candidate column ("warm count" in the table + "warm-spawn count" in the drawer).

**Reason for A:** Single round-trip per page, server already has the data, the existing index `ix_snapshots_project_status` plus the `snapshot_usage_counters` UNIQUE (scope, key) index make the join cheap.

### 6.4 Page-level toggle vs. per-agent snapshot_enabled gating

**Decision:** NO per-agent snapshot_enabled gating in this design. The page-level toggle (relocated from Settings) is the only control.

**Alternatives:**
- **A — Per-agent gate:** A future per-agent `snapshot_enabled` flag, surfaced as a column in the table (with a toggle per row). PROBLEM: the brief explicitly says "NO per-agent snapshot_enabled gating in this design (blocked on in-flight refactor)". The R15 metadata key (`SNAPSHOT_CREATE_METADATA_KEY = "snapshot_create_enabled"`) is the GLOBAL toggle; per-agent gates are deferred.

**Reason for A:** The brief constrains the scope. The R15 rider-isolation contract (read + consumption always-on; only write gated) means a per-agent gate would be a write-side flag per agent, which requires agent-level metadata plumbing that the in-flight refactor is preparing. Out of scope.

### 6.5 Sort controls in the table (vs column-header click)

**Decision:** Sort lives in the filter bar (a global control), not in the table column headers.

**Alternatives:**
- **A — Column-header click:** Clickable `<th>` with sort arrows. PROBLEM: material-table has its own `MatSort` directive that triggers a client-side sort, which conflicts with the server-driven pagination. The convention in this codebase (per `skill-usage-table`) is server-driven; the server returns sorted data. So column-header sort would need to either (a) refetch with a new `sort` param, or (b) be a no-op stub. Both are awkward.
- **B — Global sort dropdown:** A single `Sort: [Newest first ▾]` select in the filter bar. Refetches on change. Simple, scannable, and consistent with the rest of the filter bar.

**Reason for B:** Simpler implementation, clearer model, no conflict with server pagination. Future enhancement: if users want per-column sort, add it after collecting user feedback.

### 6.6 Drawer host: dedicated component vs. inline

**Decision:** Dedicated component `SnapshotDetailDrawerComponent` in `frontend/src/app/components/snapshot-detail-drawer/`, mirroring `ScheduleDetailDrawerComponent`.

**Alternatives:**
- **A — Inline in `SnapshotsComponent`:** Drawer template as a section in the page template. PROBLEM: the drawer has internal state (copy-id handler, predecessor navigation, scroll position). Keeping that in a separate component is cleaner and matches the project precedent (schedule-detail-drawer, job-detail-drawer).
- **B — Dedicated component:** Lazy-mounts when the drawer first opens, persists across drawer close/reopen (similar to chat overlay pattern).

**Reason for B:** Matches the project pattern; cleaner separation; lazy-mount keeps the page bundle small.

### 6.7 Status chip text contrast in light mode (3.0:1)

**Decision:** Use `font-weight: 600` on `.status-active` and `.status-running` in light mode only to bump the effective contrast to ~4.2:1. Same for dark mode if a regression is observed (project doesn't ship light mode today but the convention should be forward-compatible).

**Alternatives:**
- **A — Use a darker green (e.g. `#1b5e20`):** Higher contrast but reads as "darker", less vibrant, breaks the visual rhythm.
- **B — Use a colored border + neutral text:** Cleaner contrast but loses the "tinted" look.
- **C — Bold the text:** One line of CSS, no color change, no rhythm break.

**Reason for C:** Least change, matches the project precedent (active nav link uses 600 weight). If a future accessibility audit flags it, switch to option A.

---

## 7 · Implementation hand-off (developer-facing)

This section is a one-page hand-off summary; the full spec is above.

### 7.1 Files to create

```
frontend/src/app/pages/snapshots/snapshots.component.ts   (new — page host)
frontend/src/app/pages/snapshots/snapshots.component.html (new — page template)
frontend/src/app/pages/snapshots/snapshots.component.scss (new — page styles)
frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.ts
frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.html
frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.scss
frontend/src/app/services/snapshot.service.ts             (new — HTTP client)
frontend/src/app/models/snapshot.model.ts                 (new — type defs)
```

### 7.2 Files to modify

```
frontend/src/app/app.routes.ts                            (add /snapshots route)
frontend/src/app/app.ts                                   (add Snapshots menu item to settingsMenuItems)
frontend/src/app/pages/settings/settings.component.html   (REMOVE two snapshot sections)
frontend/src/app/pages/settings/settings.component.ts     (REMOVE snapshot signal trio, can also remove getSnapshotUsageMetrics import if not used elsewhere)
```

### 7.3 New BE endpoint (developer to design + build)

```
GET /api/snapshots
  Query params:
    project_id   string   (optional — exact match)
    agent_id     string   (optional — exact match)
    status       string   (optional, repeat for multi — server-side: WHERE status IN (...))
    tags         string   (optional, repeat for multi — server-side: tag_mode='all' default)
    age          string   (optional, one of: 24h, 7d, 30d, 90d, all; default all)
    sort         string   (optional, one of: created_desc, created_asc, title_asc, warm_desc; default created_desc)
    limit        int      (default 25, max 50)
    offset       int      (default 0)
  Response:
    {
      "snapshots": [SnapshotRow, ...],   // current page only
      "total": int                        // total matching rows, drives the paginator
    }
  SnapshotRow shape:
    {
      id: string,
      title: string,
      project_id: string,
      project_name: string,                // joined from projects table
      created_by_agent_id: string,
      target_instance_id: string,
      status: 'active'|'superseded'|'running'|'failed'|'interrupted',
      domain_tags: string[],
      supersedes_snapshot_id: string | null,
      git_sha: string | null,
      git_branch: string | null,
      git_dirty: boolean,
      repo_path: string | null,
      runtime_version: string,
      effective_model: string | null,
      created_at: string,                  // ISO-8601
      warm_spawn_count: number,            // joined from snapshot_usage_counters
    }
```

### 7.4 Status column in list — server join

The list endpoint must LEFT-JOIN the `snapshot_usage_counters` table to compute `warm_spawn_count` per row. SQL sketch:

```sql
SELECT s.*,
       p.name AS project_name,
       COALESCE(c.value, 0) AS warm_spawn_count
FROM snapshots s
LEFT JOIN projects p ON p.project_id = s.project_id
LEFT JOIN snapshot_usage_counters c
       ON c.scope = 'spawn:snapshot:' || s.id
WHERE /* filter predicates from query params */
ORDER BY /* sort */
LIMIT :limit OFFSET :offset;
```

The total count for the paginator: a separate `SELECT COUNT(*) FROM snapshots WHERE ...` (with the same WHERE predicates) OR a window-function `COUNT(*) OVER ()` in the same query. The window-function form is preferred (one round-trip).

### 7.5 Page removal of the two Settings blocks

When removing the two snapshot sections from `settings.component.html`:
- Remove the `<section class="setting-section editor-section">` block whose `<h2>` is "Agent Snapshots" (lines ~242-321 in current main checkout).
- Remove the `@if (snapshotMetrics()) { <section>...</section> }` block whose `<h2>` is "Snapshot Usage Metrics" (lines ~323-359).
- The corresponding signal trio (`snapshotCreateEnabled`, `savedSnapshotCreateEnabled`, `savingSnapshotCreate`, `snapshotCreateDirty`, `snapshotMetrics`, `snapshotMetricsCaptureEntries`, `snapshotMetricsSpawnEntries`, `saveSnapshotCreateEnabled`, `onSnapshotCreateSelectionChange`, `loadSnapshotCreateEnabled`, `loadSnapshotMetrics` — and the `OnInit` loaders for them) can be removed from `settings.component.ts` to keep the file lean. The `getSnapshotCreateEnabled` / `setSnapshotCreateEnabled` / `getSnapshotUsageMetrics` methods on `SettingsService` are NOT removed — the new page uses them.

### 7.6 Test pack (for the implementer + tester)

| Pack | What it asserts |
|---|---|
| `e2e_snapshots_page_route` | `/snapshots` resolves; document.title is "Snapshots". |
| `e2e_snapshots_gear_menu` | Gear menu contains "Snapshots" item with `bookmarks` icon and `/snapshots` route. |
| `e2e_snapshots_settings_clean` | `/settings` does NOT contain "Agent Snapshots" or "Snapshot Usage Metrics" sections. |
| `e2e_snapshots_header_toggle` | Header renders radiogroup; Apply button enable/disable on dirty; PUT round-trip. |
| `e2e_snapshots_filter_bar` | 6 filter controls render; clear-all-filters resets; active-filter-count badge shows. |
| `e2e_snapshots_list_table` | 8 columns render in order; paginator [10,25,50]; status chip colors per §3.1; tag overflow +N chip; row click opens drawer. |
| `e2e_snapshots_drawer` | Drawer opens on row click; 8 sections present; copy-id works; supersedes link navigates; close via × and Escape. |
| `e2e_snapshots_states` | Loading skeleton, error retry, empty (educational), filtered-empty (clear-filters CTA). |
| `a11y_snapshots_keyboard` | Tab through header → filters → table rows → drawer; Enter opens drawer; focus visible. |
| `a11y_snapshots_contrast` | Each of 5 status chip variants has ≥ 4.5:1 contrast (effective, with weight bump). |
| `static_snapshots_no_new_deps` | `package.json` diff is zero. |
| `static_snapshots_no_new_tokens` | No new CSS color variables beyond the 5 status colors. |

---

## 8 · Self-review (5 passes, before pinning)

- [x] **Components covered** — every section in the brief (header, metrics, filter bar, table, drawer, states) has a spec section. Service tree covers HTTP. Tradeoffs covers the OD lane and the new endpoint.
- [x] **AC testable** — every AC has a `Validation:` block (12 e2e, 8 static, 2 a11y, 1 perf). Each AC names an observable artifact.
- [x] **Tokens named** — all 5 status colors are explicit hex + alpha pair. All spacing values reference the existing 8-pt grid. Typography uses Material defaults. No bare pixels.
- [x] **Wireframe present** — ASCII for header, full-page skeleton, metrics strip, filter bar (populated), table (8-column populated), drawer (8-section).
- [x] **A11y + tradeoffs captured** — 6 tradeoffs (OD lane, endpoint, warm-count join, per-agent gate, sort placement, drawer host, contrast), all with alternatives + decision + reason.

---

## 9 · Freeze

**Status: `approved`** (effective at SHA freeze: `2ca69147b51383e452ba9d4185cb43b573ee5575`).

The spec is FROZEN at the moment the `pinned_spec_sha` is filled in. From that point, this file is immutable — future changes ride a new spec (new SHA) or an amendment file (`design-spec-amendment-<reason>.md`).

**Approval ceremony (executed):**

1. ✅ `git add` the spec file → commit on `feature/snapshot-uiux` → SHA `2ca69147`.
2. ✅ `pinned_spec_sha` recorded in this file's front-matter and §9.
3. ✅ Hand-authored `mockups/snapshots-page.html` written and added in a follow-up commit (this commit).
4. → Surface the artifacts to the leader with the 3-5 bullet walkthrough in the report.
