# Phase 5 Code Review — Dual-Path Unification

**Reviewer:** orchestrator (with delegated file reads via Read/Grep)
**Date:** 2026-06-17
**Branch:** `feature/correlation-manager` (uncommitted working tree)
**Commit chain:** Phase 4 complete (`3b9bf3be`), Phase 4 verification (`4f608fc1`)
**Files reviewed:** 17 modified + 3 new (2,846 lines of new tests)

---

## TL;DR

| # | Section | Verdict |
|---|---------|---------|
| 1 | Behavioral Equivalence | ✅ PASS (1 WARN) |
| 2 | Error Handling Correctness | ✅ PASS (1 WARN) |
| 3 | InstanceStatus Migration | ✅ PASS |
| 4 | Enqueue Helper | ✅ PASS |
| 5 | Deferred Cleanup (S3-S5) | ✅ PASS |
| 6 | Code Quality | ✅ PASS (1 WARN) |
| 7 | Test Quality | ✅ PASS |

**No FAIL issues found.** Two minor WARN items, both pre-disclosed in the discovery documents and benign given current callers.

**Overall: READY FOR MERGE.**

---

## Section 1: Behavioral Equivalence — ✅ PASS (1 WARN)

**Verdict:** Pipeline produces identical observable behavior to pre-Phase-5 implementation. All 6 shared stages correctly extracted.

### ✅ Pipeline stage extraction

| Stage | Location | Status |
|-------|----------|--------|
| 1. Build `_do_process` closure | `daemon/services/message_processing_pipeline.py:397-408` | ✅ |
| 2. Acquire lease (`execution_gate.run`) | `daemon/services/message_processing_pipeline.py:419-430` | ✅ |
| 3. Handle `LeaseContention` / `LeaseLostError` | `daemon/services/message_processing_pipeline.py:635-660` | ✅ |
| 4. Mark message COMPLETED | `daemon/services/message_processing_pipeline.py:497-520` | ✅ Defensive JQ pattern adopted for both |
| 5. Resolve dispatch source + dispatch | `daemon/services/message_processing_pipeline.py:522-602` | ✅ JQ guard (skip `internal_*`) adopted for both |
| 6. Child completion check | `daemon/services/message_processing_pipeline.py:605-628` | ✅ |

### ✅ Pre-Phase-5 divergences preserved (per ADR)

- **WP `is_retry` OR**: `daemon/services/task_processor.py:189` — `is_retry = task.retry_count > 0 or original_resume_mode` preserved
- **JQ `is_retry`**: `daemon/services/message_job_handler.py:290` — `resume_mode=resume_mode` preserved

### ✅ Error helper invocation: exactly once per failure

- **Stage 2 errors** → propagate out of `execute()` → dispatcher's outer `except` calls `handle_message_processing_error`
  - WP: `daemon/services/task_processor.py:243-260`
  - JQ: `daemon/services/message_job_handler.py:401-422`
- **Stage 3-6 errors** → pipeline catches at `daemon/services/message_processing_pipeline.py:455-474`, runs `_run_error_handler` once, returns `ProcessingResult(success=False, error=e)` (does NOT re-raise)
- **Verified by** `tests/test_pipeline_unified.py:677-777` (`test_error_side_effects_parity`)

### ✅ Cancel discrimination preserved (out of pipeline)

- WP: `daemon/services/task_processor.py:228-242` — `OperationCancelledError` re-raised, `asyncio.CancelledError` logs "task paused" and re-raises
- JQ: `daemon/services/message_job_handler.py:348-400` — `OperationCancelledError` calls `complete_job(CANCELLED)`, `asyncio.CancelledError` discriminates pause vs terminate via `instance.status == PAUSED.value`

### ⚠️ WARN-1: `on_defer` callback is dead code

- **Locations:**
  - `daemon/services/message_processing_pipeline.py:173` — `OnDeferCb` type alias
  - `daemon/services/message_processing_pipeline.py:208` — `PipelineCallbacks` docstring
  - `daemon/services/message_processing_pipeline.py:214` — `PipelineCallbacks` docstring
  - `daemon/services/message_processing_pipeline.py:252` — `on_defer: OnDeferCb | None = None` field
- **What:** Declared in `PipelineCallbacks` and documented as "the place for the JobQueue callback to emit `notify_watchers(status='in_progress')`" — but **never invoked** in `execute()`. The JQ path's deferral logic is implemented inside `on_success` at `daemon/services/message_job_handler.py:480-567` instead.
- **Severity:** Low (dead surface area, no behavioral impact)
- **Suggested fix:** Remove from `PipelineCallbacks`, drop `OnDeferCb` type alias, remove docstring references. Or refactor JQ to call `on_defer` for the deferral case.

---

## Section 2: Error Handling Correctness — ✅ PASS (1 WARN)

### ✅ Stage partition (errors caught vs propagated)

| Layer | What | Where |
|-------|------|-------|
| Stage 2 (`gate.run` / `work_fn`) | `LeaseLostError` caught → `on_contention`; everything else propagates | `daemon/services/message_processing_pipeline.py:419-430` |
| Stages 3-6 (post-processing) | `OperationCancelledError` → `on_cancel`; `asyncio.CancelledError` → `on_cancel`; `Exception` → `handle_message_processing_error` + `on_error` | `daemon/services/message_processing_pipeline.py:451-474` |
| Outer try/except in dispatcher | Catches stage-2 errors, dispatches by type | `daemon/services/task_processor.py:228-260` (WP), `daemon/services/message_job_handler.py:348-422` (JQ) |

### ✅ No double-firing of error helper

- Pipeline returns `ProcessingResult(success=False, error=e)` at `daemon/services/message_processing_pipeline.py:474` for stage 3-6 errors — does NOT re-raise
- Dispatcher's outer `except` only fires for stage-2 errors (which bypassed the pipeline's try/except)
- WP re-raises `result.error` at `daemon/services/task_processor.py:268` so the worker pool's `fail_task` runs — but the error helper is NOT called a second time (it already ran inside the pipeline)

### ✅ Cancellation discrimination preserved

- **WP `asyncio.CancelledError`**: `daemon/services/task_processor.py:234-242` — log "task paused" then re-raise (matches pre-Phase-5)
- **WP `OperationCancelledError`**: `daemon/services/task_processor.py:228-233` — re-raise silently (matches pre-Phase-5)
- **JQ `OperationCancelledError`**: `daemon/services/message_job_handler.py:348-366` — `complete_job(CANCELLED)` (matches pre-Phase-5)
- **JQ `asyncio.CancelledError`**: `daemon/services/message_job_handler.py:367-400` — discriminate via `instance.status == PAUSED.value` (matches pre-Phase-5)

### ✅ Callback robustness

- `_handle_contention` wraps callback in try/except, re-raises original on failure: `daemon/services/message_processing_pipeline.py:646-660`
- `_handle_cancel` same pattern: `daemon/services/message_processing_pipeline.py:674-686`
- `_run_error_handler` wraps `handle_message_processing_error` in try/except: `daemon/services/message_processing_pipeline.py:714-723`

### ⚠️ WARN-2: `LeaseContention` raise quirk

- **Locations:**
  - `daemon/services/execution_gate.py:152-153` — defines `LeaseContention` as plain `@dataclass` (not a `BaseException` subclass)
  - `daemon/services/message_processing_pipeline.py:647` — `raise exc` if `on_contention is None`
  - `daemon/services/message_processing_pipeline.py:683` — same `raise exc` pattern in `_handle_cancel` (less likely to trigger since only called with `BaseException` subclasses)
- **What:** `raise exc` requires a `BaseException` instance in Python 3.9+. For `LeaseLostError` (an `Exception` subclass) this works. For `LeaseContention` (a dataclass) it would `TypeError` at runtime.
- **Impact:** Both production paths always supply `on_contention`:
  - WP: `daemon/services/task_processor.py:402-406`
  - JQ: `daemon/services/message_job_handler.py:599-603`
  So this path is currently unreachable. The test at `tests/test_pipeline_unified.py:422-433` correctly documents this as a benign quirk.
- **Severity:** Low (latent bug, unreachable in practice)
- **Suggested fix:**
  ```python
  if callbacks.on_contention is None:
      if isinstance(exc, BaseException):
          raise exc
      raise RuntimeError(f"Unhandled contention: {exc!r}")
  ```

---

## Section 3: InstanceStatus Migration — ✅ PASS

### ✅ Canonical enum updated

- `daemon/repositories/instance/models.py:23` — `WAITING = "waiting"  # Active but no in-flight work (e.g. awaiting next user input)`
- All 10 members present: IDLE, RUNNING, **WAITING**, PAUSED, COMPLETED, ERROR, TERMINATED, QUEUED, WAITING_CHILDREN, FAILED
- `is_valid()` classmethod retained at `daemon/repositories/instance/models.py:32-34`

### ✅ Duplicate definition eliminated

- `daemon/models/instance.py:9` — single line re-export: `from daemon.repositories.instance.models import InstanceStatus`
- `__all__` updated at `daemon/models/instance.py:118`
- No leftover `class InstanceStatus` in `daemon/models/instance.py` (verified by reading entire 118-line file)

### ✅ Import migration complete

| File | Line | Status |
|------|------|--------|
| `daemon/sources/adapters/scheduler.py` | 22 | ✅ imports from canonical |
| `daemon/sources/adapters/scheduler.py` | 562-565 | ✅ `InstanceStatus.WAITING.value` now works (was broken before) |
| `daemon/routers/messages.py` | 13 | ✅ migrated to canonical |
| `daemon/services/job_feedback_observer.py` | 53 | ✅ already canonical |
| `daemon/services/job_recovery_service.py` | 10 | ✅ already canonical |

### ✅ All 19 raw-string checks replaced (discovery item #5)

| File | Lines | Status |
|------|-------|--------|
| `daemon/services/job_processor.py` | 405, 431, 448, 523, 561, 577 | ✅ All use `InstanceStatus.XXX.value` |
| `daemon/services/job_feedback_observer.py` | 338, 361, 493, 535, 695, 697, 777 | ✅ All use `InstanceStatus.XXX.value` |
| `daemon/services/job_recovery_service.py` | 132-137 | ✅ `InstanceStatus.XXX.value` |
| `daemon/tools/job_queue.py` | 453-458 | ✅ `InstanceStatus.XXX.value` |

### ✅ `_TERMINAL_INSTANCE_STATUSES` consistency

- `daemon/services/job_feedback_observer.py:73-78` and `daemon/services/job_recovery_service.py:25-30` are identical sets
- Documented as intentional to avoid import cycle (`daemon/services/job_feedback_observer.py:70-72`)

---

## Section 4: Enqueue Helper — ✅ PASS

### ✅ `_prepare_enqueued_message` extraction

- **Definition:** `daemon/services/instance_messaging.py:710-868` (159 lines)
- **Return type:** `_PreparedEnqueueContext` NamedTuple at `daemon/services/instance_messaging.py:121-128` with 6 fields: `message_id`, `msg_type`, `status_changed_to_running`, `is_idle_to_running`, `instance_agent_id`, `previous_status`

### ✅ Path parity verified

| Aspect | `enqueue_message` (WP) | `enqueue_message_via_jq` |
|--------|------------------------|-------------------------|
| Location | `daemon/services/instance_messaging.py:870-927` | `daemon/services/instance_messaging.py:1419-1509` |
| Helper call | line 894-903 | line 1444-1453 |
| `create_task_row` | `True` (line 901) | `False` (line 1451) |
| `path_label` | `"WorkerPool"` (line 902) | `""` (line 1452) |
| Pre-state (DB writes) | MessageQueue + Task + status transition + event | MessageQueue + status transition + event |
| Post-prelude dispatch | `worker_pool.notify_work()` (line 919) | `_job_queue_service.enqueue(...)` (line 1480) |

### ✅ Atomicity preserved

- Task row inserted inside the same `WriteGuardSession` as MessageQueue row (`daemon/services/instance_messaging.py:797-805`): both commit or both roll back

### ✅ Minor cosmetic difference (acceptable)

- "Reactivating completed instance" log includes `(WorkerPool)` for WP path, no suffix for JQ path
- This only affects log readability, not pre-state

### ✅ Test coverage

- `tests/test_enqueue_shared.py:704-` has `TestPrepareEnqueuedMessageHelper` (4 tests) — helper isolation
- `TestMessageQueueRowParity`, `TestEventRowParity`, `TestStatusTransitionParity`, `TestTitleGenerationParity` (lines 221-703) — both paths produce identical side-effects

---

## Section 5: Deferred Cleanup Items — ✅ PASS (all 3)

### ✅ S3 — Double `get_correlation_manager()` removed

- **Location:** `daemon/services/child_reports.py:810-816`
- **Fix verified:** At line 814, code reads `if cm is not None:` — reuses the `cm` bound at line 771 (no second `get_correlation_manager()` call)
- The misleading comment block from the discovery doc (lines 811-815 in pre-Phase-5) has been removed; replaced with the reuse comment at lines 811-813
- The orphan `from .correlation_manager import get_correlation_manager` at the second site is also gone

### ✅ S4 — `waiting_for=0` pause write comment added

- **Location:** `daemon/services/instance_lifecycle.py:729-741`
- **Comment verified:** 13-line block explains the ADR-011 carve-out (CM authoritative, children paused, resume re-registers). References CM as the source of truth, explains why the cache is safe to reset to 0, and notes that resume re-registers via CM.

### ✅ S5 — `getattr` fallback replaced with `assert`

- **Location:** `daemon/services/job_processor.py:175-176`
- **Fix verified:**
  ```python
  assert hasattr(instance_meta, "instance_id"), "instance_meta must be an InstanceModel"
  instance_id = instance_meta.instance_id
  ```
- Line 178 simplified to `if cm is not None:` (no longer needs `and instance_id is not None`)
- **Appropriateness:** Pydantic guarantees the field on `InstanceModel`. `assert` is correct — fails fast with clear error on wrong type; `python -O` would skip it but default is to run.

---

## Section 6: Code Quality — ✅ PASS (1 WARN)

### ✅ Type hints

- Mostly correct throughout
- `Any` usage in pipeline constructor (`daemon/services/message_processing_pipeline.py:297-300`) is acceptable for facade patterns but a `Protocol` would be stricter. Not a blocker.

### ✅ Docstrings

- Excellent. The pipeline's module docstring (`daemon/services/message_processing_pipeline.py:1-76`) explains design rationale, per-callback semantics, and the cancellation ownership model
- All public methods documented
- Inline comments explain ADR-011 carve-outs, FIFO carve-outs, and the per-instance contention throttling pattern

### ✅ Logging preserved

| Original log | New location |
|--------------|--------------|
| Per-instance contention throttling (WP) | `daemon/services/task_processor.py:373-389` |
| JQ trace logs | `daemon/services/message_job_handler.py:127, 252, 622, 1496, 1502` |
| "Task paused" log (WP) | `daemon/services/task_processor.py:240` |
| "Reactivating completed instance" | `daemon/services/instance_messaging.py:797-801` (helper) |
| Lease lost / contention logs | `daemon/services/task_processor.py:351-355`, `daemon/services/message_job_handler.py:580-584` |

### ✅ No orphaned imports (syntax check passed on all 17 files)

### ⚠️ WARN-1: `on_defer` dead code (see Section 1)

- Same finding as Section 1 — `daemon/services/message_processing_pipeline.py:173, 208, 214, 252`

### ℹ️ `event_repo` accepted but unused (intentional)

- `daemon/services/task_processor.py:69` accepts `event_repo`; line 100 stores it as `self._event_repo` with comment "accepted for API compat; unused"
- Documented as intentional, not a defect

### ℹ️ `_TERMINAL_INSTANCE_STATUSES` duplicated (intentional)

- `daemon/services/job_feedback_observer.py:73-78` and `daemon/services/job_recovery_service.py:25-30`
- Documented at `daemon/services/job_feedback_observer.py:70-72` as intentional to avoid import cycle

---

## Section 7: Test Quality — ✅ PASS

### ✅ 2,846 lines of new tests across 3 files

| File | Lines | Test classes | Coverage |
|------|-------|--------------|----------|
| `tests/test_pipeline_unified.py` | 1,071 | 2 | Pipeline unit + path parity |
| `tests/test_enqueue_shared.py` | 993 | 6 | Enqueue helper + path parity |
| `tests/test_phase5_real_cm_integration.py` | 782 | 4 | Real CM + SQLite round-trip |

### ✅ `tests/test_pipeline_unified.py` coverage

- **`TestPipelineUnit`** (8 tests, lines 113-563):
  - `test_happy_path_calls_all_stages_in_order` (line 127)
  - `test_post_processing_error_runs_error_handler_and_on_error` (line 201)
  - `test_post_processing_exception_swallowed_by_stage` (line 271)
  - `test_stage2_error_propagates_out` (line 322)
  - `test_contention_path_delegates_to_on_contention` (line 365)
  - `test_contention_without_callback_reraises_lease_lost` (line 422) — documents the `LeaseContention` quirk
  - `test_cancel_path_delegates_to_on_cancel` (line 470)
  - `test_cancel_without_callback_reraises` (line 532)
- **`TestPathParity`** (5 tests, lines 571-1069):
  - `test_error_side_effects_parity` (line 678) — verifies exactly-one error helper call per failure across both paths
  - `test_dispatch_parity` (line 784)
  - `test_child_completion_parity` (line 868)
  - `test_retry_count_parity` (line 938)
  - `test_context_fields_parity` (line 1014)

### ✅ `tests/test_enqueue_shared.py` coverage

- `TestMessageQueueRowParity` (line 221) — both paths write identical MessageQueue rows
- `TestEventRowParity` (line 362) — both paths emit identical events
- `TestStatusTransitionParity` (line 457) — both paths transition identically
- `TestTitleGenerationParity` (line 608) — both paths trigger title gen identically
- `TestPrepareEnqueuedMessageHelper` (line 704) — 4 tests for helper isolation
- `TestDispatchLayerDifference` (line 952) — verifies path-specific dispatch (Task + notify vs JobQueue enqueue)

### ✅ `tests/test_phase5_real_cm_integration.py` coverage

- `TestIncrementPathRoundTrip` (line 270) — DB+CM agreement after send_message
- `TestDecrementPathRoundTrip` (line 366) — DB+CM agreement after child completion
- `TestRebuildAfterRestart` (line 477) — CM reconstruction from DB
- `TestMultiMessageRoundTrip` (line 659) — N correlations, partial resolution

### ✅ Test strength qualities

- All tests have descriptive docstrings explaining intent
- Parity tests are well-designed: inject SAME mocks into both paths, assert identical call signatures
- Well-isolated with per-test fixtures
- `test_contention_without_callback_reraises_lease_lost` (line 422) correctly documents the WARN-2 quirk

### ✅ Modified test files

- `tests/test_dispatch_completed_fix.py:528` — updated log assertion to match new "MessageProcessingPipeline" prefix
- `tests/test_jq_error_reporting.py:835-840` — moved `manager.execution_gate = gate` BEFORE `ProcessMessageProcessor()` construction (the pipeline now snapshots the gate at construction time)

### ℹ️ Minor gaps (acceptable)

- No test for `on_defer` (it's dead code — nothing to test)
- No test explicitly verifies the assertion at `daemon/services/job_processor.py:175` (Pydantic guarantees the field, hard to test the wrong-type case in a way that exercises the assert)

---

## WARN Summary

### WARN-1: `on_defer` callback is dead code

- **Files:** `daemon/services/message_processing_pipeline.py:173, 208, 214, 252`
- **Issue:** Declared in `PipelineCallbacks` and `OnDeferCb` type alias, documented as the JQ deferral hook, but never invoked in `execute()`. The JQ path's deferral logic is implemented inside `on_success` at `daemon/services/message_job_handler.py:480-567` instead.
- **Severity:** Low
- **Suggested fix:** Remove from `PipelineCallbacks`, drop `OnDeferCb` type alias, remove docstring references. Or refactor JQ to call `on_defer` for the deferral case (would be a larger change).

### WARN-2: `LeaseContention` raise quirk

- **Files:** `daemon/services/execution_gate.py:152-153`, `daemon/services/message_processing_pipeline.py:647, 683`
- **Issue:** `LeaseContention` is a plain `@dataclass`, NOT a `BaseException` subclass. The pipeline's `_handle_contention` does `raise exc` at line 647 if `on_contention is None`, which would `TypeError` at runtime for `LeaseContention` (works for `LeaseLostError` which IS an `Exception` subclass).
- **Impact:** Both production paths always supply `on_contention`:
  - WP: `daemon/services/task_processor.py:402-406`
  - JQ: `daemon/services/message_job_handler.py:599-603`
  So this path is currently unreachable. The test at `tests/test_pipeline_unified.py:422-433` correctly documents this as a benign quirk.
- **Severity:** Low (latent bug, unreachable in practice)
- **Suggested fix:**
  ```python
  if callbacks.on_contention is None:
      if isinstance(exc, BaseException):
          raise exc
      raise RuntimeError(f"Unhandled contention: {exc!r}")
  ```

---

## Final Verdict

**Phase 5 is READY FOR MERGE.** No FAIL issues. Two minor WARN items, both pre-disclosed in the discovery documents and benign given current callers.

### Recommended Pre-Merge Actions (Optional)

1. **Trivial cleanup (5 min):** Remove dead `on_defer` callback from `PipelineCallbacks` and `OnDeferCb` type alias (addresses WARN-1).
2. **Optional hardening (5 min):** Type-check `exc` before `raise` in `_handle_contention` to defend against the WARN-2 latent bug.

### Files NOT Requiring Review Action

All 19 raw-string `InstanceStatus` checks correctly replaced. All 3 deferred cleanup items (S3, S4, S5) verified. Both `enqueue_message` paths produce identical pre-state. Pipeline correctly delegates 6 shared stages. Error handling is exactly-once per failure with no double-firing. Tests are thorough with 2,846 lines across 3 new files.

### Pre-Merge Verification Recommendations

1. Run `pytest tests/test_pipeline_unified.py tests/test_enqueue_shared.py tests/test_phase5_real_cm_integration.py -v` (new tests)
2. Run `pytest tests/test_jq_error_reporting.py tests/unit/test_dispatch_completed_fix.py -v` (modified tests)
3. Run full test suite to confirm no regressions in the ~6,900+ existing tests
4. Verify `python -c "from daemon.services.message_processing_pipeline import MessageProcessingPipeline, PipelineCallbacks, ProcessingContext, ProcessingResult"` imports cleanly
