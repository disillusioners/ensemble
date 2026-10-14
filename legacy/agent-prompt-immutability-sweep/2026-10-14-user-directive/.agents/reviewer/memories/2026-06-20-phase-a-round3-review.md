# Phase A Round 3 Review (commit 9414a17f) — 2026-06-20

## Verdict: REQUEST CHANGES (1 critical, 1 warning)
The generation counter mechanism is SOUND — the orphan race is genuinely closed. But the re-arm block has a fall-through bug that fires the post-commit outbox with stale "completed" status.

## ✅ Verified Correct (orphan race closed)
1. **Gen counter atomicity:** No await between bump (L252) and lock acquisition (L253). Single-threaded asyncio cannot deschedule between them. Interleaving A (register bumps gen, deschedules, finalize reads pre_gen=N, commits, post_gen=N → no re-arm) is IMPOSSIBLE.
2. **Read-modify-write:** `_generation[parent_id] = _generation.get(parent_id, 0) + 1` is a single statement, no await. Atomic in asyncio.
3. **Multiple late registers:** Sequential bumps, post_gen = pre_gen + N, re-arm fires once, both children in _pending. ✅
4. **Gen lifecycle:** clear_for_instance + rebuild_from_db both reset _generation alongside _pending. If clear runs mid-finalization, post_gen=0 < pre_gen → no re-arm, but _pending also cleared → nothing to re-arm for. ✅
5. **State machine transition:** (COMPLETED, PROCESSING): "rearm_after_complete" — clean, documented, well-tested.
6. **False-positive guard test:** Patches get_generation → 0, asserts orphan manifests. Valid independent guard. ✅
7. **W2 honest docs:** Accurately documents that RuntimeError is caught by upstream except Exception handlers. Per-job FAILED, not process crash. ✅

## 🔴 CRITICAL — Re-arm falls through to post-commit outbox
**File:** `job_feedback_observer.py:771-801` (re-arm block, NO return) → falls through to `:849-887`
**Issue:** After successful `atomic_transition(COMPLETED → PROCESSING)`, no `return`. Execution reaches:
- L851: `notify_watchers(job.job_id, "completed")` — wrong status for PROCESSING job
- L877: `_dispatch_instance_post_commit_side_effects(terminal_status="completed")` — fires SSE status_change("completed"), CompletionRegistry.complete(), lifecycle event
- L887: `_trigger_next_job(job)` — queue over-admission

`db_result.terminal_status` was set to "completed" by `_finalize_job_db_sync` BEFORE re-arm; the re-arm updates DB but never updates db_result. Stale "completed" flows through outbox.

**Impact:** CompletionRegistry.complete() is worst — unblocks waiters with pre-re-arm result (incomplete data). No self-correction on second finalize. SSE causes UI flicker. _trigger_next_job causes queue over-admission.

**Fix:** Add `if rearmed: return` after successful transition. Exception paths (InvalidTransitionError, generic Exception) correctly fall through (job moved by other actor / re-arm failed → outbox valid).

## 🟡 WARNING — Test doesn't assert side effects
**File:** `tests/test_finalize_job_threading.py:421-590`
**Issue:** Mocks for notify_watchers, stream_status_change, _publish_instance_lifecycle_event wired but never asserted. CompletionRegistry not mocked. Both tests pass even with the fall-through bug.
**Fix:** Add assert_not_called on the three side effects + mock CompletionRegistry.complete.
