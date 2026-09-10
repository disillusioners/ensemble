# Phase 5: URL Filter State, Drawer Gate Fixes & Job Deep-Link

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A as C-shaped groundwork

## Objective

Make page state shareable and restorable: every filter + view mode binds to URL query params (back/forward works, filtered views are bookmarkable), `?job=<id>` opens the drawer directly, and the drawer stops lying — Result renders for `settled`/report rows, Timeline works for all-work rows, Message hides honestly where the wire has no content.

## Shared Context (true at phase start)

- Phase 1 landed `JobsFilterState` with a URL-ready serialize/parse shape (Task 1 was built for this phase); the store is the single source of filter truth.
- Today: **no `ActivatedRoute`/`queryParamMap` anywhere in `jobs.component.ts`** — filters are not deep-linkable; only project id (`localStorage['job-page-selected-project']`) + view mode (`localStorage['job-page-view-mode']`) survive reload; drawer opens only from list state (no `/jobs/:id` route, no query-param handling).
- Drawer gates (bug-grade, `job-detail-drawer.component.html`): Result renders ONLY when `status === 'completed'` (`:164`) — `settled` rows and all-work `report` rows never show their result even when `result_summary` is present; Message never renders for all-work rows (`workToJob` set `message: undefined` — Phase 1 task 7 kept that as an honest gap); Timeline was broken for all-work rows — **already repaired data-side** by Phase 1 task 7 (started/completed carried from `WorkRecord`).
- SSE: `JobSseService` is a single-job singleton (`job-sse.service.ts:19`); drawer subscribes only when the selected job is non-terminal (`:1184-1192`). List-level SSE does not exist (gap-e1). Patches must keep order-preserving in-place semantics.

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/pages/jobs/jobs-url-state.model.ts` (+ `.spec.ts`) | **NEW** — pure query-param ↔ filter-state codec |
| `frontend/src/app/pages/jobs/jobs.component.ts` / `.html` | Modify — `ActivatedRoute` binding, `?job=` handling, router writes |
| `frontend/src/app/components/job-detail-drawer/` | Modify — Result gate, Timeline source, Message honest copy (+ spec extensions) |
| `frontend/src/app/models/jobs-filter-state.model.ts` | Extend if codec needs richer value types |
| `frontend/src/app/app.routes.ts` | Unchanged — NO `/jobs/:id` route (D6: separate route only if requested, OQ-6) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Codec model: filter state ↔ query params (`status` comma-joined, `source`, `agent`, `project`, `queue`, `deleted`, `view`, `job`); unknown params ignored; invalid values fall back to defaults; empty-string tolerated | P1 JobsFilterState | Round-trip spec; hostile-input fixtures (unknown status token, `?job=`, duplicated params) |
| 2 | Bind: read `queryParamMap` on init (URL wins over localStorage; localStorage seeds a `replaceUrl` write when URL is bare — one-time migration, then URL authoritative); filter changes write via `router.navigate` with `queryParamsHandling: 'merge'` | Tasks 1, P1 store | Spec: back/forward restores full filter state; project persistence scenarios 1–6 still green (localStorage now the fallback layer) |
| 3 | `?job=<id>` deep-link: on load (and on param change), open drawer if the job is in the current window; if absent, attempt single-job fetch via the existing BE endpoint (`GET /api/jobs/{id}` exists, `jobs_crud.py:606-615` — VERIFY a FE `JobService` wrapper exists first; if none, add one small GET method); 404 ⇒ honest "job not found" drawer state | Task 2 | Spec: in-window open, out-of-window fetch, 404 honest state |
| 4 | Drawer Result gate: render when `result_summary` present — drop the `status === 'completed'`-only gate (covers `settled` + report rows); keep empty-result hidden | — | Drawer spec: settled row with `result_summary` shows Result; completed without result shows nothing |
| 5 | Drawer Timeline: all rows render created/started/completed from real fields (Phase 1 parity); duration computable when started+completed present | Task 4 | Drawer spec: all-work row Timeline shows all three + duration |
| 6 | Drawer Message for report rows: hidden-by-design with honest copy ("report rows do not carry message content") — gap-e6 is a BE fact, not a bug | Task 4 | Spec pins the honest copy; card preview fallback unchanged |
| 7 | SSE contract re-pin: patches stay in-place + order-preserving; per-job stream behavior unchanged (drawer-open AND non-terminal); the Phase-2 modal-pause gate still releases SSE when drawer closes | P2 | Existing SSE pins green + explicit order-preservation pin |

## Dependencies

**Internal:** Phase 1 (filter-state model, row parity), Phase 2 (drawer-open pause interacts with `?job=` open), Phase 3 (grouped list is the visual context the drawer opens from). Phases 1→5 ordering matters for task 4/5 (parity fields must exist).

**External:**
- **GATE-COMBO-FIX:** implicated at the MARGIN — a shared/bookmarked URL with `status=settled,failed` returns defect-truncated rows until the parallel arc lands and deploys. The codec must NOT silently rewrite combos; document the gate in the codec spec and mark the combo fixture **gated (GATE-COMBO-FIX)**.
- **needs-BE:** none new. Single-job GET exists; if the FE wrapper is missing it is a one-method service addition against an existing endpoint (not a BE ask).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| URL↔localStorage migration surprises existing users (persisted project vanishing) | Medium | One-time seed: bare URL ⇒ hydrate from localStorage + `replaceUrl`; after that URL wins; scenario specs 1–6 extended with URL-precedence cases |
| `?job=` id pointing at deleted/other-project job | Low | 404/not-in-project ⇒ honest drawer state + banner stays; never open an empty drawer silently |
| Router writes on every keystroke of searchable-select | Low | Debounce URL writes (~300ms) or write on selection-close; state lives in the store regardless |
| Drawer gate changes break standalone drawer consumers | Medium | Drawer is reused standalone (research §1.1) — grep consumers first; extend `job-detail-drawer` spec before flipping gates |

## Test Strategy

Codec: round-trip + hostile-input specs. Navigation logic-mirror: init restore, merge-write, back/forward (pure router-double pattern per house convention). Drawer: gate-flip specs (settled/report Result, Timeline duration, honest Message copy) added BEFORE flipping (grep-pin old behavior first). Deep-link: three-state spec (in-window / fetch / 404). GATE-COMBO-FIX fixture marked and skipped-with-reason until the parallel arc deploys. **Template-extraction audit:** drawer template gates + router binding hunks — diff-audit `(click)`/`(keydown)` changes; plain-TS specs cannot see the template.

## Verification Commands

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
npx jest jobs-url-state jobs.component job-detail-drawer
npm run build
```

## Sizing

**M (2–4 days).** Three S-sized workstreams (URL codec, drawer gates, deep-link) bundled into one phase to share the template-audit pass and the router integration; codec + specs dominate.

## Exit Criterion

A filtered view survives copy-paste-URL into a fresh tab; back/forward restores state; `?job=<id>` opens the drawer (honest fallbacks otherwise); `settled` and report rows show their results; all-work drawer Timelines are complete.
