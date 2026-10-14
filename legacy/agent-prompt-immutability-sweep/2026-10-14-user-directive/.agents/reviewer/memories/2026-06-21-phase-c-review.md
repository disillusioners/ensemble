# Phase C Review (commits f69023d6..0f9c909a) — 2026-06-21

## Verdict: REQUEST CHANGES (1 critical, 2 warnings, 1 suggestion)
The architecture is sound — dispatch unification + gate collapse are well-designed. But _admit_via_worker_pool has silent-return paths that wedge jobs.

## 🔴 CRITICAL

### C-F1 — _admit_via_worker_pool silent returns wedge job in PROCESSING forever
- **File:** `job_feedback_observer.py:483-530` (4 silent return paths) + `job_processor.py:938` (unconditional continue)
- **Issue:** `_admit_via_worker_pool` has 4 error paths that log and `return` without raising:
  1. L489: missing message_id
  2. L496: missing instance_id
  3. L509: missing task_repo
  4. L530: Task creation exception
- After the call at L888, the caller at L938 unconditionally `continue`s. The `except Exception` at L900 only catches RAISED exceptions. Silent returns bypass it entirely.
- **Result:** Job stays in PROCESSING forever. The per-queue lock is held. No watchdog recovers it.
- **Fix:** Make the 4 error paths raise (e.g., `raise JobAdmissionError(...)`) so the caller's existing `except Exception` handler at L900-931 marks the job FAILED.
- **Docstring contradicts itself:** L468-471 says "The job is NOT marked FAILED here on error paths: those exceptions propagate up and JobProcessor decides." But the code RETURNS, doesn't propagate.

## ✅ Verified Correct

| Area | Status |
|------|--------|
| C-M5 flag default | ✅ `use_legacy_jobqueue_dispatch: False` (config.py:371, config.yaml:127) |
| C-M5 JobProcessor routing | ✅ Correct 3-way branch: legacy / unified / misconfiguration |
| C-M5 MessageJobHandler demotion | ✅ Only called when flag ON |
| C-M5 api.py wiring | ✅ JobFeedbackObserver correctly wired into JobProcessor |
| C-M6 asyncio.Lock impl | ✅ `async with lock: return await work_fn()` — clean |
| C-M6 threading model | ✅ Both gate.run call sites are async on main loop; MainLoopBridge funnels WorkerPool threads via run_coroutine_threadsafe |
| C-M6 execution_lease/ deleted | ✅ factory.py, __init__.py, manager.py all clean |
| C-M6 constructor backward compat | ✅ `*args, **kwargs` accepts and ignores old args |
| C-M6 cross-process limitation | ✅ Documented in module docstring L8-10 |
| C-M6 deprecated stubs | ✅ Importable, never raised/returned |

## 🟡 Warnings

### C-W1 — Dead LeaseContention/LeaseLostError branches have terminal-state side effects
- **Files:** `message_processing_pipeline.py:440-443`, `manager.py:2819,2854`
- These branches are unreachable (gate never raises/returns them), but IF reached (e.g., via a regression), they would: re-queue jobs, mark FAILED, mark instances ERROR, trigger exponential backoff retry. Harmless today but confusing if triggered.
- **Fix:** Remove in a follow-up cleanup pass (as documented in execution_gate.py:65-66).

### C-W2 — Pause/terminate matrix not parametrized over dispatch flag
- **File:** `test_pause_terminate_matrix.py` (20 tests)
- Tests snapshot both paths but are not parametrized over `use_legacy_jobqueue_dispatch`. Each scenario is tested once, not under both flag states.
- **Fix:** Add `@pytest.mark.parametrize("use_legacy", [True, False])` or document why single-state is sufficient.

## 🟢 Suggestions

### C-S1 — test_gate_threading_serialization.py misleading name
- Tests use `asyncio.gather`, not `threading.Thread`. The serialization is event-loop-level, not thread-level. Rename to `test_gate_serialization.py` or add real threading tests.
