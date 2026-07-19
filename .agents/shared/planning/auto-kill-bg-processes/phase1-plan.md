# Phase 1: Proc Auto-Kill (Two-Tier Cleanup)

> **Revision 2 (2026-07-18)** — incorporates review items C2 (path), C4 (tree_ids init), C5 (Tier 1 self-cleanup), M2 (sequential), M3 (repo access + to_thread).

## Objective

Wire the existing `BackgroundProcessManager.cleanup_instance` into a two-tier cleanup model inside `_dispatch_instance_post_commit_side_effects` (Tier 1 always + Tier 2 root-gated tree sweep), plus a daemon-shutdown sweep via new `cleanup_all()`. **No bash registry, no bash changes, no CancelledError fix** — those are Phase 2.

## Coupling

- **Depends on:** None (root phase)
- **Coupling type:** —
- **Shared files with Phase 2:** `job_feedback_observer.py` (Phase 2 adds adjacent bash cleanup lines within the SAME Tier 1/Tier 2 blocks), `daemon/manager.py` (Phase 2 adds adjacent bash cleanup in `shutdown()`), `proc_tools.py` (Phase 2 touches `bash.py`, not here)
- **Shared APIs/interfaces:** `cleanup_instance(instance_id)`, `cleanup_all()` (both used here; Phase 2 mirrors for bash)
- **Why tight coupling with Phase 2:** Phase 1 and Phase 2 edit the SAME LINES of the SAME FUNCTIONS. Phase 2 ADDS adjacent lines to Phase 1's code. **Must be strictly sequential** (D12): Phase 2 starts only after Phase 1 merges.

## Context

- Pre-existing cleanup: `terminate_instance` (instance_lifecycle.py:1461) handles explicit DELETE/cancel and cascades. **Untouched in this phase.**
- Gap: COMPLETED / ERROR / FAILED paths reach `_dispatch_instance_post_commit_side_effects` (job_feedback_observer.py:2391) and do NO proc cleanup.
- Gap: `manager.shutdown()` (daemon/manager.py:5536) does NO proc cleanup.
- Gap: `cleanup_all()` does not exist on `BackgroundProcessManager` (only `cleanup_instance`).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `cleanup_all()` to `BackgroundProcessManager` | New async method that iterates all instance buckets and calls `cleanup_instance(iid)` for each. Snapshot the instance_id list under lock, release lock, then iterate (since `cleanup_instance` re-acquires the lock). Return count of instances cleaned. Idempotent. | `daemon/tools/proc_tools.py` (~line 1085, after `cleanup_instance`) |
| 2 | Add TWO-TIER proc cleanup in post-commit dispatcher | In `_dispatch_instance_post_commit_side_effects`, add Tier 1 (always, for `instance_id`) + Tier 2 (root-gated, for descendants). See implementation detail below. Initialize `tree_ids = []` OUTSIDE try (C4). Repo via `self._instance_manager._instance_repository` + `to_thread` (M3). Best-effort try/except per call. | `daemon/services/job_feedback_observer.py:2391-2488` |
| 3 | Add proc cleanup to `manager.shutdown()` | Add a new step early in `shutdown()` (before `shutdown_worker_pool`), wrapped in try/except. Call `cleanup_all()` on the proc manager. Log summary count. | `daemon/manager.py:5536-5605` |
| 4 | Unit test: `cleanup_all()` empties all buckets | Test that `cleanup_all()` after spawning processes across multiple instances results in empty `_processes` dict and all process groups killed. | `tests/.../test_proc_tools.py` |
| 5 | Unit test: Tier 1 always fires for any terminal instance | Simulate a CHILD instance (`parent_id != None`) reaching the dispatcher. Assert `cleanup_instance(instance_id)` IS called for the child (Tier 1). Assert `get_tree_ids` is NOT called (Tier 2 skipped). | `tests/.../test_job_feedback_observer.py` |
| 6 | Unit test: Tier 2 fires only for root | Simulate a ROOT instance (`parent_id is None`). Assert `cleanup_instance(instance_id)` called (Tier 1), `get_tree_ids` called (Tier 2), and `cleanup_instance(iid)` called for each descendant but NOT for `instance_id` again (Tier 2 skips root — Tier 1 handled it). | `tests/.../test_job_feedback_observer.py` |
| 7 | Unit test: `get_tree_ids` failure is isolated | Mock `get_tree_ids` to raise. Assert `tree_ids` stays `[]` (C4), Tier 2 loop is a no-op, Tier 1 still ran, other side-effects (SSE, CompletionRegistry) still fire, WARNING logged. | `tests/.../test_job_feedback_observer.py` |
| 8 | Unit test: `manager.shutdown()` calls `cleanup_all()` | Mock the proc manager, assert `cleanup_all()` is invoked during shutdown and failures don't propagate. | `tests/.../test_manager_shutdown.py` |
| 9 | Regression: existing terminate_instance tests pass | Run the full `terminate_instance` test suite to confirm no double-cleanup or race introduced. | existing tests |

## Task 1 — `cleanup_all()` Implementation Detail

```python
# daemon/tools/proc_tools.py, after cleanup_instance (~line 1085)

async def cleanup_all(self) -> int:
    """Kill ALL background processes across ALL instances.

    Daemon-shutdown sweep. Idempotent: each cleanup_instance pops its
    bucket atomically, so concurrent/ repeated calls are safe.

    Returns:
        Number of instance buckets that were cleaned (0 if none).
    """
    # Snapshot instance ids under lock; cleanup_instance re-acquires
    # the lock per instance, so we don't hold it across the loop.
    async with self._lock:
        instance_ids = list(self._processes.keys())
    cleaned = 0
    for iid in instance_ids:
        try:
            await self.cleanup_instance(iid)
            cleaned += 1
        except Exception as e:
            logger.warning(
                f"cleanup_all: cleanup_instance failed for {iid[:8]}: "
                f"{type(e).__name__}: {e}"
            )
    if cleaned:
        logger.info(f"cleanup_all: cleaned {cleaned} instance bucket(s)")
    return cleaned
```

**Why snapshot under lock then release:** `cleanup_instance` itself acquires `self._lock`. Holding the lock across the iteration would deadlock (asyncio.Lock is not reentrant). Snapshot the keys, release, iterate.

## Task 2 — Two-Tier Post-Commit Dispatcher Hook Detail (C4, C5, M3)

```python
# daemon/services/job_feedback_observer.py
# Inside _dispatch_instance_post_commit_side_effects (line 2391)
# Add as the FIRST side-effect step (before SSE/CompletionRegistry/lifecycle):

import asyncio
from daemon.tools.proc_tools import get_background_process_manager

# --- TIER 1: ALWAYS clean THIS instance's own processes ---
# Runs for ANY terminal instance (root OR child). Closes the per-child
# leak window: a child that COMPLETED no longer leaks its procs until the
# root finalizes. (C5 fix.)
try:
    proc_mgr = get_background_process_manager()
    await proc_mgr.cleanup_instance(instance_id)
except Exception as e:
    logger.warning(
        f"Tier-1 proc cleanup failed for {instance_id[:8]}: "
        f"{type(e).__name__}: {e}"
    )

# --- TIER 2: Root-gated tree sweep for DESCENDANTS ---
# Only when this is a ROOT instance (parent_id is None). Tier 1 already
# cleaned the root's own processes; Tier 2 sweeps descendants.
tree_ids: list[str] = []  # ← initialized OUTSIDE try (C4 — prevents NameError in Phase 2)
if parent_id is None:
    try:
        # M3: JobFeedbackObserver has NO self.repository. Access via manager.
        # get_tree_ids is SYNC (repository.py) — wrap in to_thread.
        instance_repository = getattr(self._instance_manager, "_instance_repository", None)
        if instance_repository is not None:
            tree_ids = await asyncio.to_thread(
                instance_repository.get_tree_ids, instance_id
            )
        else:
            logger.warning(
                f"Tier-2: no instance_repository on manager; skipping tree sweep "
                f"for root {instance_id[:8]}"
            )
    except Exception as e:
        logger.warning(
            f"Tier-2 get_tree_ids failed for root {instance_id[:8]}: "
            f"{type(e).__name__}: {e}"
        )
    # Phase 2 will add the parallel bash sweep inside this `if` block.
    for iid in tree_ids:
        if iid == instance_id:
            continue  # already cleaned in Tier 1
        try:
            await proc_mgr.cleanup_instance(iid)
        except Exception as e:
            logger.warning(
                f"Tier-2 proc cleanup failed for {iid[:8]}: "
                f"{type(e).__name__}: {e}"
            )

# ... existing Step 2 (SSE), Step 3 (CompletionRegistry), Step 4 (lifecycle event) ...
```

**Verification steps (M3):**
1. Confirm `_dispatch_instance_post_commit_side_effects` has access to `self._instance_manager` (JobFeedbackObserver stores it in `__init__`). If not, thread it.
2. Confirm `_instance_repository` is the correct attribute name on the manager (matches instance_lifecycle.py:1836 pattern `self._manager._instance_repository.get_tree_ids(...)`).
3. `get_tree_ids` signature is `get_tree_ids(self, root_id: str) -> list[str]` (sync, repository.py:246). `to_thread` is required — calling sync DB code inline blocks the event loop.

**Import:** Function-level import (`from daemon.tools.proc_tools import get_background_process_manager`) inside the method, matching the `terminate_instance` pattern at instance_lifecycle.py:1458 (avoids circular import risk).

## Task 3 — `manager.shutdown()` Hook Detail

```python
# daemon/manager.py, inside shutdown() (~line 5536)
# Add BEFORE shutdown_worker_pool (processes owned by workers should die first):

# Kill all background processes before tearing down workers.
try:
    from daemon.tools.proc_tools import get_background_process_manager
    cleaned = await get_background_process_manager().cleanup_all()
    if cleaned:
        logger.info(f"shutdown: killed background processes in {cleaned} instance(s)")
except Exception as e:
    logger.warning(f"shutdown: proc cleanup_all failed: {type(e).__name__}: {e}")

# ... existing shutdown steps: stop_sources, cancel_active_requests, ...
```

**Placement rationale:** Put proc kill as the FIRST shutdown step. Rationale: (1) processes are best-effort background work, (2) worker pool tasks may hold references, so killing the OS processes first avoids zombie references, (3) it's the safest order — if shutdown fails later, processes are already gone.

## Key Files

- `daemon/tools/proc_tools.py` — add `cleanup_all()` method (~line 1085)
- `daemon/services/job_feedback_observer.py` — add two-tier proc cleanup in `_dispatch_instance_post_commit_side_effects` (~line 2391)
- `daemon/manager.py` — add `cleanup_all()` call in `shutdown()` (~line 5536)
- `tests/.../test_proc_tools.py` — unit test for `cleanup_all()`
- `tests/.../test_job_feedback_observer.py` — unit tests for Tier 1 always + Tier 2 root-only + get_tree_ids failure isolation
- `tests/.../test_manager.py` (or `test_manager_shutdown.py`) — unit test for shutdown proc kill

## Constraints

- **No bash changes.** Bash tool, bash registry, and CancelledError fix are Phase 2.
- **No lifecycle path changes.** `_dispatch_instance_post_commit_side_effects` is the only hook. Do NOT modify `terminate_instance`, `_process_child_completion_and_notify_parent`, or `_send_error_report` directly — they all converge on the dispatcher.
- **Best-effort only.** Every cleanup call wrapped in try/except; failures log WARNING, never propagate.
- **Idempotency.** Rely on `cleanup_instance` atomic bucket pop. Double-fire (terminate cascade + finalize) is a benign no-op.
- **Tier 1 always fires.** ANY terminal instance cleans its own procs, regardless of parent_id.
- **Tier 2 strictly root-gated.** Tree sweep fires ONLY when `parent_id is None`, and skips the root itself (Tier 1 handled it).
- **`tree_ids` initialized outside try.** `tree_ids: list[str] = []` before the `try` block (C4).
- **Sync `get_tree_ids` wrapped in `to_thread`.** Never call sync DB code inline in async context (M3).
- **Repository access via `self._instance_manager._instance_repository`.** `JobFeedbackObserver` has NO `self.repository` (M3).

## Deliverables

- [ ] `BackgroundProcessManager.cleanup_all()` implemented and tested
- [ ] Tier 1 always cleans the terminating instance's own proc processes
- [ ] Tier 2 root-gated cleans descendants' proc processes
- [ ] `get_tree_ids` failure is isolated (tree_ids stays [], other side-effects fire)
- [ ] `manager.shutdown()` kills all proc processes
- [ ] All existing tests pass (no regressions)
- [ ] Best-effort: cleanup failures log WARNING, never block terminal/shutdown

## Edge Cases Handled in This Phase

| Edge Case | Handling |
|-----------|----------|
| Root with no processes | `cleanup_instance` returns immediately on empty bucket pop. No-op. |
| Child with no processes | Tier 1 runs (no-op empty pop). Tier 2 skipped (parent_id != None). |
| Descendant with no processes | Same — no-op in Tier 2. |
| `get_tree_ids` returns `[]` (root missing) | Loop body skipped. No-op. |
| `get_tree_ids` raises | `tree_ids` stays `[]` (C4). Tier 2 no-op. Tier 1 already ran. WARNING logged. |
| `instance_repository` missing on manager | WARNING logged, Tier 2 skipped. Tier 1 still ran. |
| Double cleanup (terminate cascade + finalize) | Idempotent atomic pop. No-op second time. |
| `cleanup_instance` raises | Caught per-iid, WARNING logged, loop continues to next iid. |
| Instance already cleaned by cascade | Empty bucket pop. No-op. |
