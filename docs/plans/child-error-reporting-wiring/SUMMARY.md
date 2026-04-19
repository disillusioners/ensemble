# Child Error Reporting — Wire Dead Code into Worker Pool Architecture

## Problem

`InstanceManager._send_error_report()` is fully implemented but **never called**. It was written for the old `_process_queue` architecture (documented in `docs/child-agent-error-reporting.md`). After the rewrite to `worker_pool` / `task_processor`, **no one wired up the callers**.

**Consequence**: When a child instance fails permanently (any non-recoverable error), the parent instance is **never notified**. The parent stays stuck in `WAITING_CHILDREN` status forever with `waiting_for` counter never decremented.

## Scope

All **18 non-recoverable failure paths** that permanently fail a child task are affected. See `00-error-catalog.md` for the full catalog.

## Solution

Wire `_send_error_report()` into every code path that permanently fails a child task, plus decrement the parent's `waiting_for` counter.

## Revised Phase Order

| Phase | Description | Dependency |
|-------|-------------|------------|
| 0 + 4 | **Parallel**: Fix `_send_error_report()` (atomic tx, hierarchy delete, cascade, null guards) + Fix `_event_bus` bug | None — can be parallel |
| 1 + 2 | **Parallel**: Wire TaskProcessor (async) + Wire Worker (sync/fallback) | Phase 0 complete |
| 3 | Wire StaleTaskRecovery via callback | Phase 0 complete |

## Key Council Findings

1. **Phase 0 is incomplete as described** — missing 4 critical operations: atomic transaction, instance hierarchy delete, cascade check, `_live_hub` null guard. The method needs a near-complete rewrite, not just additions.

2. **Double-reporting risk is real** — the plan initially assumed both TaskProcessor AND Worker would call `_send_error_report()` for the same error. Resolution: TaskProcessor reports (async, natural), Worker only reports for cancellation and pre-processing errors that bypass TaskProcessor.

3. **Phase 0 + 4 are parallel** — both are critical preconditions for Phase 1/2. Do them together first.

4. **Queue-check dedup is sufficient** — no need for DB-level flag. The existing dedup guard works.

## Open Questions

1. **Parent cascade on error**: When last child fails, transition parent to `RUNNING` (align with success path) or `ERROR`?
2. **Error report format**: Plain text message is sufficient — parent's agent soul/rule handles it.
3. **`_live_hub` timing**: Verify `_live_hub` is initialized before first task can fail.

See `01-implementation-plan.md` for revised phase details and `03-risks.md` for updated risk analysis.
