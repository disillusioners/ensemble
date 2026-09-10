# Decisions Log: jobs-page-improvement

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft (final choice on flagged items belongs to the user)
Arc: jobs-page-improvement · Worktree `agents-ensemble-wt-jobs-page-plan` @ `feature/jobs-page-improvement` (base `e72558d1`)

Each entry: Decision / Rationale / Alternatives considered / Open-question binding (where applicable). Defaults below are APPLIED in the phase plans; every OQ-flagged decision remains a user override point.

---

## D1 — Direction: A "Align-in-Place Refresh", architected as C-shaped groundwork

**Decision.** Adopt Direction A with Direction C's data layer (one `JobsPageStore`, one filter pipeline, pure projection models) built in from Phase 1.

**Rationale.** (1) Every A element is evidence-backed FE-shippable today — the api-capabilities gap table (gap-b/c/e1/e5/d) rules out honest pagination, real totals, list SSE, and server-side source/agent filters without BE. (2) Positioning safety: the page's mandate is deep inspection + operations; A keeps that at the center and adds mission *context*, whereas B rebuilds the page into a second instances-primary tree structurally adjacent to the panel's glanceable role. (3) The dual-mode divergence bug (P5.1) is precisely what C-without-a-store recreates; building the store now makes any future Conversations lens a *projection*, not a second pipeline. (4) A is mostly mechanical, each workstream independently shippable, and every fix maps 1:1 to a pain point P1–P9 — per-phase regression pins in plain-TS specs.

**Alternatives.** B (Instances-Primary Rebuild): native mental-model alignment + real pagination — but only on the missions axis (offset-paged 10/100), secondary mode still inherits the jobs-window ceiling, L+ effort, med–high risk, positioning drift. C-full (Dual-Lens now): same corrective coverage as A but doubles QA surface and ships the lens before the invariant that makes it safe. Both documented in `ux-direction-options.md` and revisitable after OQ-2.

---

## D2 — Pagination / windowing strategy: explicit newest-100 window + honesty banner; no fake paging

**Decision.** FE always sends `limit=100` (BE max clamp) on `/api/jobs`; a full window renders the banner "Showing newest 100 · the list may continue — refine filters to narrow" + manual Reload; NO "Load more" (re-querying without offset returns the same page — gap-b); all-work gets a client-side hard render guard (>1000 rows ⇒ explicit truncation notice, never silent slicing); the store's window shape reserves a paging seam so BE-OPT-1 slots in without UI rework.

**Rationale.** `total` on `/api/jobs` is page length, not a count (`jobs_crud.py:866-869`); `/api/work` has no pagination at all. Any completeness claim would be false; honesty surfaces are the only truthful FE-only posture (non-goal #8). The current silent default-50 cap is the worst of both worlds — truncated AND invisible (P2.1).

**Alternatives.** BE pagination arc now (rejected by default — OQ-2); client-side "load all" via repeated mission-scoped queries (rejected: combinatorial, still no ordering guarantee across windows); pretend-infinite scroll (rejected: re-fetches the same page).

**OQ binding:** OQ-2 default (FE-only). **GATE-COMBO-FIX interaction:** banner copy never promises completeness, so a combo-defect-truncated window is not made more deceptive by this design.

---

## D3 — Live-update strategy: single visibility-gated 30s poll; no list SSE; per-row SSE pool deferred

**Decision.** One poll tick feeds the store (both views + defer leg ride it): 30s cadence, pauses on hidden tab / open drawer / open modal, immediate refresh on refocus (debounced ≥2s). SSE patches remain in-place and order-preserving. The optional capped per-row SSE pool (≤5 visible non-terminal rows, replacing the singleton `JobSseService` usage) is explicitly deferred — documented, not planned.

**Rationale.** List-level SSE does not exist (gap-e1); the notifications stream carries no job rows (`notifications.py:29-90`). The page is a heavy-inspection surface, not glanceable — 30s is proportionate (the panel keeps its own 8s; different job). Modal/drawer pause prevents list replacement under an about-to-mutate action surface. Daemon-side terminal-receipt wake latency (~15min under pool saturation, known sibling risk) is inherited by any poll — noted as external, not designed around (P4.3).

**Alternatives.** 8s panel-aligned cadence (rejected: doubles load for marginal benefit on an inspection surface); WebSocket/list push (needs BE, gap-e1); aggressive SSE fan-out (violates non-goal #8).

**OQ binding:** OQ-5 default.

---

## D4 — Defer-blocked surfacing & remediation: page banner + inline holders panel + actions behind two-stage confirm

**Decision.** Persistent page-level banner under the filter bar, severity via the existing client-side conjunction (AMBER = any paused/stalled holder, INFO = all live, RED anomaly = `pending_count > 0 && holders == []`); "Review holders →" opens an inline panel (instance, agent, kind, since; paused > stalled > live order); per-holder `force-complete` / `resend-foreground` wired inline behind a two-stage ConfirmDialog naming the holder and stating irreversibility; defer + preflight fetches join the retain-last-data discipline, replacing the silently-swallowed preflight failure (`:587-589`).

**Rationale.** Defer-blocked is currently discoverable only as a count inside the DESTRUCTIVE System-Cleanup dialog (P3.1) while the action endpoints already exist and are wrapped (`job.service.ts:240-272`) — pure wiring. The RED-anomaly state (rows pending, zero holders) gets "Open System Cleanup" as its action rather than a bare alarm. Retain-last-data kills the stale red-glow (P3.3).

**Alternatives.** Link-out to the header indicator's unstick menu / cleanup dialog (fallback if the user rejects inline destructive ownership — OQ-8); read-only banner without actions (leaves P3.2 unresolved: operator must leave the page to act).

**OQ binding:** OQ-8 default (inline, cleanup-dialog gravity).

---

## D5 — Filter-set redesign: dead filters resolved per OQ-3 default; structural single-pipeline enforcement

**Decision.** All filters flow through the one store pipeline (D1/D7). Source/agent: client-side over the fetched window in BOTH views, controls labeled "window-scoped". Show-deleted: hidden + inert in all-work with explanatory copy (BE soft-delete concept does not exist on `/api/work` — `work_resolver.py:2144`). Status: multi-select retained; combos forwarded verbatim to BE. Queue: queues-view-only via sidebar (OQ-1 default keeps queues). View mode: retained as a projection selector over the same store.

**Rationale.** The dead-control bug is structural (`filteredJobs` never touches `works()`; `workToJob` nulls `source` so the agent filter could never match — P5.1/P5.2); per-control patches would regress. Server-side source/agent needs BE (gap-e5 — `jobs_crud.py:638-682` has no such params). Labeling keeps the client-side limitation honest instead of pretending server-side semantics.

**Alternatives.** Hide source/agent entirely in all-work (max honesty, but loses useful window-scoped narrowing and contradicts unification); BE-OPT-2 server-side filters (documented optional upgrade path — when it lands, the same controls flip from window-scoped to server-side with no UI change).

**OQ binding:** OQ-3 default. **GATE-COMBO-FIX:** settled-involving multi-status combos forwarded verbatim; correctness gated on the parallel arc — not compensated FE-side.

---

## D6 — URL / deep-link state: query-param-bound filters + `?job=<id>` drawer open; no separate job route

**Decision.** `JobsFilterState` (view mode, status, source, agent, project, queue, show-deleted) serializes to query params; URL wins over localStorage on load; bare URL hydrates once from localStorage then `replaceUrl` (one-time migration); filter changes write via `queryParamsHandling: 'merge'` (debounced ~300ms); `?job=<id>` opens the drawer (in-window directly; out-of-window via single-job GET; 404 ⇒ honest state). NO `/jobs/:id` route.

**Rationale.** Today nothing is shareable — only project + view mode survive reload, via localStorage (P8.2, cannot-do #8/#9). Query-param state is testable, shareable for incidents, and back/forward-correct without new routes. The single-job GET exists BE-side (`jobs_crud.py:606-615`), so deep-link needs at most a one-method FE wrapper, not a BE ask.

**Alternatives.** Separate `/jobs/:id` route (more canonical deep-linking, but new route + navigation semantics for marginal gain — only if requested); keep localStorage only (rejected: the pain point).

**OQ binding:** OQ-6 default.

---

## D7 — Store architecture: one `JobsPageStore`, one pipeline, view modes as projections

**Decision.** A page-scoped `JobsPageStore` (signals-based, plain-TS testable, no TestBed) owns: fetch legs (jobs + works + defer) with per-leg `catchError → retain last data`, the unified `filteredJobs` computed over the ACTIVE dataset, SSE patching (in-place, order-preserving), degraded/error flags per leg, and expansion/window state. `jobs.component.ts` becomes a thin shell: template bindings + dialog orchestration + router. Grouping/window/poll/keyboard logic live in separate pure model files (`models/`, `pages/jobs/*.model.ts`).

**Rationale.** The 1,252-LOC monolith owning filters + view modes + SSE + 3 dialogs + DLQ + cleanup + raw HttpClient (P8.1) is the root cause enabling P5.1's dual-path drift. C-shaped groundwork (D1) requires exactly this seam. Pure models keep the plain-TS no-TestBed convention testable (binding constraint).

**Alternatives.** Two stores (one per view mode) — recreates the divergence; service-singleton store shared with the indicator (rejected: the panel is deliberately stateless-dumb with its own fetching indicator; coupling the page to it violates surface separation); NgRx-style global store (rejected: framework overhead disproportionate to one page).

**OQ binding:** none directly; enables cheap OQ-1 reversal.

**Contingency (OQ-1 flip).** If OQ-1 ever flips to collapsing the modes into one list + kind/personality facet, queue CRUD (create/start/stop/delete in `queue-list`) needs an explicit NEW home (e.g., a Queues management dialog/drawer reachable from the filter bar); queue *selection* is plain `JobsFilterState` and survives untouched — the store is unaffected, only the sidebar surface relocates. Default remains keep-both-modes, so this is contingency only.

---

## D8 — Mission-title strategy: honest fallback chain day 1; capped lazy missions-join enrichment

**Decision.** Group headers resolve titles via the panel-proven fallback chain (`job_metadata.instance_name → agent_id → first-8-chars`) immediately; a new `mission.service.ts` (first rich `/api/missions` consumer: paged list + `GET /api/missions/{id}`) enriches VISIBLE group headers only, capped at ~3 page fetches per refresh; degraded/failed enrichment retains the fallback title (never blocks render).

**Rationale.** Titles exist only on `/api/missions` (`title`, `initiative_preview`) / `/api/instances` (`title`) — both offset-paged 10/100 with no batch-by-ids endpoint (gap-d); a BE-efficient row-title is BE-OPT territory. The fallback chain is already shipped and spec-pinned in the panel (`job-queue-panel.component.ts:508-517`) — honest by construction. Capping bounds the cost of wide windows.

**Alternatives.** Fallback chain only (cheapest, but headers stay raw-ish where one cheap join would fix them); join ALL groups eagerly (unbounded fetch amplification on wide windows — rejected); BE row-titles (needs-BE, documented ask).

**OQ binding:** OQ-4 default.

---

## D9 — Mobile breakpoints: keep single 768px breakpoint; sidebar collapses into a sheet

**Decision.** 768px remains the page's single breakpoint. ≤768px: queue sidebar collapses into a sheet (select/bottom-sheet from existing Material primitives), queue operations (create/start/stop/delete) reachable through it; chip filter row gets an explicit horizontal-scroll-or-wrap strategy; drawer keeps its existing full-width behavior. Any mat-menu-based sheet must use a GLOBAL `panelClass` width override (global `.mat-mdc-menu-panel` caps at 280px; component-scoped `::ng-deep` can never match a CDK overlay at `<body>`).

**Rationale.** P6.1 is a coverage gap, not a multi-breakpoint design problem; adding intermediate breakpoints doubles QA surface for marginal benefit on an inspection page. The menu-width gotcha is a live repo trap (alignment §7, `styles.scss:75-96`).

**Alternatives.** Hide sidebar entirely and move queue selection into the filter bar (OQ-7's alternative — cleaner filter UX but demotes queue OPERATIONS, contradicting OQ-1's operations-home rationale); new 1024px intermediate breakpoint (rejected: QA cost).

**OQ binding:** OQ-7 default (sheet). Only meaningful while OQ-1 keeps queues.

---

## D10 — Test strategy: plain-TS logic-mirror only; cross-seam invariants; honesty-shaped fixtures; template audit per phase

**Decision.** (1) ALL new logic lives in pure models/store files; specs are plain-TS mirrors — zero TestBed. (2) Every seam gets ≥1 cross-seam invariant test (same filter state ⇒ same rows in both view modes; keyboard order == DOM order; group projection never drops/duplicates rows). (3) Retain-last-data pins for EVERY degradation shape (fetch-error, empty-200, degraded:true) on every fetch leg. (4) Fixtures AT and PAST caps (100-row window, 1001-row guard). (5) Old-behavior grep-pins written BEFORE flipping any legacy contract (drawer gates, dual-path). (6) Mirror-parity: new specs pin real construction (grep real `.set()`/event sites) — including a real-service parity pin for `work.service` whose existing 293-line spec is a mirror only. (7) Template-extraction audit — diff-audit deleted/added `(click)`/`(keydown)` hunks per invocation site — is a MANDATORY step in every phase touching templates, plus a final sweep in Phase 6. (8) Merge-order rule: server `created_at DESC` order is authority; SSE patches in place; client-side re-sorting prohibited (grouping sorts group order only). (9) GATE-COMBO-FIX fixtures (settled+failed combos) marked and skipped-with-reason until the parallel arc deploys.

**Rationale.** House convention (Testing & QC): plain-TS specs cannot see DOM, so template changes carry the audit as their verification; the dead-filter bug class is invisible without cross-seam invariants; "pre-existing warnings" claims drift without attribution against the known-10 corpus.

**Alternatives.** TestBed-based component tests (rejected: violates the repo's zero-TestBed page convention and adds a second test idiom); relying on E2E only (rejected: none exists for this surface).

**OQ binding:** none.

---

## Decision index → phase binding

| Decision | Lands in | Overridable via |
|----------|----------|-----------------|
| D1 | Phase 1 (store), all | — (dispatch-fixed direction) |
| D2 | Phase 2 | OQ-2 |
| D3 | Phase 2 (+ Phase 4 defer leg) | OQ-5 |
| D4 | Phase 4 | OQ-8 |
| D5 | Phase 1 | OQ-3 |
| D6 | Phase 5 | OQ-6 |
| D7 | Phase 1 | — (constitutes D1 groundwork) |
| D8 | Phase 3 | OQ-4 |
| D9 | Phase 6 | OQ-7 |
| D10 | All phases (convention) | — |
