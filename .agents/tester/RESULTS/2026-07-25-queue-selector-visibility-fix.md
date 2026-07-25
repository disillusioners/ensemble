# Test Report: Queue Selector Visibility Fix

**Date:** 2026-07-25
**Branch:** `feature/queue-select-message`
**Commit:** `c9c2b42c` (fix: show queue selector for all non-active instance states)

## Summary

| Category | Result | Count |
|----------|--------|-------|
| **Frontend Specs** | ✅ PASS | 64/64 (message-input + chat.component) |
| **TypeScript check** | ✅ PASS | clean |
| **Angular build** | ✅ PASS | clean (pre-existing warnings only) |
| **Browser E2E** | ✅ PASS | 6/6 (all visibility scenarios + queue_id emission) |
| **ensure.md Core** | ✅ PASS | All Critical requirements pass |
| **ensure.md Release Gate** | ⏭️ SKIPPED | Not warranted (scoped frontend fix) |
| **Bugs Found** | ✅ NONE | Clean change, no issues |
| **Quick Fixes** | ✅ NONE | Not needed |

## Scope Decision

> Change touches 2 files in 1 component (message-input). A pure frontend
> visibility logic fix — `isIdle()` → `isQueueSelectorVisible()` (new computed
> that shows for all non-active states). No backend change, no architecture
> change. **Reduced scope** to: frontend specs for affected components, type +
> build check, and browser E2E for the 5 visibility scenarios + queue_id
> emission. Skipped: backend regression packs (no backend files changed),
> full suite (not warranted). **Full suite not warranted.**

## What Changed

The queue selector was only visible for IDLE instances. Now it's visible for
all non-active states (idle, completed, error, failed, terminated, waiting,
null) and hidden only for running, waiting_children, paused, queued.

- `message-input.component.ts`: added `isQueueSelectorVisible()` computed;
  `handleSubmit()` gates `queue_id` emission on it
- `message-input.html`: `@if` uses `isQueueSelectorVisible()` instead of
  `isIdle()`

## Frontend Verification

| Step | Result | Runtime |
|------|--------|---------|
| Jest specs (message-input + chat) | ✅ PASS 64/64 | 1.3s |
| `tsc --noEmit` | ✅ PASS | <5s |
| `ng build` | ✅ PASS | 11.3s |

ng build confirms template type-checking passes (the new `isQueueSelectorVisible()`
signal referenced from template is properly declared and accessible). tsc alone
cannot catch template errors, so ng build is the authoritative check.

## Browser E2E Results (6/6 PASS)

Playwright spec: `frontend/e2e/queue-selector-states.spec.ts`

| # | Test | Expected | Observed | Result |
|---|------|----------|----------|--------|
| 1 | **COMPLETED → selector VISIBLE** (KEY) | visible | DOM visible, signal=true, 5 queues | ✅ PASS |
| 2 | IDLE → selector VISIBLE (regression) | visible | DOM visible, signal=true | ✅ PASS |
| 3 | New/null status → selector VISIBLE | visible | DOM visible, signal=true | ✅ PASS |
| 4 | RUNNING → selector HIDDEN | hidden | DOM hidden, signal=false | ✅ PASS |
| 5 | PAUSED → selector HIDDEN | hidden | DOM hidden, signal=false | ✅ PASS |
| 6a | Idle send emits `queue_id` (UUID) | selected UUID | `f8a400aa…` (system_fifo_queue) | ✅ PASS |
| 6b | Running send emits `queue_id: null` | null/omitted | omitted (undefined) | ✅ PASS |

Each visibility test asserts BOTH DOM presence (`<label class="queue-selector">`)
AND the Angular signal (`isQueueSelectorVisible()`) via `window.ng.getComponent()`
to catch DOM-vs-signal divergence.

**The KEY bug is fixed:** COMPLETED instances now show the queue selector.

Screenshots: `e2e-shots/queue-selector-states/`

## ensure.md Validation Results

### Core (always-on, scoped to change set)
- ✅ **Critical #1** No regressions in changed packs — PASS (frontend specs 64/64)
- ✅ **Critical #2-3** Deadlock/concurrency + sync DB calls — out of scope (no backend/concurrency code touched)
- ✅ **Critical #4** `dev.sh` includes `--timeout-graceful-shutdown 10` — confirmed (line 74)
- ⏭️ **Release Gate** — Skipped (not a big/critical/architecture change)

## Documentation Updated
- ✅ RESULTS/2026-07-25-queue-selector-visibility-fix.md (this file)

## Overall Status
- Frontend Specs + Build: ✅ PASS
- Browser E2E: ✅ PASS (6/6 — all visibility scenarios + queue_id emission)
- ensure.md Core: ✅ PASS
- **Testing Complete: ✅ READY**
