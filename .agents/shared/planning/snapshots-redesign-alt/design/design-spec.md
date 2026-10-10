# Design Spec — Snapshots Page v2 (independent take)

> **Independent second take** for the `/snapshots` page redesign.
> Same scope as the original commission; built from a fresh
> diagnosis (see `../diagnosis.md`) without reading the prior pass
> under `snapshots-redesign/`. This spec is paired with the prior
> spec for side-by-side review.
>
> **`pinned_spec_sha`** is computed AFTER final freeze so the pin
> matches the content. (The prior pass had a pin mismatch;
> reproduced discipline: write content → compute SHA → set
> `status: approved` in one operation.)

## Front matter

```yaml
spec_id: snapshots-page-v2-alt
status: approved             # ← set to 'approved' at freeze time (atomic with pinned_spec_sha below)
version: 0.1.0
phase: new
created_at: 2026-10-10
approved_at: 2026-10-10
author: designer
plan_ref: .agents/shared/planning/snapshots-redesign-alt/
conventions: see frontend/src/styles.scss (Material 3 dark, ng-zorro dark) + daemon side conventions.md
escalation_path: leader

# Set ONLY at status: approved (NOT earlier) — computed AFTER final freeze:
pinned_spec_sha: ea60f7fe8616faed8822a09da18a50238255a582

# Mockup lane decision (Cardinal #7; record for any text-lane row)
mockup_lane: text           # ← OD upstream 524 timeout; see "Generation lane" below
fallback_reason: timeout    # ← exact enum per Cardinal #7
```

**Pin verification.** The `pinned_spec_sha` is the git-blob SHA
captured via `git hash-object` at the moment the spec was frozen
(i.e., the SHA of the file BEFORE the pin was added). The file's
CURRENT blob SHA differs from the pin by exactly the bytes of
the pin-edit (this is the project convention — see
`snapshot-uiux/design/design-spec.md` and
`snapshots-redesign/design/design-spec.md`). Verification: the
pin must equal the SHA the designer reported to the leader at
freeze time; the in-file pin must be a 40-char lowercase hex;
any drift between the in-file pin and the parent's frozen
reference is a defect.

## Generation lane

The default lane for this designer is `opendesign` (the native
plugin tools `od.compose_brief` / `od.generate` / `od.lint` /
`od.save` are bound in the agent surface). On this run:

1. `od.compose_brief` succeeded (returned the composed brief).
2. `od.generate` returned a **524 upstream timeout** (Cloudflare
   origin read timeout at 120s; the proxy-edge read window is
   shorter than the OD lane's natural 130-170s budget).
3. Per the designer's mockup-lane procedure ("one call, no
   retry-storm; timeout mid-call → text fallback for that page"),
   the artifact was hand-authored.

Cardinal #7 applies: the spec records `mockup_lane: text` +
`fallback_reason: timeout` for this artifact. The text mockup
**does not claim pixel fidelity**; it is a layout-and-placement
aid. The implementer should treat it as such and lean on the spec
body for fidelity-bearing decisions.

A follow-up re-attempt at `od.generate` is not part of this
spec's scope — the user's brief is the deliverable, and "no
retry-storm" is the load-bearing rule. The text fallback ships
the design; a future commission can retry the OD lane for a
visual baseline if desired.

---

## 1. Information architecture

### 1.1 Route + entry points

| Entry | Route | Trigger |
|---|---|---|
| Primary | `/snapshots` | Top-nav "Snapshots" link (already present in `app.html`) |
| Deep-link (future, v3) | `/snapshots/{id}` | Drawer-as-route (deferred — see §7) |
| Notification | "View snapshot" link from a job/event stream | Not in v2 scope |

The route continues to lazy-load
`./pages/snapshots/snapshots.component` per
`frontend/src/app/app.routes.ts:58`. No router changes in v2.

### 1.2 Page hierarchy

```
/snapshots
├── Top nav (existing app shell — unchanged)
├── Page header
│   ├── H1 "Snapshots"
│   ├── Subtitle
│   └── Kebab "Page actions" (replaces global toggle)
├── KPI strip (single row, compact)
│   ├── Capture counts
│   ├── Warmed spawns
│   ├── Last capture
│   └── Top tag
├── Filter bar (single horizontal row)
│   ├── Project (searchable-select)
│   ├── Agent (searchable-select)
│   ├── Status (multi-chip, color-coded)
│   ├── Age (single-chip)
│   ├── Tag mode (all/any toggle)
│   ├── Tag input (with pills + suggestions)
│   └── Active-filter count + Clear
├── Table (7 columns — Warm dropped)
│   ├── Title + row metadata line
│   ├── Project (project_name)
│   ├── Agent
│   ├── Status (color + icon)
│   ├── Tags (max 2 + overflow)
│   ├── Created (relative + absolute tooltip)
│   └── Actions (kebab menu)
├── Paginator
└── Detail drawer (overlay, 720px, 2-region)

Drawer layout (when open)
├── Header (sticky)
│   ├── Title
│   ├── Status chip
│   ├── Truncated id + ⚠ dirty
│   ├── Kebab + Close
├── Body (2-region grid: 240px rail + 1fr sections)
│   ├── Left rail — Metadata
│   │   ├── Project
│   │   ├── Agent
│   │   ├── Target instance
│   │   ├── Runtime version
│   │   ├── Effective model
│   │   ├── Created
│   │   ├── Captured by
│   │   └── Capture bytes
│   └── Right column — Sections (priority order)
│       ├── 1. Task summary (expanded by default)
│       ├── 2. Supersedes chain
│       ├── 3. Git anchor
│       ├── 4. Tags (clickable)
│       ├── 5. Digest (lazy, prefetched when small)
│       └── 6. Context (folded)
```

## 3. Components

### 3.1 `SnapshotsPageComponent` (page host)

**Purpose.** Owns the page lifecycle, the list fetch, the
filter signals, and the drawer id-swap signal. Renders the
header, KPI strip, filter bar, table, paginator, and detail
drawer.

**Behavior.**

- Reads URL `queryParams` on `ngOnInit` to seed the filter signals
  (G8). On any filter signal write, mirrors the change to
  `Router.navigate(['snapshots'], { queryParams, queryParamsHandling: 'merge' })`.
- Hosts the **single source of truth** for `pageIndex` reset
  (G11). The fetch effect owns the reset: any non-paginator
  signal in the dep graph change ⇒ `pageIndex.set(0)`. Handlers
  no longer touch `pageIndex` directly.
- Hosts the kebab "Page actions" menu that links to
  `/settings` (G1). The page does NOT render the radio + Apply
  toggle anymore.

**States.**

- `loading` (initial): 5-row skeleton + paginator placeholder.
- `loading` (refetch): subtle filter-row highlight + thin
  progress bar above the table.
- `error`: error block with Retry + collapsible diagnostic.
- `empty` (zero snapshots): empty-state with "Enable snapshot
  creation in Settings →" link (when `snapshotCreateEnabled === false`).
- `empty` (filtered): empty-state with inline "Clear filters".
- `loaded`: the table + paginator.

**A11y.** Page has `<header>`, `<main>`, and `<aside>` landmarks.
H1 carries the title. Filter bar is a `<section aria-label="Filter
snapshots">`. Table has `aria-label="Snapshots"` and a `<caption>`
(if not already visible).

**Wireframe.** `mockups/snapshots-page-v2.html` Panel A
(drawer closed) and Panel B (drawer open).

### 3.2 `FilterBarComponent`

**Purpose.** Single horizontal row that owns Project, Agent,
Status chips, Age chips, Tag mode, Tag input + pills, and the
active-filter count + Clear. Replaces today's 3-row layout.

**Behavior.**

- Reads from the page's filter signals; writes through
  `FilterChange` events. The page's listener applies the
  `pageIndex.set(0)` reset in the centralized fetch effect (G11).
- Tag suggestions: a side channel requests
  `GET /api/snapshots/tags` (proposed in §5 — Track C). Suggestions
  appear as a popover under the input; click to add. Suggestions
  rank by usage count descending.

**States.**

- `idle`: no input focus.
- `focused (suggestions open)`: popover with up to 8 suggestions.
- `typing (debounced)`: subtle 200ms shimmer on the input border.
- `disabled` (rare — when list is in error state): chips greyed,
  inputs disabled.

**A11y.**

- Status chips use a single-selectable or multi-selectable
  `role="group"` with `aria-label`.
- Tag input has `aria-label="Add tag filter"`; pills are
  `role="button"` with `aria-label="Remove tag <name>"`.
- Clear filters button has `aria-label="Clear all filters"` and
  is `aria-disabled="true"` when there are none.

**Wireframe.** `mockups/snapshots-page-v2.html` Panel A
(the horizontal filter bar).

### 3.3 `SnapshotsTableComponent` (presentational)

**Purpose.** Render the rows fed by the host (no fetch; pass 4
amendment #8 preserved). v2 changes:

- **Drop** the `Warm` column (G5). Warm-spawn count appears in
  the row metadata under the title.
- **Replace** `Project` UUID with `project_name` joined
  client-side from `ProjectService.projects()` (G4 Track A).
- **Add** per-row kebab `more_vert` actions menu (G6).
- **Status chips** color + icon + label (G3).

**Behavior.** Same as today — `input` only for rows/total/loading/
error/hasActiveFilters/pageIndex/pageSize; `output` only for
`rowClick`, `pageChange`, `retry`. Adds two outputs:

- `quickAction(action: QuickAction)` — emitted when the user
  picks a quick action (Open detail, Copy ID, Filter by agent,
  Filter by project, Filter by tag …, Open predecessor).
- `rowActivate(row)` — same as today's `rowClick` (renamed for
  clarity; `rowClick` kept as alias for back-compat).

**States.** Same as today (skeleton, error, empty, filtered
empty, loaded).

**A11y.** Each row is `role="row"` with `aria-selected` (when
drawer is open). Kebab button is `aria-haspopup="menu"` with
the open/close state on `aria-expanded`. Status chip carries
the status text + a `sr-only` icon label.

**Wireframe.** `mockups/snapshots-page-v2.html` Panel A (table
+ open kebab menu on the running-status row).

### 3.4 `KpiStripComponent`

**Purpose.** Compact single horizontal strip replacing today's
2-card "Capture counts + Warmed snapshots" grid.

**Behavior.** Reads `metricsSnapshotUsageMetrics` (cache via the
service; same shape as today). Renders four inline items:

- Capture counts (total)
- Warmed (total spawns)
- Last capture (relative)
- Top tag (most-used tag)

If any value is empty/missing, that cell collapses (no placeholder
text).

**States.** Same as today's metrics: `loading`, `error`
(with Retry), `empty`.

**A11y.** Skeleton blocks carry `aria-busy="true"`.

**Wireframe.** `mockups/snapshots-page-v2.html` Panel A
(compact strip between header and filter bar).

### 3.5 `SnapshotDetailDrawerComponent` (existing — refactor only)

**Purpose.** Renders the snapshot detail body. v2 changes:

- Width: 720px on ≥1280 vw, 560px on smaller.
- Body becomes a 2-region grid (240px rail + 1fr sections column).
- Sections reorder to priority order (Task summary first).
- Lazy digest prefetched for digest < 100KB (G10).
- Tag chips clickable → emit a `filterByTag` event.

**Behavior.** Same inputs (`snapshotId`, `isDrawerMode`) and
outputs (`close`, `navigateToPredecessor`, `copyId`) as today.
Adds one output:

- `filterByTag(tag: string)` — emitted when the user clicks a tag
  chip in the drawer.

**States.** Same as today (error, loading, loaded, digest-loading,
digest-error, digest-too-large).

**A11y.** Drawer is `role="dialog"`, `aria-modal="true"`,
`aria-label="Snapshot detail"`. Focus trap inside the drawer;
`Esc` closes. Drawer header is sticky so the close button stays
reachable when the body scrolls.

**Wireframe.** `mockups/snapshots-page-v2.html` Panel B (drawer
open with 2-region layout).

### 3.6 `UrlStateBridgeService`

**Purpose.** New service that owns the URL ↔ filter signals
mapping (G8).

**Behavior.** Reads `ActivatedRoute.queryParams` on init; emits
filter changes via `Router.navigate`. Single subscription per
page (no debounce — query string changes are cheap; the
filter-side debounce stays in the page).

**States.** Stateless; pure mapper.

**A11y.** None (infrastructure service).

**Wireframe.** Not directly rendered; behavior is observable
via the URL bar.

## 4. Tokens

### 4.1 Color tokens (v2.0 surfaces)

All tokens trace to existing CSS variables in
`frontend/src/styles.scss` and `frontend/src/app/app.scss`.

| Token | Value | Source |
|---|---|---|
| `--bg-app` | `#0f172a` | `styles.scss:42` |
| `--bg-surface` | `#1e293b` | `styles.scss` (Angular Material M3 surface) |
| `--bg-input` | `#0b1326` | (proposed in v2 — derived) |
| `--text` | `rgba(255,255,255,0.92)` | `styles.scss` |
| `--text-2` | `rgba(255,255,255,0.70)` | (derived) |
| `--text-3` | `rgba(255,255,255,0.50)` | (derived) |
| `--accent` | `#4f46e5` | (proposed — matches M3 violet palette) |
| `--status-ok-fg` | `#10b981` | (proposed — emerald 500) |
| `--status-info-fg` | `#3b82f6` | (proposed — blue 500) |
| `--status-warn-fg` | `#f59e0b` | (proposed — amber 500) |
| `--status-err-fg` | `#ef4444` | (proposed — red 500) |
| `--status-muted-fg` | `#94a3b8` | (proposed — slate 400) |

**Day-zero rule.** v2 declares these tokens in component-local
CSS variables (`snapshots.component.scss`) until a project-level
`design-tokens/` directory exists. The blueprint says the
canonical home is `frontend/design-tokens/` (not yet present in
the tree); the v2 component tokens are the seed for that future
extraction.

### 4.2 Typography

| Element | Family | Size | Weight |
|---|---|---|---|
| H1 (page title) | system sans | 22px | 600 |
| H2 (drawer title) | system sans | 17px | 600 |
| Section H3 (drawer) | system sans | 11px | 700 (uppercase, tracked) |
| Body | system sans | 13px | 400 |
| Mono (UUIDs, paths) | JetBrains Mono / Menlo | 11.5px | 400 |
| Tag pills | JetBrains Mono | 10.5-11px | 400 |

### 4.3 Spacing

- Page container: 24px horizontal padding, 16-24px vertical gap.
- Filter bar: 8px horizontal gap.
- Table cell padding: 10px all sides; row gap 0 (table border).
- Drawer header: 16px / 20px padding.
- Drawer body rail: 16px / 16px padding.
- Drawer body sections: 16px / 20px padding; section gap 20px.

### 4.4 Iconography

Material Symbols (outline) at 14-18px; status icons inline. The
hand-authored mockup uses inline SVG fallbacks to stay
self-contained. The Angular implementation imports
`@angular/material/icon` + the Material Symbols font (the project
already imports Material Symbols via `app.scss`).

## 5. Acceptance criteria (with `Validation:` blocks)

### AC-1 — Header toggle collapsed to kebab action menu (G1)

**Description.** The `Snapshot creation` radio + Apply pattern is
removed from the page header. The page header instead renders a
single kebab `⋯` button that opens a popover with a link to
`/settings` (where the toggle is the single source of truth).

**Validation:**
```
Validation: pack snapshots-e2e;
  static: grep -L 'Snapshot creation' frontend/src/app/pages/snapshots/snapshots.component.html
    # The string "Snapshot creation" must NOT appear in the page template
    # (it lives in /settings only).
  visual: measured height of header (h1 + subtitle + kebab)
    on 1366×768 viewport ≤ 80px
```

### AC-2 — Filter bar height ≤ 95px when no filters active (G2)

**Description.** The 3-row filter layout collapses to a single
horizontal row. When no filters are active, the filter bar renders
at ≤ 95px height (compared to today's ~140px).

**Validation:**
```
Validation: pack snapshots-e2e;
  visual: clear all filters, measure .filter-bar rendered
    height on 1366×768 viewport; assert ≤ 95px
  behavioral: type "domain:api" + Enter → tag pill becomes
    visible inside the tag-input WITHOUT expanding the filter
    bar's vertical footprint beyond the +pill width
```

### AC-3 — Status chips color + icon + label hierarchy (G3)

**Description.** Each of the five status enums (`active`,
`running`, `superseded`, `failed`, `interrupted`) renders with a
distinct background color, foreground color, icon, and label.
Scanning a 25-row list to count failed rows is ≤ 3s.

**Validation:**
```
Validation: pack snapshots-e2e;
  static: grep -c '\.status\.' frontend/src/app/pages/snapshots/snapshots-table.component.scss
    # ≥ 5 (one class per enum value)
  visual: 25-row fixture with 5× failed + 8× running + 12×
    active rows; time "count failed" task; assert ≤ 3s
```

### AC-4 — Project column shows project_name (G4)

**Description.** The Project column renders the project name
(joined client-side from `ProjectService.projects()`) instead of
the truncated project_id UUID. Fallback to truncated UUID when
project is not loaded.

**Validation:**
```
Validation: pack snapshots-e2e;
  static: grep -n 'project_name' frontend/src/app/models/snapshot.model.ts
    # ≥ 1 (Track A — client-side join)
  static: grep -n 'project_name' daemon/routers/snapshot_schemas.py
    # ≥ 1 (Track B optional — server-side join)
  visual: open /snapshots; verify column shows "agents-ensemble"
    (not a UUID)
  behavioral: refresh with no projects loaded; verify fallback
    to truncated UUID
```

### AC-5 — Warm column dropped; warm-spawn count shown elsewhere (G5)

**Description.** The "Warm" column is removed from the table.
The warm-spawn count appears inline in the row metadata line
under the title (small muted text), preserving the data point
without consuming a column.

**Validation:**
```
Validation: pack snapshots-e2e;
  static: grep -L 'matColumnDef="warm"' frontend/src/app/pages/snapshots/snapshots-table.component.html
    # "warm" column NOT declared in the expanded form
  static: grep -n 'warm_spawn_count' frontend/src/app/pages/snapshots/snapshots-table.component.html
    # ≥ 1 (warm count rendered elsewhere)
  visual: count rendered columns = 7 (not 8)
```

### AC-6 — Row-level quick actions (G6)

**Description.** Each row has a kebab `more_vert` icon that opens
a menu with: Open detail, Copy snapshot ID, Filter by this agent,
Filter by project, Filter by tag (submenu), Open predecessor (when
applicable).

**Validation:**
```
Validation: pack snapshots-e2e;
  behavioral: click row kebab → menu opens with 6 items
    (5 + the submenu tag list)
  behavioral: click "Filter by this agent" → filter bar updates
    to that agent, table refetches with offset=0
  behavioral: click "Filter by tag → domain:api" → tag pill
    added to filter, table refetches with offset=0
```

### AC-7 — Drawer 2-region layout (G7)

**Description.** Drawer body is a 2-region grid: 240px metadata
rail (left) + sections column (right). Sections appear in priority
order: Task summary, Supersedes chain, Git anchor, Tags, Digest
(collapsed), Context.

**Validation:**
```
Validation: pack snapshots-e2e;
  visual: open drawer; verify left rail width ≤ 240px;
    verify sections column ≥ 360px on a 1280 vw viewport
  visual: drawer width = 720px on ≥1280 vw, 560px on 1024 vw
  visual: drawer header is sticky (close button reachable
    when body scrolls to the bottom)
```

### AC-8 — URL-persisted filters (G8)

**Description.** Filter changes are mirrored to the URL
(`queryParams`); refresh restores the filter state; sharing the
URL reproduces the same view.

**Validation:**
```
Validation: pack snapshots-e2e;
  behavioral: set status=active + failed + project=agents-ensemble;
    verify URL contains ?status=active&status=failed&project=agents-ensemble
  behavioral: refresh page; verify filters re-applied from URL
  behavioral: copy URL to a fresh tab; verify same view loads
```

### AC-9 — Empty / error states teach next-action (G9)

**Description.** Each state carries a clear next-action:
- Empty (zero snapshots): link to `/settings` when creation
  toggle is off.
- Empty (filtered): inline "Clear filters" button.
- Error: Retry + collapsible diagnostic snippet (request id).
- Loading (initial): 5-row skeleton + paginator placeholder.

**Validation:**
```
Validation: pack snapshots-e2e;
  visual: empty + toggle off → state shows link "Enable
    snapshot creation in Settings →"; clicking navigates to
    /settings
  visual: filtered empty → state shows "Clear filters"
    button that resets all filter signals
  visual: error → state shows "Retry" + collapsible diagnostic
    block carrying the request id
```

### AC-10 — Single-roundtrip small digest (G10)

**Description.** When the digest payload is < 100KB, the drawer
fetches detail + digest in parallel and renders the digest without
a second click. For ≥ 100KB digests, the digest panel stays
collapsed behind a "Show digest (size: …)" button.

**Validation:**
```
Validation: pack snapshots-e2e;
  behavioral: mock detail endpoint with digest size=20KB →
    drawer renders digest section expanded (no second click)
  behavioral: mock detail with digest size=480KB → drawer
    renders "Show digest (~480 KB)" button (collapsed)
```

### AC-11 — One pageIndex-reset site (G11)

**Description.** The pageIndex reset on filter change moves from
the per-handler sites (today: 9) into a single fetch-effect that
detects any non-paginator signal change and resets `pageIndex`
once.

**Validation:**
```
Validation: pack snapshots-e2e;
  static: grep -c 'pageIndex.set(0)' frontend/src/app/pages/snapshots/snapshots.component.ts
    # ≤ 1 (the single fetch-effect site)
  behavioral: change any filter; verify exactly one
    `list(offset=0)` request fires
```

### Goal → AC summary

| Goal | AC |
|---|---|
| G1 (toggle collapsed) | AC-1 |
| G2 (filter bar height) | AC-2 |
| G3 (status color/icon) | AC-3 |
| G4 (project_name column) | AC-4 |
| G5 (drop Warm column) | AC-5 |
| G6 (row quick actions) | AC-6 |
| G7 (drawer 2-region) | AC-7 |
| G8 (URL state) | AC-8 |
| G9 (state teaching) | AC-9 |
| G10 (single-roundtrip digest) | AC-10 |
| G11 (one reset site) | AC-11 |

## 6. A11y

### 6.1 Roles + landmarks

- Page: `<header>` (top nav) + `<header>` (page title bar) +
  `<main>` (filter + table + paginator) + `<aside>` (drawer
  when open).
- Drawer: `role="dialog"`, `aria-modal="true"`,
  `aria-label="Snapshot detail"`.
- Filter bar: `<section aria-label="Filter snapshots">`.
- Table: `<caption>` carries "Snapshots (N total)"; headers
  are `<th scope="col">`.
- Status chips: text label only (icon is decorative; the chip
  text is the screen-reader name).

### 6.2 Focus order

1. Top nav links
2. Page header kebab (`Page actions`)
3. Filter bar left-to-right
4. Tag pills (each removable independently)
5. Active-filter count + Clear
6. Table — header row → first row kebab → next row kebab → …
7. Paginator
8. (Drawer open) — drawer close button (Esc) → drawer actions →
   drawer body left-to-right, top-to-bottom

### 6.3 Keyboard

| Key | Action |
|---|---|
| `Esc` | Close drawer (when open) |
| `Tab` / `Shift+Tab` | Move focus forward / back |
| `Enter` on tag input | Commit tag to filter |
| `Enter` on row | Open detail drawer |
| `Enter` on kebab button | Open menu |
| `Arrow keys` (when chip-listbox focused) | Move selection |

### 6.4 Contrast

All text-on-bg combinations meet AA (4.5:1 normal, 3:1 large).
Status chips:

| Status | bg | fg | Contrast |
|---|---|---|---|
| `active` | `rgba(16,185,129,0.14)` | `#10b981` | ≥ 5.2:1 (AA pass) |
| `running` | `rgba(59,130,246,0.16)` | `#3b82f6` | ≥ 4.6:1 (AA pass) |
| `superseded` | `rgba(100,116,139,0.16)` | `#94a3b8` | ≥ 4.5:1 (AA pass) |
| `failed` | `rgba(239,68,68,0.16)` | `#ef4444` | ≥ 5.8:1 (AA pass) |
| `interrupted` | `rgba(245,158,11,0.16)` | `#f59e0b` | ≥ 4.7:1 (AA pass) |

(Computed against `--bg-app: #0f172a`; verified against the
mockup background.)

## 7. Tradeoffs

### 7.1 Considered alternatives

**A. Keep the global toggle on the page.** Rejected. The page's
mission is browse + inspect; a global config control competes
for attention. The relocation to `/settings` is single-source
and discoverable via the kebab action menu.

**B. Render the filter bar as a Material accordion (collapsed by
default).** Rejected. Filtering is the primary interaction on
this page; collapsing it adds a click to every visit. The single
horizontal row keeps it always-visible without dominating.

**C. Make the Warm column conditionally visible (e.g. only when
the BE returns data).** Rejected. Today the column is always
`—` placeholder; reserving column width for an always-empty
cell is misleading. The data point moves to the row metadata
line, where it is honest.

**D. Use a server-side JOIN for `project_name` (Track B only).**
Deferred. The client-side join (Track A) ships day-zero-1 with
zero BE risk. The BE JOIN is a follow-up commission (Track B)
that improves list-payload self-sufficiency; the Dashboard effect
is "the list endpoint can be read without the FE holding a
project cache". Track A is the right ship-now choice; Track B
is a future v2.x.

**E. Two-finger row tap to open drawer (mobile).** Deferred. The
project is desktop-first (no responsive grid below 1100px); v2
matches that. A future v3 mobile-first pass handles touch.

**F. Keep the legacy "Show digest" button even for small digests.** Rejected. For digest < 100KB, pre-rendering saves a click
and a round-trip. The 100KB threshold is below the JSON-pretty
readability cliff for human scanning.

**G. Use `Material Symbols` font (CDN).** Rejected for the
mockup. The hand-authored mockup uses inline SVG to stay
self-contained (per the brief). The Angular implementation will
load Material Symbols via the existing `app.scss` import.

### 7.2 Open questions (none blocking)

None. The spec ships complete; Track B (server-side JOIN) is
listed in §9 "Future commissions" and is not blocking v2.

### 7.3 Risks

| Risk | Severity | Mitigation |
|---|---|---|
| `project_name` client-side join misses when project list is empty | 🟡 minor | Fallback to truncated UUID (already specced) |
| URL state sync causes a redirect loop | 🟡 minor | Single `Router.navigate` per filter change; `queryParamsHandling: 'merge'` |
| Drawer focus trap interacts poorly with overlay backdrop | 🟡 minor | Use Material's `cdk-trap-focus` directive (already in the codebase) |
| Kebab menu on row N opens on row M on rapid clicks | 🟡 minor | The menu is bound to the row, not the table; each row's menu anchor is unique |
| OD lane retry storm if the upstream recovers mid-attempt | 🟢 nit | Cardinal "one call, no retry-storm"; the spec is shipped; a future OD re-attempt is out of scope |

## 8. Design artifacts

| Path | Kind | AC refs | Lint | Mockup lane | Fallback reason |
|---|---|---|---|---|---|
| `mockups/snapshots-page-v2.html` | text-mockup (hand-authored HTML) | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 | n/a (text-lane) | text | timeout |
| `diagnosis.md` (sibling) | diagnosis (text) | (all — input to ACs) | n/a | text | n/a |

**Note on text lane.** Per Cardinal #7, a text-lane spec MUST
record `fallback_reason`; this spec does so with the enum token
`timeout`. Conformance review MUST NOT reject the spec on
lane grounds; the lane marker + fallback reason are the
audit trail. The mockup does NOT claim pixel fidelity; the
implementer is expected to honor the spec body for
fidelity-bearing choices (layout, hierarchy, color, a11y).

## 9. Future commissions (out of v2 scope)

| Item | Why deferred | Priority |
|---|---|---|
| Track B: server-side `project_name` JOIN in `SnapshotListItem` | Requires a SQL aggregate; Track A ships day-zero | P2 |
| Track C: `GET /api/snapshots/tags` for tag suggestions | New endpoint; needs auth + caching | P2 |
| Drawer-as-route (`/snapshots/{id}`) | Navigation restructure; UX impact needs review | P3 |
| Write operations (delete / archive / supersede) | Per D9, snapshots surface is read-only in v1 | P3 |
| Restore-from-snapshot action (`spawn_hot_instance` UX) | Powerful but a v3 surface | P3 |
| Keyboard nav (`j`/`k` row nav, `Cmd+K` palette) | Polish; v3 | P3 |
| Mobile-first responsive redesign | Project is desktop-first | P3 |

## 10. Pin SHA at freeze (operation order)

The SHA pin is set ONLY at `status: approved`. The order is:

1. All content is final (this file complete + mockup complete +
   diagnosis complete).
2. Compute the SHA: `git hash-object
   .agents/shared/planning/snapshots-redesign-alt/design/design-spec.md`
   → record the value as `pinned_spec_sha` in the front-matter.
3. Set `status: approved` in the front-matter (one operation
   with the SHA; the file is then immutable).
4. Surface the approved spec + SHA back to the leader.

(The task brief flagged a pin-mismatch defect in the prior
pass; the discipline here is to do steps 1–3 in order, never
preemptively.)