# Phase 2: Window Honesty, Virtualized Render & Poll Discipline

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A as C-shaped groundwork

## Objective

Make the page's data window honest and cheap: the silent newest-50 cap becomes an explicit `limit=100` window with a full-window honesty banner; all-work gets a hard render guard against unbounded growth; the full-array `@for` render is replaced by `cdk-virtual-scroll`; the 30s poll pauses when it should (hidden tab, drawer/modal open) and refreshes on refocus.

## Shared Context (true at phase start)

- Phase 1 landed: `JobsPageStore` owns fetch/filter/patch; `limit=100` already on the `/api/jobs` wire; retain-last-data discipline exists per fetch leg.
- BE truth: `/api/jobs` has NO offset/cursor and NO real total (`JobListResponse.total = len(page)`, `jobs_crud.py:866-869`) — a "Load more" would re-fetch the same page (gap-b/c). `/api/work` has no pagination at all. Honesty surfaces are the only correct FE-only posture (non-goal #8: no fake pagination, no silent truncation).
- Current poll: `setInterval(30000)` mode-aware, skips only in-flight fetch, **no `visibilitychange` anywhere** (`jobs.component.ts:655-670`), no drawer/dialog pause. Render: full-array `@for` (`html:261-272`), no guard.
- Drawer + dialogs mutate job state; polling underneath an open action surface risks mid-action list replacement.

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/pages/jobs/jobs-window.model.ts` (+ `.spec.ts`) | **NEW** — window policy (pure) |
| `frontend/src/app/pages/jobs/jobs-poll.model.ts` (+ `.spec.ts`) | **NEW** — poll gate policy (pure): shouldTick(visible, overlayOpen, inFlight) |
| `frontend/src/app/pages/jobs/jobs.component.ts` / `.html` / `.scss` | Modify — banner mount, virtual scroll, poll rewiring |
| `frontend/src/app/pages/jobs/jobs-page.store.ts` | Modify — expose window/degraded signals consumed by banner |
| `frontend/package.json` | Verify `@angular/cdk` present (ScrollingModule) |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Window model: `windowIsFull(rowCount, limit=100)` → banner state; banner copy: "Showing newest 100 · the list may continue — refine filters to narrow" + manual Reload button; NO claim of completeness (we cannot know — gap-c) | P1 store | Model spec: exactly-at-100 shows banner; 99 does not; copy consts pinned |
| 2 | Honesty banner in template (both view modes) driven by window model + store degraded flag (degraded ⇒ "last refresh failed — showing retained data", never silent) | Task 1 | Template renders banner variant per state; audit hunks recorded |
| 3 | All-work cap guard: pure `renderGuard(rows)` — refuse render >1000 rows, show explicit truncation notice with count; never silently slice | none | Spec with fixtures AT (1000) and PAST (1001) the cap |
| 4 | `cdk-virtual-scroll` over the flat projection (itemSize tuned to card min-height); `track` by `job_id`; grouping (Phase 3) must render as a flattened (header\|row) item list — structure the scroll source as `readonly items: WindowItem[]` from day 1 | P1 | Render capped at viewport; expand/collapse a card inside the virtual viewport keeps state |
| 5 | Poll gate model: `shouldTick = tabVisible && !drawerOpen && !modalOpen && !fetchInFlight`; `document.visibilitychange` listener: pause hidden, **immediate refresh on re-focus**; keep 30s cadence (OQ-5 default, D3) | P1 store | Model spec covers all gate combinations; component wires listener + teardown in `ngOnDestroy` |
| 6 | Degraded-state sweep for BOTH views: fetch failure retains last rows + degraded banner (extends P1 discipline to the whole-work leg; kills the snackbar-only lossy failure `:628-641`) | P1 store | Retain-last-data pins: fetch-error, empty-200, degraded:true shapes |
| 7 | Keep the panel's freshness contract intact: header indicator (`job-queue-indicator`, 8s forkJoin) is NOT touched (non-goal #1) | — | Grep: zero edits under `components/job-queue-indicator/`, `components/job-queue-panel/` |

## Dependencies

**Internal:** Phase 1 (store, limit=100, retain-last-data). Phases 3–6 depend on this phase's window model + virtual-scroll item list shape.

**External:** none new. **GATE-COMBO-FIX** not implicated (no settled-involving combo logic here). **needs-BE (noted, out of scope):** gap-b (`/api/work` limit), gap-c (real total/has_more) — the store's window shape is designed so real paging slots in later without UI rework (D2).

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Grouped virtual scroll (Phase 3) is the arc's fiddliest bit | Medium | This phase defines the flattened `WindowItem[]` scroll source NOW so Phase 3 adds header items without re-plumbing |
| cdk ScrollingModule import bumps bundle/component budgets | Medium | Build after import; classify every NEW warning vs the known-10 corpus (6 scss budgets, 1 bundle, NG8113, 2 Sass deprecations); budget breaches are a visible diff, not noise |
| Refocus-immediate-refresh storms (rapid tab switching) | Low | Debounce refocus refresh (≥2s since last fetch); in-flight skip already exists |
| Expandable card state lost on virtual recycle | Medium | Expansion state keyed by `job_id` in the store/component Set (panel `userTouchedInstances` pattern), not DOM-local |

## Test Strategy

Window + poll + guard models are pure TS — full logic specs with fixtures AT and PAST caps (100 / 1001). Poll gate: truth-table spec + a component-level pin that `setInterval` callback consults `shouldTick`. Retain-last-data pins for every degradation shape (fetch-error, empty-200, degraded). Cross-seam invariant: window banner state identical for both view modes at equal rowCount. **Template-extraction audit:** banner + virtual-scroll template is new binding surface — diff-audit all added `(click)`/`(keydown)` hunks (Reload button, card expand inside viewport). Merge-order rule: virtual scroll reorders nothing client-side; server order remains authority.

## Verification Commands

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
npx jest jobs-window jobs-poll jobs.component
npm run build   # attribute new warnings vs known-10
```

## Sizing

**M (2–4 days).** Three orthogonal workstreams (banner S, virtualization M, poll discipline S) sharing one template-audit pass; virtualization is the only genuinely new integration.

## Exit Criterion

At 100 returned rows the banner is visible; a 1001-row all-work payload cannot render silently; the poll provably pauses on hidden tab and open drawer/modal and refreshes on refocus; full render path goes through the virtual list.
