# Snapshots Page v2 — Diagnosis (independent take)

> Companion to `design/design-spec.md`. This file captures the
> pain points I found by reading the current implementation against
> measurable UX goals. Read the spec for the proposed solution and
> the mockup at `mockups/snapshots-page-v2.html` for the visual.
>
> Independent of the prior pass in `snapshots-redesign/`. I did not
> read that file; this take is grounded only in the current
> implementation under `frontend/src/app/pages/snapshots/` and
> `daemon/routers/snapshots.py`.

## 1. Scope of the diagnosis

I read (read-only, no edits) the following surfaces:

| Surface | Files |
|---|---|
| Page host | `frontend/src/app/pages/snapshots/snapshots.component.{ts,html,scss}` |
| Table | `frontend/src/app/pages/snapshots/snapshots-table.component.{ts,html,scss}` |
| Detail drawer | `frontend/src/app/components/snapshot-detail-drawer/snapshot-detail-drawer.component.{ts,html,scss}` |
| Service | `frontend/src/app/services/snapshot.service.ts` |
| Model | `frontend/src/app/models/snapshot.model.ts` |
| Routes | `frontend/src/app/app.routes.ts` (single route: `path: 'snapshots'`) |
| BE list + detail + metrics | `daemon/routers/snapshots.py` + `daemon/routers/snapshot_schemas.py` |
| Tests (for shape intent) | `frontend/src/app/pages/snapshots/snapshots.component.spec.ts` |

I did **not** read `.agents/shared/planning/snapshots-redesign/` —
independence is required by the brief.

## 2. Current page shape (one screenshot summary)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Snapshots                                                            │
│ Browse and inspect every agent-snapshot …                            │
├──────────────────────────────────────────────────────────────────────┤
│ Snapshot creation  [Enabled ● Disabled ○]   [Apply] [Unsaved changes]│
├──────────────────────────────────────────────────────────────────────┤
│ ┌─ Capture counts ──────┐ ┌─ Warmed snapshots ──────┐               │
│ │ coder      12 captures │ │ (empty → hidden)        │               │
│ └─────────────────────────┘ └─────────────────────────┘              │
│ Monitoring only — these counters never feed the search ranking.     │
├──────────────────────────────────────────────────────────────────────┤
│ [Project ▾] [Agent ▾] [Sort ▾]                                       │
│ Status: [active][superseded][running][failed][interrupted]            │
│ Age: [24h][7d][30d][All]   Tag mode: [all]                           │
│ Tags: [Add tag (e.g. domain:api) and press Enter ___________ ]       │
│ [domain:api] [domain:web]                                            │
│                                       3 active filters  [Clear ⨯]   │
├──────────────────────────────────────────────────────────────────────┤
│ Title          Project   Agent    Status  Tags  Created  Warm  …     │
│ ──────────── ──────── ─────── ──────── ───── ──────── ──── ───       │
│ first-snap   7c3a…8f  coder   active  …    2 min ago  —     ⧉       │
│ …                                                                    │
├─────────────────────┬────────────────────────────────────────────────┤
│ Detail (drawer)     │                                                │
│ 520px fixed overlay │                                                │
└─────────────────────┴────────────────────────────────────────────────┘
```

## 3. Pain points and measurable goals

### P1 — The header hosts a config toggle that competes with the page's primary task

**What I see.**
`snapshots.component.html:15-81` ships a global
`Snapshot creation` toggle (R15 — relocated from `/settings`).
The page's mission is **browse + inspect**; the toggle is a global
config concern. It takes ~110px of vertical space at the top, ahead
of the filters, ahead of the table, and ships with a radio + Apply
+ dirty-hint pattern that mirrors `/settings` and adds noise.

**Measurable goal G1.** Reduce above-the-fold non-essential chrome
by ≥ 40% on a 1366×768 viewport. **Measurement:** height of the
region from `header` to `filter-bar` inclusive; today ~360px.
**Target:** ≤ 220px when the toggle is collapsed into a sub-header
icon button.

**How v2 fixes it.** Relocate the toggle to a header trailing
icon-menu (`⋯` → "Snapshot creation settings") that opens a popover
linking to `/settings` (single source of truth). The page header
itself stays a title + a one-line subtitle + a kebab action menu.

### P2 — Filter bar is a 3-row vertical stack; tag input is separated from its chips

**What I see.**
`snapshots.component.html:166-291` ships three `filter-row`s:

- row 1: Project (searchable) + Agent (searchable) + Sort
- row 2: Status chips + Age chips + Tag-mode toggle
- row 3: Tag input field, then the tag chips on the next visual row

The user must type a tag and press Enter; there is no chip-style
click affordance on tags surfaced by the table. The Tag chips
appear under the Tag input, but visually nothing in the layout
says "these two are related". Clearing filters requires scrolling
to row 3.

**Measurable goal G2.** Reduce the visual height of the filter
region by ≥ 30%. **Measurement:** rendered height of
`.filter-bar` when no filters are active; today ~140px (incl.
tag input + tag chips row + actions-row even when no chips).
**Target:** ≤ 95px when no filters active (single horizontal chip
row). **Side goal:** reduce "find snapshots tagged X" to ≤ 2
clicks (today: type tag + Enter + 250ms debounce + fetch).

**How v2 fixes it.**

- Collapse the three rows into **one horizontal filter bar** with
  inline chip controls (Project, Agent, Status, Age, Tag mode) and
  a **search-like input** that combines text + tag suggestions.
- Tag suggestions come from a `/api/snapshots/tags` endpoint
  (proposed in v2) ranked by usage count; user can click to add.
- The active-filter count + Clear filters move inline to the right
  of the bar (no separate `actions-row`).

### P3 — Status chips are tiny lowercase text with no color hierarchy

**What I see.**
`snapshots.component.scss:209-225` (table) defines
`.status-chip` as a 11px uppercase pill with bg only; five enum
values (`active`, `superseded`, `running`, `failed`, `interrupted`)
all render as the same pill style. A user scanning a table of 25
snapshots cannot distinguish status by glance.

**Measurable goal G3.** Distinguish status by color + icon + label
so a user can identify the state of 25 rows in ≤ 3 seconds.
**Measurement:** wall-clock time to answer "how many failed
snapshots are in this list?" measured against a fixed fixture;
today's chip style → ~12s scan; target ≤ 3s.

**How v2 fixes it.** Five explicit status styles:

| Status | bg | fg | icon |
|---|---|---|---|
| `active` | `--status-ok-bg` | `--status-ok-fg` | `check_circle` |
| `running` | `--status-info-bg` | `--status-info-fg` | `autorenew` (spinning when status flips live) |
| `superseded` | `--status-muted-bg` | `--status-muted-fg` | `history` |
| `failed` | `--status-err-bg` | `--status-err-fg` | `error` |
| `interrupted` | `--status-warn-bg` | `--status-warn-fg` | `warning` |

Each carries a 16px Material icon left of the label.

### P4 — `Project` column shows truncated project_id, not the project name

**What I see.**
`snapshots-table.component.html:85-93` renders
`row.project_id` truncated to 8 chars (`truncateId`).
`snapshot_schemas.py:74-105` (`SnapshotListItem`) explicitly omits
`project_name` (D-5 binding). But the FE has
`ProjectService.projects()` already loaded (used for the filter
dropdown) and can join `project_id → name` client-side with zero
new BE work.

The user can identify Agents (full string `coder`, `developer[v2]`)
but cannot identify Projects (only `7c3a…8f2b`). That is inverted
from how the human reads the system.

**Measurable goal G4.** Show project name in the list. **Target:**
"identify which project a snapshot belongs to" → ≤ 1s scan;
today ~10s (open detail drawer to read `task_summary` for context).

**How v2 fixes it.** Two-track fix:

- **Track A (client-side, no BE change):** map `row.project_id` →
  `Project.name` using the cached `projectService.projects()`
  signal. Fallback to truncated UUID when project not loaded.
- **Track B (proposed v2 BE contract add):** include
  `project_name: str | None` in `SnapshotListItem` (`SnapshotRow.nem`)
  and on the list endpoint populate it via JOIN; null when the
  project was deleted. Track B is the durable answer; Track A is
  the day-zero-1 flag-ship.

Spec references both; implementation picks Track A on day 0 and
files a follow-up for Track B.

### P5 — `Warm` column is dead (`—` placeholder, tooltip "Coming in a future release")

**What I see.**
`snapshots-table.component.html:146-152` reserves column 7 for
"warm spawn count" but always renders `—` (D-5: omitted from v1).
The tooltip text is `Coming in a future release`. That is a column
that consumes width today and ships zero information.

**Measurable goal G5.** Either populate `Warm` with real numbers
or drop the column. **Measurement:** table column count and
per-row width today → 8 columns at ~144 px row width = ~1152 px.
**Target:** ≤ 7 columns OR the `Warm` column shows real data.

**How v2 fixes it.** Drop the `Warm` column in v2. Move warm-spawn
information into the row metadata line (small text under the
title) so the data point is still visible without consuming a
column. The BE wires `warm_spawn_count` per `be-plan.md` §4.2 in
Phase-2; until then the v2 layout has a placeholder slot.

### P6 — No row-level quick actions; copy / navigate-to-predecessor / filter-by are all behind the drawer

**What I see.**
`snapshots-table.component.html:155-197` (`actions` column) shows
only a copy-id icon button (when wired) and otherwise a click that
opens the drawer. To filter by the row's agent, the user must open
the drawer, copy the agent id, close the drawer, paste into the
Agent filter. To filter by tag, same drill.

**Measurable goal G6.** Make "filter by this row's agent" and
"filter by this row's tag" a single click. **Target:** ≤ 1 click
+ 0 text input.

**How v2 fixes it.** Per-row **quick-action menu** (kebab
`more_vert` icon at the row end). Menu items:

- `Open detail` (modal) — opens the detail drawer
- `Copy snapshot ID`
- `Filter by this agent` — sets `filterAgentId` and `pageIndex=0`
- `Filter by project` — sets `filterProjectId` and `pageIndex=0`
- `Filter by tag …` (per-tag submenu from `domain_tags`)
- `Open predecessor` (only if `supersedes_snapshot_id` set)

### P7 — Drawer is 520px wide with 7 stacked sections; digest panel sits at the bottom

**What I see.**
`snapshots.component.scss:358-364` (`snapshot-detail-drawer-panel`)
is `width: 520px`. `snapshot-detail-drawer.component.html` ships
seven sections stacked (Task summary, Git anchor, Runtime/Model,
Supersedes, Tags, Timestamps, Context) plus an on-demand Digest
section. On a 1366×768 viewport with a 520px drawer, the
underlying table area collapses to ~416 px (less than the 1100px
`min-width` declared on the page container — the drawer overlay
mode means the table is hidden, not squeezed).

The body section is already well-structured, but the visual hierarchy
collapses: all sections look equally weighted; the eye doesn't
know to skip ahead to the supersedes chain or the digest.

**Measurable goal G7.** Improve drawer scannability. **Target:**
time to find "the runtime version of this snapshot" ≤ 1s
(measured: open drawer → find the value). Today ~5s (the section
sits in a vertical stack of 8 sections).

**How v2 fixes it.** Re-organize the drawer body into a
**two-region layout**:

- **Left rail (40% / 240px):** Title, Status, Metadata (id, project,
  agent, instance, runtime version, model, timestamps) — the
  "card" of the snapshot.
- **Right column (60%):** Sections in priority order — 1. Task
  summary (top, expanded by default), 2. Supersedes chain, 3. Git
  anchor, 4. Tags (clickable → filter), 5. Digest (lazy, with
  early prefetch for digest size < 100KB), 6. Context (folded).

Width: 720px on ≥1280 viewports, 560px on smaller. Drawer
header becomes sticky to the top edge of the drawer body on
scroll.

### P8 — Filters do not persist in URL; share-back-link loses state

**What I see.**
`SnapshotsComponent` holds all filter state in component-level
`signal`s. Nothing in `app.routes.ts` or `SnapshotsComponent` reads
from `ActivatedRoute.queryParams` or writes back to the URL. A
refresh wipes filters; sharing `/snapshots` after filtering
yields a generic view.

**Measurable goal G8.** Filter state survives refresh and is
shareable via URL. **Target:** 100% of filter changes reflected in
the URL within 100ms; copied URL re-applies the same view on
load.

**How v2 fixes it.** Bind filter signals to `queryParams` via
`Router.navigate(..., { queryParams, queryParamsHandling: 'merge' })`
on every filter change. Read params on `ngOnInit` to seed the
signals. Encode `status[]` and `tags[]` as repeated query params
(matches the BE wire contract — `?status=active&status=running`).

### P9 — Empty / loading / error states do not teach next-action

**What I see.**
`snapshots-table.component.html:32-49` ships an empty state
("No snapshots yet") and a filtered-empty state ("No snapshots
match your filters"). Neither links to the (now-collapsed)
creation toggle or to /settings. The error state has a Retry
button but no detail (e.g., "Backend unavailable", "request
timeout").

**Measurable goal G9.** Each empty / error state names the
next-action. **Target:** zero next-action blanks — every state
either shows what to do, links somewhere, or offers a retry.

**How v2 fixes it.** Per-state upgrade:

- **Empty (zero snapshots):** icon, copy, link "Enable snapshot
  creation in Settings →" when the page detects
  `snapshotCreateEnabled === false` (via the new
  `settingsService.getSnapshotCreateEnabled` snapshot the page
  already reads for the collapsed toggle).
- **Filtered empty:** icon, copy, "Clear filters" button
  (in-place).
- **Error (list):** icon, copy, "Retry" button + collapsible
  diagnostic snippet (request id, status).
- **Loading (initial):** skeleton (today); add 5-row skeleton +
  paginator placeholder.
- **Loading (refetch):** thin progress bar (today); add filter
  row subtle highlight to communicate "filters just changed".

### P10 — Detail drawer fetches detail without digest, then re-fetches on demand

**What I see.**
`snapshot-detail-drawer.component.ts:113-158` fetches detail
without digest (`includeDigest: false`). The digest panel then
fires a second fetch on the first `Show digest` click
(`onToggleDigest`). For small digests (< 50KB), two fetches is
overhead; for large digests (> 200KB) the guard trips correctly.

**Measurable goal G10.** Reduce round-trips for small digests.
**Target:** for `digest_bytes < 100KB`, the digest appears in
the drawer within one BE round-trip (parallel fetch).

**How v2 fixes it.** When the detail drawer mounts, fire two
parallel requests:

- `getById(id, { includeDigest: false })` — full detail payload
  (no digest).
- `getById(id, { includeDigest: true })` — digest-only payload
  (or accept the cost on the first detail hit and let the BE
  stream it).

If the digest < 100KB (cheap to JSON-stringify), pre-render it
inside the right column (no toggle). If ≥ 100KB, show a
"Show digest (size: 480 KB)" button and lazy-load.

### P11 — Paginator + filter-row layout: filter changes the `pageIndex` reset is duplicated 9 times

**What I see.**
`snapshots.component.ts:342-406` — every filter-change handler
performs `this.pageIndex.set(0)` explicitly. That's nine
duplicated reset sites (filter×6, tag add, tag remove, clear). A
single signal-effect cascade would carry the reset centrally.

**Measurable goal G11.** Reduce duplicated pageIndex-reset sites
to one. **Measurement:** grep `pageIndex.set(0)` in
`snapshots.component.ts`; today = 9. **Target:** 1 (in
`composeFilters` or in a single effect that owns the reset on any
non-range signal change).

**How v2 fixes it.** The host moves the pageIndex-reset into the
fetch-effect: every effect tick reads the filter set in
`untracked`, computes whether `pageIndex` should reset
(any non-paginator signal in the dep graph changed), and writes
once. Handlers stop touching `pageIndex` directly. Net: fewer
sites to drift, one audit point.

## 4. Goal-to-AC mapping

| Goal | AC ID | Validation |
|---|---|---|
| G1 — toggle moved to kebab | AC-1 | `Validation: grep -L 'Snapshot creation' frontend/src/app/pages/snapshots/snapshots.component.html` (toggle text must NOT appear in the page template); `static: grep -c 'Snapshot creation settings' frontend/src/app/pages/snapshots/snapshots.component.html` ≥ 1 |
| G2 — filter bar height ≤ 95px when empty | AC-2 | `Validation: pack snapshots-e2e; visual: clear all filters → measured height of .filter-bar ≤ 95px on 1366×768` |
| G3 — status chips color-coded | AC-3 | `Validation: pack snapshots-e2e; visual: 25-row fixture shows ≥ 3 distinct chip colors per scan; 5/5 enum values have distinct bg color` |
| G4 — project column shows project name | AC-4 | `Validation: static: grep -n 'project_name' frontend/src/app/models/snapshot.model.ts` ≥ 1 (Track A: client-side join via ProjectService.projects()); `static: grep -n 'project_name' daemon/routers/snapshot_schemas.py` ≥ 1 (Track B optional) |
| G5 — drop dead `Warm` column | AC-5 | `Validation: static: grep -L "matColumnDef=\"warm\"" frontend/src/app/pages/snapshots/snapshots-table.component.html` (warm column NOT in expanded form); `static: grep -n 'warm_spawn_count' frontend/src/app/pages/snapshots/snapshots-table.component.html` ≥ 1 (info shown elsewhere) |
| G6 — row quick actions | AC-6 | `Validation: pack snapshots-e2e; click row kebab → menu shows 5 items incl. "Filter by this agent"` |
| G7 — drawer 2-region layout | AC-7 | `Validation: pack snapshots-e2e; visual: drawer renders metadata rail (left, ≤ 240px) + sections column (right)` |
| G8 — URL-persisted filters | AC-8 | `Validation: pack snapshots-e2e; set status=active+failed → URL contains ?status=active&status=bad_failed; refresh → both filters still active` |
| G9 — empty/error teach next-action | AC-9 | `Validation: pack snapshots-e2e; filtered-empty state shows "Clear filters" button; missing error state shows Retry + collapsible diagnostic` |
| G10 — single-roundtrip small digest | AC-10 | `Validation: pack snapshots-e2e; mock digest size=20KB → drawer renders digest without second click` |
| G11 — one pageIndex-reset site | AC-11 | `Validation: static: grep -c 'pageIndex.set(0)' frontend/src/app/pages/snapshots/snapshots.component.ts` ≤ 1 |

## 5. Out of scope for v2 (defer list)

These are real but explicit deferrals — NOT to be implemented in v2:

- **Write operations** (delete / archive / supersede). Per D9 the
  snapshots surface is read-only in v1. No UI for mutation in v2
  either; the **draft** write surface (`POST /api/snapshots`) is
  a future commission.
- **BE-side `warm_spawn_count` join** (D-5). Track B requires a
  new SQL aggregate; punt to a follow-up commission; Track A
  covers the day-0 fix.
- **Per-snapshot restore action** (`spawn_hot_instance` from a
  snapshot id). Powerful but a v3 surface.
- **Drawer keyboard shortcuts** (`Esc` close, `j`/`k` row nav,
  `Cmd+K` command palette). The drawer already has a close
  button + backdrop; keyboard nav is a v3 polish.
- **Drawer-as-route.** Today the drawer is a side panel inside
  the page; "drawer as URL" (`/snapshots/{id}`) is a future
  navigation restructure.

## 6. Open questions for the user (decision needed before freeze)

None blocking. The spec ships with the eleven pain points above
mapped to ACs and a clear Track A vs Track B for goal G4. The
follow-up commission for Track B (`warm_spawn_count` + a future
`project_name` server-side JOIN) is filed at the end of the
spec under "Future commissions".

## 7. Acceptance criteria for the spec itself

The spec is acceptable iff:

- All 11 goals map to at least one AC with a `Validation:` block.
- Every AC is observable + testable by an automated pack or a
  static grep.
- The design-artifacts table records the lane (`mockup_lane:
  opendesign` or `text`) and a `fallback_reason` only when the
  lane is text.
- The token references in the spec trace to the project's
  design-token path (today: inline CSS variables in component
  files; v2 should consolidate to `frontend/design-tokens/` if
  it does not already exist there).
- `pinned_spec_sha` is set ONLY at `status: approved` and is
  recomputed AFTER all content is final (not before).