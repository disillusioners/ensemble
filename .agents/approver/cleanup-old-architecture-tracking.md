# Tracking: Cleanup Old/Legacy Architecture Parts

## Iteration 001 — 2026-06-22 17:20

**Verdict: REJECTED**

### Resolution (Iteration 002 — 2026-06-22)

Both blocking issues addressed:

1. **Task 5.7** — Now explicit BEHAVIORAL change: threads `status` from `_emit_terminal_via_bus` → `_retrigger_parent_finalize(target_id, terminal_status=status)` → `_finalize_job`. Conservative "any child error → parent error" semantics (mirrors CM's `_determine_terminal_status`). Per-parent error accumulation tracking specified. `TestBusSoleAuthority` scenario (4) verifies before CM deletion. ✅

2. **Task 1.1** — Now specifies 4-step sequential locking: (1) generation mutation outside lock (CPython atomicity), (2) per-parent lock for INSERT only, (3) release, (4) per-task lock for cache only. "Locks are sequential, never held simultaneously." Deadlock risk: "N/A — Eliminated by design." ✅

**VERDICT: APPROVED**

1. **Phase 5 Task 5.7: `_retrigger_parent_finalize` hardcodes `COMPLETED` status — error-path gap not resolved**
   - `child_reports.py:496` calls `observer._finalize_job(job, instance_id, InstanceStatus.COMPLETED.value, error=None)` unconditionally
   - `_emit_terminal_via_bus` (child_reports.py:223) receives a `status` parameter ("completed", "error", "terminated") but does NOT pass it through to `_retrigger_parent_finalize`
   - When a child errors on the bus path, `error_reporting.py:673` calls `_emit_terminal_via_bus(status="error")`, but the bus `status` is only used in the `Outcome` for logging. The `_retrigger_parent_finalize` still calls `_finalize_job` with `COMPLETED`
   - After Phase 5 removes CM, `handle_correlation_complete` (which received `terminal_status="error"` from CM) is also gone. The bus path is the ONLY path. This means child errors are silently masked as successful parent completions
   - **Expected**: Plan must include a concrete task (not just "audit") to thread `status` from `_emit_terminal_via_bus` through `_retrigger_parent_finalize` to `_finalize_job`. This is a behavioral change that must happen during Phase 5, not deferred as "determine whether needed"

2. **Phase 1 Task 1.1: Lock ordering specification is incomplete**
   - The plan states "Preserve lock ordering: parent lock → task lock"
   - But the current code (dependency_bus.py:365-368) mutates `cm._generation` OUTSIDE the per-parent lock (relies on CPython dict atomicity), then acquires the per-parent lock only for the DB INSERT, then SEPARATELY acquires the per-task lock (L394) for the cache update
   - When Phase 1 moves generation to the bus, the plan must specify: (a) whether generation mutation stays outside the lock (relying on CPython atomicity) or moves inside the new per-parent lock, and (b) the exact interaction between per-parent lock and per-task lock in `watch()` (currently they are sequential, not nested)
   - **Expected**: Task 1.1 must specify the exact locking strategy: "Generation counter mutation uses CPython dict atomicity (outside lock), same as current CM pattern. Per-parent lock is acquired only for the DB INSERT, then released. Per-task lock is acquired separately for cache update. Locks are NEVER held simultaneously." Or if the plan intends nested locking, it must justify why and prove no deadlock cycle exists
