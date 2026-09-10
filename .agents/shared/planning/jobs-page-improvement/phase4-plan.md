# Phase 4: Defer-Blocked Surfacing & Remediation Wiring

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A as C-shaped groundwork

## Objective

Answer "is anything stuck?" from the page root: a persistent, severity-graded defer-blocked banner with a one-click holders drill-down, remediation actions (`force-complete` / `resend-foreground`) wired inline behind two-stage confirmation, and honest degraded handling that kills the stale red-glow (P3.1–P3.3).

## Shared Context (true at phase start)

- `GET /api/queues/defer-blocked` (`daemon/routers/queues.py:599-659`) returns `DeferBlockResponse { defer_blocked, pending_count, holders[{instance_id, agent, status, since, kind ∈ paused|stalled|live}] }`; holder ordering paused > stalled > live (`queues.py:648-649`); **severity is a client-side conjunction**: AMBER if any paused/stalled, INFO if all live, RED anomaly = `pending_count > 0 && holders == []` (`queues.py:622-632`). Fail-closed (DB errors propagate — no degrade shape).
- Remediation endpoints EXIST and are already wrapped: `JobService.forceCompleteDeferHolder` / `resendForeground` (`job.service.ts:240-272`) — consumer today is only the header indicator.
- Page today: defer data appears ONLY inside System-Cleanup dialog composition (`refreshBadStateCount` `:560-590`, raw `HttpClient` preflight + defer endpoint); preflight failures silently swallowed (`:587-589`) leaving a stale red-glow; FE composes `defer_blocked_count` with holder kinds because **preflight does not emit holder kinds** (`cleanup-preflight.model.ts:10-30`).
- `models/defer-blocked.model.ts` (177 LOC) already carries holder types + severity/tooltip/action helpers, with an existing spec — reuse, don't reinvent.
- Phases 1–2 landed: per-leg retain-last-data fetch discipline and the single poll tick.

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/pages/jobs/defer-holders-panel/` (ts + html + scss + `.spec.ts`) | **NEW** — banner + inline holders panel (page-scoped) |
| `frontend/src/app/pages/jobs/jobs.component.ts` / `.html` | Modify — mount banner, wire panel + actions, join poll tick |
| `frontend/src/app/services/job.service.ts` | Reuse only (force-complete/resend already wrapped) — no endpoint change |
| `frontend/src/app/models/defer-blocked.model.ts` | Extend — page-banner severity mapping helpers if missing |
| `frontend/src/app/components/confirm-dialog/` | Reuse — two-stage confirm for destructive actions |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Page-level banner under the filter bar: severity via existing `defer-blocked.model.ts` conjunction (AMBER / INFO / RED-anomaly); hidden only when no data AND no anomaly; copy states what defer-blocked means (queue admission is waiting on a holder) | P2 poll tick | Spec: severity mapping table pinned for all holder-kind permutations incl. RED-anomaly |
| 2 | "Review holders →" opens the inline holders panel — **defer-blocked visible ≤1 interaction from page root**: load page → banner visible; one click → holder list (instance, agent, kind, since; paused > stalled > live order) | Task 1 | Spec: panel opens from banner event; holder ordering pinned; empty-holders + pending>0 renders RED-anomaly copy |
| 3 | Per-holder actions `force-complete` / `resend-foreground` wired inline behind TWO-STAGE ConfirmDialog (reuse cleanup-dialog gravity: name the holder, state irreversibility, require explicit confirm) — OQ-8 default (D4) | Task 2 | Spec: service action fires ONLY after confirm resolves; cancel path fires nothing; success refreshes holders leg |
| 4 | Retain-last-data for the defer + preflight legs: fetch failure retains last payload + degraded flag on the banner ("last check failed — showing retained state"); replaces the silent swallow at `:587-589` | Tasks 1-2 | Retain-last-data pins: fetch-error and empty-200 shapes; stale-red-glow scenario spec (failure ⇒ degraded note, never silent glow) |
| 5 | Defer leg joins the Phase-2 poll tick with per-leg `catchError` (forkJoin discipline port, `job-queue-indicator.component.ts:657-735`); drawer/modal pause applies to this leg too | P2 task 5 | Spec: tick gate consults the defer leg; per-leg failure doesn't kill sibling legs |
| 6 | System-Cleanup dialog keeps working unchanged (it consumes the same service data) — no regression to `cleanup-preflight.model.spec.ts` verbatim-pinned copy | — | Existing cleanup dialog pins green; dialog data contract untouched |

## Dependencies

**Internal:** Phase 1 (service fetch discipline), Phase 2 (poll tick + visibility/modal gating — the defer leg rides the same tick). If phases are re-ordered, tasks 4–5 degrade to a standalone interval — acceptable but noted.

**External:** none new. **GATE-COMBO-FIX** not implicated. **needs-BE:** none — everything here is already-served (`defer-blocked` endpoint + both action endpoints exist; api findings §5). Notably: preflight not emitting holder kinds is already FE-composed and stays so.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Destructive action gravity (`force-complete` drops a real task) | High | Two-stage confirm with holder identity + irreversibility copy; action button disabled until confirm dialog resolves; OQ-8 documents the ownership decision — link-out alternative is the fallback if user rejects inline |
| RED-anomaly state nags without actionable remediation | Medium | Anomaly copy explains the inconsistency (pending rows but zero holders) and offers "Open System Cleanup" (existing surface) — never a bare alarm |
| Endpoint fail-closed (no degrade shape) turns a DB hiccup into a hard error | Low | Per-leg catchError → retain-last + degraded banner; banner never claims freshness it doesn't have (`lastFetchAt` discipline port) |

## Test Strategy

Severity conjunction: extend `defer-blocked.model.spec.ts` with page-banner mappings (all kind permutations). Panel logic spec: open/close, ordering, action dispatch gated on confirm. Retain-last-data pins for the defer/preflight legs (fetch-error, empty-200, fail-closed error). Cross-seam invariant: banner severity identical whether data arrives via poll tick or manual refresh. **Template-extraction audit:** banner + panel + confirm wiring add `(click)`/`(keydown)` hunks — diff-audit each invocation site (Enter `preventDefault` guard applies inside any `mat-menu` context, F2 trap).

## Verification Commands

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
npx jest defer-holders-panel defer-blocked.model jobs.component cleanup-preflight.model
npm run build
```

## Sizing

**M (2–4 days).** New page-scoped component + action wiring + confirm UX; data layer pre-exists entirely (service methods + severity helpers), which keeps it M rather than L.

## Exit Criterion

With a defer-blocked fleet, the banner is visible on page load with correct severity; holders drill-down is one click away; force-complete/resend execute only behind two-stage confirm; a failed preflight shows retained data + degraded note instead of a stale glow.
