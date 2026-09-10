# Jobs Page — UX Direction Options (sections 1–2, part b)

Date: 2026-09-10
Author: worker (requirements analysis) for the jobs-page-improvement plan
Status: Draft — recommendation inside; final choice belongs to the user
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-jobs-page-plan` @ `feature/jobs-page-improvement` (base `e72558d1`)

Inputs: `research/current-state-findings.md`, `research/alignment-patterns-findings.md`, `research/api-capabilities-findings.md`, `current-state-analysis.md` (pain points P1–P9 referenced below by ID).

**Positioning constraint (binding):** the page is deep inspection + operations; the header panel keeps glanceable live status. No direction may collapse that split.

**Capability verdicts used throughout** (from the api-capabilities gap table, cited as gap-a…gap-e6):

| Capability | Verdict |
|---|---|
| Fetch all jobs of one mission (`?mission_id=`) | ✅ already possible |
| Jobs↔reports union (`/api/work`) | ✅ already possible (field-name differences) |
| Client-side grouping by `mission_id` + `parent_mission_id` | ✅ sanctioned client-side pattern |
| Titles on job rows | ❌ needs BE for efficiency; ✅ client workaround = join `/api/missions` or `/api/instances` (offset-paged 10/100, no batch-by-ids) or honest fallback chain |
| `/api/jobs` offset/cursor pagination | ❌ needs BE (only `limit` 1..100; FE currently sends none → BE default 50) |
| Real total / `has_more` on `/api/jobs` | ❌ needs BE (`total` = page length) |
| `/api/work` pagination | ❌ needs BE (none at all) |
| List-level SSE | ❌ needs BE (only per-job stream + notification stream with no job rows) |
| `source`/`agent_id` filters on `/api/jobs` | ❌ needs BE (FE sends both today; BE ignores them) |
| `root_only` on `/api/jobs` | ❌ needs BE (child rows degrade to null mission fields) |
| `/api/missions` paging + real total + `has_more` + `degraded` | ✅ exists |
| Per-mission lazy jobs fetch | ✅ `listJobsByMission` exists, deliberately unwired, docblock reserves it for lazy per-node fetch |

External dependency: settled-involving multi-status combos are broken BE-side; parallel arc `fix/jobs-status-combo-filter` owns the fix. Every direction notes the version gate; none designs around it.

Effort legend: S ≈ ≤1 day, M ≈ 2–4 days, L ≈ 1–2 weeks (single worker, including plain-TS specs).

---

## Direction A — "Align-in-Place Refresh"

**Thesis.** Keep the flat job list as the page's primary IA (it *is* the deep-inspection surface), but make it honest and contextual: group rows under mission/conversation headers via the proven coalesced key, unify vocabulary with the panel, fix every dead/dishonest control, surface defer-blocked as a first-class page element, and put a windowed-render + URL-state foundation under it. No IA bet on the missions API.

### IA sketch (text wireframe)

```
┌ /jobs — Jobs & receipts ─────────────────────────────────────────────┐
│ [Queues | All Work]   Status▾ Source▾ Agent▾ Project▾ ☐Deleted  ⧉URL │
│ ⚠ 2 defer-blocked holders (1 paused, 1 stalled)   [Review holders →] │
│ ── ▾ ● fix-resume-router · worker · 3 jobs · 2m ago ──────────────── │
│      ▦ settled (receipt) → worker-a        2m      [⇩][↺][🗑][👁]    │
│      ▣ processing (task)  retry×1          4m      […]              │
│ ── ▸ ✓ jobs-page-plan · planner · 12 jobs · 1h ago  (collapsed)      │
│ ── (no mission context) ──                                           │
│      ▣ pending (task)  api · 11:02                                   │
│ Showing newest 100 · list may continue — refine filters ⋅ [Reload ⟳] │
└──────────────────────────────────────────────────────────────────────┘
▦ = settled/receipt_long glyph (teal)   ▣ = task row   ● = live conversation
```

- Grouping is a **presentation layer over the same flat fetch** (coalesced key `mission_id ?? instance_id`); rows without mission context fall into an explicit "(no mission context)" group — never-hide, panel-parity.
- Group headers use the panel's honest fallback chain (`job_metadata.instance_name → agent_id → first-8-chars`); optional lazy `/api/missions` title enrichment for the visible page only.
- View-mode toggle survives (Queues = queue-scoped operations home; All Work = union incl. reports), but both views render through the SAME grouped card list and the SAME filter pipeline (kills the dual-path that produced P5.1).

### Pain-point resolution map

| Pain | Resolves? | How |
|---|---|---|
| P1.1 vocabulary | ✅ | One status vocabulary on the page (canonical + `settled`=receipt styling per ADR-MISSION-01); BE-internal names stay service-internal |
| P1.2 no grouping | ✅ | Group headers via coalesced key |
| P1.4 no titles | ✅/◐ | Honest fallback chain day 1; lazy missions-title join as enhancement (client workaround for gap-d) |
| P2.1/P2.2 scale | ◐ | Explicit `limit=100`; honesty banner when the window is full; **true pagination stays impossible without BE** (gap-b) |
| P2.3 render | ✅ | `cdk-virtual-scroll` over the flat (ungrouped) projection; grouping headers recomputed from the same window |
| P2.4 poll | ✅ | Visibility-gated tick + pause while drawer/modal open |
| P2.5 lossy mapping | ✅ | All-work path stops mapping through lossy `workToJob` for display fields it actually has (started/completed/error exist on WorkRecord; message content does not — stays an honest gap) |
| P3 defer | ✅ | Banner + holders panel; wire remediation behind confirm (open question OQ-8) |
| P4 live | ◐ | Poll discipline only; per-row SSE for visible non-terminal rows is an *optional* capped enhancement (see live strategy) |
| P5 dead controls | ✅ | Source/agent either hidden in all-work or served from one shared pipeline; if wired against `/api/jobs` they must be labeled client-side-only until gap-e5 lands |
| P6/P7 mobile+a11y | ✅ | Port panel WAI-ARIA (chevron `aria-expanded`, clamped arrow nav, Enter-preventDefault); sidebar collapses ≤768px into a select/sheet |
| P8 monolith | ✅ | Extract `JobsFilterState` (URL-bound), `JobsPageStore` (fetch+patch), grouping/window pure models |
| P9 tests | ✅ | Extraction makes new logic plain-TS testable; missing service suites filled |

### Reuse vs build

- **Reuse (panel-proven, near-verbatim):** coalesced grouping key + never-hide semantics (`instance-node.model.ts:382-394`), honest fallback title chain (`job-queue-panel.component.ts:508-517`), `settled`→`receipt_long`/teal + `completed`→`check_circle` styling, `mission-liveness-chip`, defer-blocked severity helpers (`defer-blocked.model.ts`), retain-last-data fetch discipline (per-leg catchError pattern from indicator §3), WAI-ARIA keyboard model (§7 of alignment research).
- **Build new:** `JobsPageStore` (single fetch/patch pipeline behind both views), URL-bound filter state, virtual-scroll integration with grouped rendering, page-level defer holders panel, honesty banner logic, mobile sidebar collapse.

### FE-only vs needs-BE (explicit)

- **FE-only:** everything above, *except* true pagination/total (stays a window with honesty banner), `source`/`agent_id` **server-side** filtering (client-side filtering over the window is what ships), any row-level live push.
- **Needs BE (explicitly out unless user opts in — OQ-2/OQ-3):** offset/cursor + real total on `/api/jobs` (gap-b/c), `limit` on `/api/work` (gap-b), source/agent filters server-side (gap-e5), list SSE (gap-e1), titles on job rows (gap-d).

### Live-updates strategy (no list SSE)
- One visibility-gated poll tick (`document.visibilitychange` pause/resume + immediate refresh on tab re-focus), 30s default (cadence OQ-5); paused while drawer or any modal is open (actions are about to mutate state anyway).
- SSE patches stay in-place, order-preserving (merge-order discipline: never client-side re-sort).
- Optional capped enhancement: per-job SSE for the ≤N (e.g. 5) *visible non-terminal* rows in the window, managed as a small pool replacing the singleton service; only if measurements show the poll is not enough.

### Pagination / virtualization strategy (limit-clamp-100, no cursor)
- FE sends `limit=100` (max clamp) always; render through virtual scroll; **no fake "Load more"** (re-querying without offset returns the same page — gap-b).
- Full window (100 rows) ⇒ banner: "showing newest 100 — refine filters to narrow" (honest: we cannot know if more exist; `total` is page length, gap-c).
- All-work adds a client-side hard cap guard (e.g. refuse to render >1000 rows, show explicit truncation notice) until BE adds `limit` to `/api/work`.
- BE cursor/total arc = optional Phase (OQ-2); the store's public shape is designed so paging slots in later without UI rework.

### Drawer evolution
- Fix the gates: Result renders for `settled`/`report` rows (drop the `status === 'completed'`-only gate to "has `result_summary`"); Timeline fed from WorkRecord fields in all-work; Message for report rows stays hidden-by-design with honest copy (no BE surface carries report message content — gap-e6 note).
- Keep drawer as-is structurally (it is already the deep-inspection asset); add `aria-*` and focus-trap polish; support deep-link `?job=<id>` open (OQ-6).

### Defer-blocked surfacing
- Persistent page-level banner (severity via existing `defer-blocked.model.ts` helpers: AMBER/INFO/RED-anomaly), "Review holders →" opens an inline panel listing holders (instance, agent, kind, since) with per-holder actions **force-complete / resend-foreground** behind ConfirmDialog — endpoints already in `JobService` (`:240-272`).
- Preflight/defer fetches join the retain-last-data discipline (kills the stale red-glow, P3.3).

### Effort (per workstream) & risk

| Workstream | Size |
|---|---|
| `JobsPageStore` + filter-pipeline unification (both views one path) | M |
| URL-bound filter state (`JobsFilterState`) | S |
| Grouping model (coalesced key + never-hide + fallback titles) — pure TS | M |
| Virtual scroll + window policy + honesty banner | M |
| Dead-filter fix / honest hiding (all-work) | S |
| Defer banner + holders panel + wired remediation | M |
| Drawer gate fixes + deep-link | S |
| Poll discipline (visibility/drawer pause) | S |
| A11y pass (ARIA port + keyboard) | M |
| Mobile collapse (≤768px) | M |
| Tests (new pure-model suites + missing service specs) | M |

**Risk: LOW–MEDIUM.** Mostly mechanical fixes on a proven IA; grouped virtual scroll is the one genuinely fiddly bit (header rows inside virtual viewport). No BE dependency. Biggest product decision is the honesty banner (OQ-2).

---

## Direction B — "Instances-Primary Rebuild"

**Thesis.** The page becomes a mission/conversation-first surface like the panel — missions (== instances) as first-class paginated rows, their jobs as receipts nested beneath — with the flat list demoted to a secondary "All receipts" mode (or removed). This is the maximal-alignment option: it makes the page speak the user's mental model natively and becomes the first rich `/api/missions` consumer.

### IA sketch (text wireframe)

```
┌ /jobs — Conversations & their jobs ──────────────────────────────────┐
│ [Missions | All receipts]  Liveness▾ Status▾ Project▾      ⚑ defer ⚠2│
│ ▾ ● fix-resume-router (worker) · 3 jobs · 2m ago      ● live         │
│    ├ ▦ settled (receipt) → worker-a        2m      [⇩][↺][👁]        │
│    ├ ▣ processing (task)  retry×1          4m                        │
│    └ ▣ completed (task)                    9m                        │
│ ▸ ◷ jobs-page-plan (planner) · 12 jobs · 1h ago       ✓ completed    │
│ ▸ ▣ data-pipeline (scheduler) · 5 jobs · 3h          ✗ failed        │
│ Page 2 of ∄ · has_more=true · 40 missions total    [Next →]          │
└──────────────────────────────────────────────────────────────────────┘
```

- **Missions axis is genuinely paginated** (`/api/missions` limit/offset/`has_more`/`total`/`degraded` all exist) — the only direction with real pagination *today*.
- Per-mission jobs load lazily on expand via `listJobsByMission` (`GET /api/jobs?mission_id=…`, exists, deliberately unwired for exactly this).
- Flat list demoted to "All receipts" secondary mode (the current view, modernized) or dropped (OQ-1).

### Pain-point resolution map

| Pain | Resolves? | How |
|---|---|---|
| P1.* IA/vocabulary | ✅✅ | Native instances-primary IA; titles from missions rows (`title`, `initiative_preview` — no fallback chain needed at top level) |
| P2.1/P2.2 scale | ✅/◐ | Missions page = 10/100 rows with real `has_more`; per-mission job sets are small. **But:** unattached/orphan jobs and the "All receipts" mode still hit the `/api/jobs` window problem; reports axis still needs `/api/work` |
| P2.3 render | ✅ | Panel-style flattened visible-items list (same construction as `visibleInstanceTreeItems`) — keyboard order == DOM order by design |
| P2.4 poll | ✅ | Same visibility-gated discipline; expanded-mission job lists refetched on tick |
| P3 defer | ◐ | Banner + defer holders; holders are instance-keyed so they can also stamp a warning glyph onto matching mission rows — but the panel already owns much of this glanceable role (positioning tension) |
| P4 live | ◐ | Poll missions + poll **expanded** missions' jobs; optional capped per-row SSE pool. No list push without BE |
| P5 dead controls | ✅ | Single filter pipeline over missions liveness/status; no dual-path |
| P6/P7 mobile+a11y | ✅✅ | Inherits the panel's full WAI-ARIA tree + clamped arrow-key model wholesale — strongest a11y story by construction |
| P8 monolith | ✅ | Page rebuilt around a `MissionTreeStore`; old monolith shrinks to the secondary mode |
| P9 tests | ✅ | New mission-tree model is pure functions — ideal plain-TS corpus |

### Reuse vs build

- **Reuse:** `buildInstanceNodes`/`buildInstanceTree`/`visibleInstanceTreeItems`/`nextInstanceTreeItem` patterns (adapted to mission rows: `parent_mission_id` ↔ `parent_id`), chevron-only expansion + `aria-expanded` + Enter-preventDefault guards, liveness chips + receipt glyphs, degraded-envelope retention (`degraded:true` never clobbers last good payload), dumb-panel/smart-container split.
- **Build new:** mission-node model (missions + attached receipts + lazy jobs), `listJobsByMission` integration (first real consumer — docblock-reserved), mission detail surface (drawer or expanded-pane), orphan-jobs handling (`recentFlat` analog for jobs whose mission is off-page — never-hide), "All receipts" modernized mode if kept.

### FE-only vs needs-BE (explicit)
- **FE-only:** the whole missions-tree UX (missions paging ✅, per-mission jobs ✅, titles ✅ on missions rows, client-side subtree via `parent_mission_id` ✅ sanctioned).
- **Needs BE for the full vision:** "missions with jobs nested in one call" (gap-e2 — avoided via lazy fetch, at the cost of an N+1-ish fetch profile), list SSE (gap-e1), jobs-axis total for the secondary mode (gap-c), `root_only` parity (gap-e3).

### Live-updates strategy
- Visibility-gated poll of `/api/missions` (paged, `liveness` filter server-side) + refetch of **expanded** missions' job lists on tick; collapse state and `userTouched`-style guards survive refreshes (panel pattern).
- Optional capped per-job SSE pool for visible non-terminal receipt rows.
- No list push without BE (gap-e1) — same ceiling as A; the missions axis just pollutes less (page of missions ≠ page of all jobs).

### Pagination / virtualization
- Missions axis: real pagination — explicit pager or infinite scroll with `has_more` (both honest; `total` exists).
- Jobs axis: per-mission lazy fetch, expected small; cap + honesty note per mission (`linked_jobs` count from missions rows gives an honest "showing N of M").
- Secondary "All receipts" mode inherits Direction A's window policy (its unsolved residual).

### Drawer evolution
- Drawer grows a **mission mode**: mission header (title/initiative/liveness/epoch) + receipt list + per-job detail (existing drawer embedded). This is the single largest new build item.
- Job drawer itself: same gate fixes as A.

### Defer-blocked surfacing
- Same banner as A; additionally stamp holders onto matching mission rows (instance-keyed join) so a stuck conversation is visible at tree level. Note the positioning tension: the panel already surfaces defer-blocked glanceably — the page's added value is the holders *list + actions*, not the glyph.

### Effort & risk

| Workstream | Size |
|---|---|
| Mission-node model + tree builders (pure TS, adapted) | M–L |
| `MissionService` (list/detail/paged, degraded envelopes) | S–M |
| `listJobsByMission` lazy fetch + caps + never-hide orphan bucket | M |
| Tree page component (keyboard/ARIA port, expansion persistence) | L |
| Mission drawer/detail surface | L |
| Secondary "All receipts" mode (A's flat workstream, reduced) | M |
| Defer integration (banner + glyph join) | S–M |
| Tests (model + service + parity) | L |

**Risk: MEDIUM–HIGH.** First rich missions consumer (contract discovery risk); N+1 fetch profile on expand-heavy usage; **positioning drift risk** — a full-page instances-primary tree starts duplicating the panel's glanceable role; needs explicit differentiation (page = paginated, deep, operational). Residual `/api/jobs` window problem persists in the secondary mode.

---

## Direction C — "Hybrid Dual-Lens"

**Thesis.** One shared `JobsPageStore` (single fetch + single filter pipeline), projected into two lenses: **Conversations** (mission-grouped tree, client-grouped from the jobs window + titles join — not missions-first like B) and **Flat** (dense virtualized table for exhaustive filtering/bulk ops). This is A's data layer plus B's primary lens, with the mode-divergence failure explicitly engineered away.

### IA sketch (text wireframe)

```
┌ /jobs ───────────────────────────────────────────────────────────────┐
│ [◉ Conversations | ☰ Flat]   Status▾ Source▾ Agent▾ Project▾  ⚠2 defer│
│                        ┌ one JobsPageStore ─┐                        │
│  Conversations lens:   │ fetch·filter·patch │  Flat lens:           │
│  ▾ ● mission A 3 jobs  └────────────────────┘  ▦ settled receipt  2m │
│    ├ ▦ settled receipt 2m  (tree projection)   ▣ processing task  4m │
│    └ ▣ processing task 4m                      ▣ pending        11:02│
│  ▸ ✓ mission B 12 jobs                         (virtualized table)   │
│ Showing newest 100 · refine filters                                  │
└──────────────────────────────────────────────────────────────────────┘
```

- The defining invariant: **both lenses read the same store state** — a filter change re-projects both; a SSE patch updates both. This directly eliminates the failure mode that created P5.1 (two modes with two fetch paths).
- Conversations lens here is **client-side grouped from the jobs window** (coalesced key + `/api/missions` title join for visible groups), not missions-paginated like B — cheaper than B, but inherits the jobs-window cap (group count bounded by window).

### Pain-point resolution map
Same coverage as A for every store-level pain (P2, P3, P5, P8, P9); grouping/a11y/vocabulary pains (P1) resolved as in A, plus the flat lens becomes a true dense operations table (sortable columns are now safe: one pipeline). The unique claim: **no pain resolves that A doesn't**, except "flat list feels unstructured for browsing" — which A's group headers already address. C's value is strategic (dual-purpose surface) rather than corrective.

### Reuse vs build
- Reuse: everything A reuses + the tree-projection patterns B reuses (applied to client-grouped data).
- Build: everything A builds + lens-switch shell + dual projection computeds + cross-lens parity tests + dense table variant of job rows (or reuse card at `compact` density).

### FE-only vs needs-BE
Identical to A (all BE gaps unchanged). C does not reduce any BE dependency; it adds FE surface on the same FE-only foundation.

### Live-updates / pagination / drawer / defer
- One poll tick feeds the store; both lenses update from it (single fetch budget — the efficiency argument for C).
- Pagination: A's window policy; the missions-paginated axis of B is explicitly *not* included (that's the A-vs-B difference; C-with-B's-axis is effectively B+flat).
- Drawer: shared, as A.
- Defer: shared banner; lens-appropriate drill-down (tree glyph join + flat filter shortcut).

### Effort & risk

| Workstream | Size |
|---|---|
| Everything in A's table | (as A) |
| Lens shell + dual projections | M |
| Dense table variant + parity tests | M–L |

**Risk: MEDIUM.** Doubles QA surface; the historical dead-filter bug is precisely a dual-mode divergence, and C must prove the single-pipeline invariant with cross-seam tests. Shipping A first and C's lens later is the de-risked path to the same place.

---

## Comparison snapshot

| | A Align-in-Place | B Instances-Primary | C Hybrid Dual-Lens |
|---|---|---|---|
| Mental-model alignment | ◐ (grouping headers) | ✅✅ native | ✅ (both lenses) |
| Solves pain points | all, most fully | all except jobs-window residuals in secondary mode | == A |
| Real pagination today | ✗ (window + honesty) | ✅ missions axis | ✗ |
| BE asks avoided | all | most (not list-SSE, not jobs-total) | all |
| Positions vs panel | clean separation | drift risk | clean (flat lens) |
| FE effort total | **M–L** | **L+** | **L+** |
| Risk | low–med | med–high | med |

---

## Recommendation

**Adopt Direction A — "Align-in-Place Refresh" — architected as the foundation of C ("C-shaped groundwork").**

Rationale:
1. **It is the only direction whose every element is evidence-backed as FE-shippable today.** The gap table (api findings §8) rules out honest pagination, real totals, list SSE, and server-side source/agent filters without BE; B's headline advantage (real pagination) exists only on the missions axis and its secondary mode still inherits the same jobs-window ceiling. A fixes all nine pain themes without a single BE dependency.
2. **Positioning safety.** The page's mandate is deep inspection + operations. A keeps that mandate at the center (flat exhaustive list + drawer + ops) and adds mission *context*, whereas B rebuilds the page into a second instances-primary tree — structurally adjacent to the panel's glanceable role. The panel is 560px top-10-roots; a full-page tree competes with it rather than complementing it.
3. **C is the right long-term shape but the wrong first move.** The dual-mode divergence bug (P5.1) is the cautionary tale: build the single `JobsPageStore` + one filter pipeline now (A), so a Conversations lens later is a *projection*, not a second pipeline. C-without-A's-store is how the current mess was made.
4. **Risk profile.** A is mostly mechanical, each workstream independently shippable, and every fix maps 1:1 to a pain point (P1–P9) — the phased plan writes itself with per-phase regression pins in plain-TS specs.

Concretely: recommend the phased plan adopt A's workstreams in an order that lands the store+filters first, then honesty/window + grouping + defer, then a11y/mobile polish; carry B and C as documented alternatives, and revisit B only if the user opts into the BE pagination/list-SSE arc (OQ-2), which would also change C's calculus.

---

## Non-goals (this plan deliberately does NOT)

1. **Not replace, rework, or restyle the header panel/indicator** — the glanceable/live-status role stays exactly where it is; the page only stops *contradicting* it (vocabulary, glyphs).
2. **No BE work in the default plan.** The only BE items are *named optional asks* behind open questions OQ-2/OQ-3 (jobs pagination/real-total; `/api/work` limit; source/agent filters; row titles; list SSE). Nothing in the default phases blocks on them.
3. **Not fix the settled+failed combo defect** — owned by the parallel arc `fix/jobs-status-combo-filter` (base e72558d1). The plan pins it as a version-gated dependency for any settled-involving filter phase and explicitly does not compensate FE-side.
4. **Not remove the queue concept** (queue sidebar, queue-scoped views, DLQ replay) — its fate is OQ-1, not a unilateral IA decision.
5. **Not introduce a design-system/component-library overhaul** — the page reuses existing panel patterns and Material primitives as-is.
6. **Not touch chat, instances sidebar, or notification surfaces** (their merge-order and fetch disciplines are out of scope).
7. **Not implement mission-events/epoch history** (M4(ii) is a BE feature; `epoch` is constant 1 today).
8. **Not chase real-time parity with no BE** — no fake "load more" against a cursor-less endpoint, no unbounded SSE fan-out, no silent truncation. Honesty surfaces instead.

---

## Open questions (decision points only the user can make)

| # | Question | Why it matters | Default if unanswered |
|---|---|---|---|
| OQ-1 | Should the **Queues vs All Work** split survive at all — or collapse into one list with a kind/personality filter (task / receipt / report) + optional queue facet? | Determines the top-level IA of Direction A; queues are also an *operations* surface (create/start/stop) not just a filter | Keep both modes, unified pipeline (conservative) |
| OQ-2 | Is a **BE pagination arc** (offset/`has_more`/real-total on `/api/jobs`, `limit` on `/api/work`) in scope, or is the newest-100 window + honesty banner acceptable? | The single biggest UX ceiling; also gates any future Conversations-lens depth | FE-only; window + banner |
| OQ-3 | **Source/agent filters**: drop them in all-work (honesty), keep them client-side-over-window with a label, or ask BE to implement server-side (gap-e5)? | Dead-or-dishonest controls erode trust; server-side needs BE | Client-side-over-window, labeled; BE ask noted |
| OQ-4 | **Title strategy**: honest fallback chain only (panel-parity), client join against `/api/missions` pages (cost: up to several paged fetches for wide windows), or BE row-titles (gap-d)? | Latency/complexity tradeoff on every grouped view | Fallback chain day 1; lazy missions-join enhancement |
| OQ-5 | **Poll cadence & visibility policy** for the page: keep 30s visibility-gated, or align with the panel's 8s while the tab is visible? | Freshness vs load; page is heavy-inspection, panel is glanceable | 30s visibility-gated + refocus refresh |
| OQ-6 | **Deep-linking**: should `/jobs` get URL filter state + `?job=<id>` drawer open (recommended), and should a job get its own route (`/jobs/:id`)? | Bookmarkability, incident sharing, testability of restored state | URL state yes; separate route only if requested |
| OQ-7 | **Queue sidebar on mobile** (≤768px): collapse into a select/sheet, or hide with queue selection moved into the filter bar? | Only meaningful if OQ-1 keeps queues | Collapse into sheet |
| OQ-8 | **Defer remediation ownership**: should the page offer force-complete / resend-foreground inline (destructive-ish ops currently housed in the header indicator + cleanup dialog), or link out to the existing surfaces? | Safety/ownership boundary for destructive actions | Inline behind two-stage ConfirmDialog, matching cleanup-dialog gravity |
