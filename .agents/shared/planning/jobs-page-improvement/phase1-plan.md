# Phase 1: Store & Filter-Pipeline Unification (`JobsPageStore`)

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A "Align-in-Place Refresh" architected as C-shaped groundwork

## Objective

Replace the jobs page's dual fetch/filter paths (queues: `JobService.listJobs` → `filteredJobs` → `displayedJobs`; all-work: `WorkService.getWork` → lossy `workToJob` → `worksAsJobs` **bypassing filters entirely**, `jobs.component.ts:528-543` / `:614-642` / `:262-267`) with ONE `JobsPageStore` (single fetch + single filter pipeline), so every view mode is a projection of the same state. This structurally eliminates the dead-filter class (P5.1: source/agent/show-deleted no-ops in all-work) instead of patching it per-control.

## Shared Context (true at phase start)

- Page: `frontend/src/app/pages/jobs/jobs.component.ts` (1,252 LOC, 6 injected deps incl. raw `HttpClient` at `:95`; filter state in `filters` signal `:117`, `filteredJobs` `:230-249`, `displayedJobs` `:262-267`, `workToJob` `:292-317`).
- `GET /api/jobs` supports `status` (comma-multi, ≤20 tokens → 400), `project_id`, `queue_id` (requires project), `include_deleted`, `job_types`, `mission_id` (canonical; `instance_id` deprecated alias); `limit` clamps 1..100, **FE currently sends none → BE default 50** (`daemon/routers/jobs_crud.py:638-723`, `constants.py:17/20`). No `source`/`agent_id` server-side filters (BE ignores them — gap-e5). `total` = page length (gap-c).
- `GET /api/work` (`daemon/routers/work.py:109-191`) has **no pagination at all**; `WorkRecord` carries `started_at`/`completed_at`/`result_summary`/`error` — i.e. the all-work Timeline data EXISTS on the wire; `workToJob` discards it (`:297-298`).
- Known BE defect (external): `status=settled,failed` combos drop rows (M3 predicate, `work_resolver.py:602-683`) — owned by arc `fix/jobs-status-combo-filter`. Not designed around here.
- Existing spec: `jobs.component.spec.ts` (2,085 lines, ≈40 describes) pins `root_only=false`, project persistence 1–6, SSE liveness propagation, DLQ retry-all. These pins must stay green.
- Worktree lacks `node_modules` → `npm ci` once before any verify command.

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/pages/jobs/jobs-page.store.ts` (+ `.spec.ts`) | **NEW** — the store |
| `frontend/src/app/models/jobs-filter-state.model.ts` (+ `.spec.ts`) | **NEW** — pure filter-state model |
| `frontend/src/app/pages/jobs/jobs.component.ts` | Modify — delegate fetch/filter/patch to store; delete dual path |
| `frontend/src/app/services/job.service.ts` | Modify — `listJobs` always sends `limit=100` (max clamp); document BE default-50 trap |
| `frontend/src/app/services/work.service.ts` | Modify — row-parity mapping (or new pure `workToJob` in a model file so it is spec-able) |
| `frontend/src/app/models/work.model.ts` | Modify if mapping moves here |
| `frontend/src/app/pages/jobs/jobs.component.spec.ts` | Modify — migration/deletion pins ONLY (dual-path removal, store delegation); all NEW pins land in NEW spec files; 2,085-line file is frozen as archive per the P6 split-or-decline default (see Test Strategy) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `JobsFilterState` pure model: `{ status: string[], source, agent_id, project_id, queue_id, include_deleted, view_mode }` + normalize/empty-state helpers, URL-ready shape (serialize/parse stubs, wired to URL in Phase 5) | none | Model spec passes: parse→serialize round-trip; unknown values tolerated; jest green |
| 2 | Create `JobsPageStore`: signals for `jobs[]`, `works[]`, unified `filteredJobs` computed over the ACTIVE dataset regardless of view mode; fetch legs with per-leg `catchError → retain last data` (indicator pattern, `job-queue-indicator.component.ts:657-847`); degraded/error flags per leg | Task 1 | Store spec: filter change re-projects BOTH datasets from one pipeline |
| 3 | Move SSE patching into the store (`updateJobFromSse` equivalent): patch `jobs[]`/`works[]` **in place, order-preserving — never client-side re-sort** (merge-order rule; server `created_at DESC` order is authority) | Task 2 | Spec pin: SSE patch preserves array order; existing M3-terminal `completed_at` stamping pin stays green |
| 4 | Rewire `jobs.component.ts`: both view modes read `displayedJobs = store.filteredJobs`; delete `filteredJobs`/`worksAsJobs` dual path and `workToJob` from the component | Tasks 2-3 | Full existing spec suite passes; template-extraction audit (below) clean |
| 5 | `job.service.listJobs` sends `limit=100` explicitly; keep `listRecentJobs(10)` untouched (indicator feed) | none | Service spec pins the query string contains `limit=100` |
| 6 | Dead-filter resolution (OQ-3 default): source/agent filters apply **client-side over the fetched window in BOTH views**, labeled "window-scoped"; show-deleted hidden + inert in all-work with explanatory copy (BE has no deleted concept on `/api/work` — `_query_jobs` hides soft-deleted, `work_resolver.py:2144`) | Task 4 | Cross-seam invariant spec: EVERY rendered filter changes `filteredJobs()` output in BOTH view modes — or the control is absent with honest copy. Zero no-op controls. Template-source enumeration pin: every filter binding in `jobs.component.html` is enumerated (grep) and mapped 1:1 to a `JobsFilterState` key — the flagship criterion must be provable from TEMPLATE wiring, not from a store spec alone (F-5 class) |
| 7 | Row-parity fix: all-work mapping stops nulling `started_at`/`completed_at` (carries `result_summary` too); `message` stays undefined (honest gap — no BE surface carries report message content, gap-e6 note) with known follow-on effect on drawer (Phase 5) | Task 4 | Spec: all-work row Timeline fields populated; `work.service` parity pin added (mirror exists at `work.service.spec.ts:1-40` — pin real-service construction parity per mirror-parity rule) |
| 8 | Vocabulary: page sends canonical status names through one service path; BE-internal `queued,active` mapping (`job.service.ts:108-128`) stays service-internal — no page-visible vocabulary change yet (display sweep is Phase 3) | Task 4 | Grep: page template contains no BE-internal status strings |

## Dependencies

**Internal:** none (first phase).

**External:**
- **GATE-COMBO-FIX (version gate):** any behavior/fixture/preset that REQUIRES `settled+failed` combo correctness is gated on arc `fix/jobs-status-combo-filter` merged to `latest` AND deployed to the daemon serving this FE. Probe: `GET /api/jobs?status=settled,failed` returns ≥ rows of `?status=failed` (critical-notes live-measure: 2 vs 3). The pipeline itself ships ungated (it forwards combos verbatim; the defect is BE-side). Do NOT compensate FE-side.
- **needs-BE (noted, out of scope):** gap-e5 server-side `source`/`agent_id` filters; gap-b `/api/work` `limit`. Until then source/agent are window-scoped by design (D5).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Refactor breaks the 2,085-line spec pins (project persistence 1–6, `root_only=false`, SSE stamping) | High | Keep store API surface compatible with existing component call sites; run FULL existing suite per commit; pin old behavior before flipping (grep specs first) |
| Hidden consumers of `workToJob`/`filteredJobs` outside the page | Medium | Grep `frontend/src/app` for both symbols before deletion; drawer is a consumer — coordinate with Phase 5 task 4 |
| `limit=100` doubles payload vs silent-50 | Low | One-time cost, clamp is BE-enforced; measure response size once and record in phase notes |

## Test Strategy

Plain-TS logic specs, NO TestBed. **Spec-file placement:** all NEW pins land in NEW spec files (`jobs-page.store.spec.ts`, `jobs-filter-state.model.spec.ts`, …); `jobs.component.spec.ts` (2,085 lines) receives ONLY migration/deletion pins. **P6 split-or-decline (review item) on the 2,085-line monolith spec:** default = DECLINE (freeze as a migration/deletion-pin archive — no new describes inside it); SPLIT into per-model spec files is an explicit user-override candidate (plan-overview OQ table, R-7). (1) Store spec: fetch/filter/patch/degraded. (2) **Cross-seam invariant:** same `JobsFilterState` ⇒ same row set in queues and all-work mode. (3) Retain-last-data pins for EVERY degradation shape: fetch-error, empty-200, degraded flag — never a bare empty list that impersonates a healthy poll. (4) Merge-order pin: SSE patch never re-sorts. (5) Mirror-parity: `work.service.spec.ts` gains a real-construction parity pin (mirror exists; parity does not). **Template-extraction audit:** this phase rewires event/data bindings — diff-audit every deleted/added `(click)`/`(keydown)` hunk in `jobs.component.html` per invocation site (plain-TS specs cannot verify DOM bindings).

## Verification Commands

```bash
cd frontend && npm ci   # fresh worktrees lack node_modules
npx tsc --noEmit -p tsconfig.app.json
npx jest jobs-page.store --coverage=false
npx jest jobs-filter-state jobs.component work.service
npm run build   # 10 pre-existing warnings known — attribute any NEW warning vs that corpus
```

## Sizing

**M (2–4 days).** Largest behavioral refactor of the arc (dual-path deletion + store + parity), but mechanical: no new UI, no BE change; the existing spec suite is the safety net. Store + two model files ≈ 600–800 LOC incl. specs.

## Exit Criterion

Both view modes render exclusively through `store.filteredJobs()`; every rendered filter control demonstrably filters (or is honestly hidden); `limit=100` on the wire; all-work rows carry timeline fields; full jest suite + tsc + build green.
