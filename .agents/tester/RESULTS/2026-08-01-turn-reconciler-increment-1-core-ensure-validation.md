# Turn Reconciler Increment 1 — Core ensure.md Validation

**Date:** 2026-08-01  
**Scope:** Core requirements only. Release Gate excluded by request because E2E validation is running separately.  
**Quarantine:** `.agents/tester/QUARANTINE.md` has no active quarantined tests.

## Results

### Critical — 4/4 passed

1. **PASS — No regressions in changed packs.** Covered by parallel packs in the enclosing test run; no duplicate pack execution was requested here.
2. **PASS — Deadlock / concurrency integrity.** Ran the `concurrency_atomic_unit_test` mapping from `PACKS.md` with:
   ```bash
   timeout 300 .venv/bin/pytest tests/test_cascade_concurrency.py tests/test_cascade_race3.py tests/test_deadlock_fix.py tests/test_instance_delete_by_project_locking.py tests/test_instance_metadata_atomic.py tests/test_observer_race1.py tests/test_project_repository_atomic.py --override-ini="addopts=" --tb=short -q
   ```
   Evidence: `66 passed, 19 skipped in 7.54s`; exit code 0. Skips are pack/environment skips, not quarantined failures.
3. **PASS — No sync DB calls on the asyncio event loop.** Covered by the same passing concurrency pack. `tests/test_deadlock_fix.py` contains paired thread-identity and explicit `asyncio.to_thread` tests for enqueue preparation, watcher reads, finalization, child completion, and error-report DB helpers.
4. **PASS — `dev.sh` graceful shutdown.** Evidence:
   ```text
   71:# --timeout-graceful-shutdown 10 ensures uvicorn forces exit after 10s even
   74:$PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log --timeout-graceful-shutdown 10
   ```

### Important — 2/2 passed

5. **PASS — All callers of converted async functions properly await.** Production references found by grep:
   - `_get_system_prompt_tokens`: definitions/docs plus calls at `daemon/services/instance_messaging.py:581` and `:727`, both `await`ed.
   - `_compute_context_usage`: call at `daemon/services/instance_messaging.py:611`, `await`ed.
   - `get_queue_stats`: production calls at `daemon/routers/instances.py:278,416`, `daemon/routers/messages.py:524`, `daemon/tools/instance.py:1541`, and delegation at `daemon/manager.py:4611`; all are `await`ed.
6. **PASS — Original parent→child→complete deadlock scenario.** Covered by Requirement 2's passing concurrency pack, including `tests/test_deadlock_fix.py` child-completion off-loop/thread tests and cascade race packs.

### Nice-to-have — 1/1 passed

7. **PASS — No dead executable UPDATE 4 block remains.** `grep -rn "UPDATE 4" daemon/ --include="*.py"` found migration comments/docstrings and reconciler-aware log text in `daemon/services/instance_lifecycle.py`, but inspection of the former block confirms the old dialect-branched SQL is absent. The executable path now invokes `self._task_repo.reconcile_turn_mirror(work_id)` at lines 3827–3829. The repository dialect grep found unrelated live repository infrastructure (upsert/boolean/atomic-update dialect handling), not the removed UPDATE 4 block. `py_compile` also passed for the reconciler and five converted call-site modules.

## Quick Fixes

None. No production or test files were changed, and no commit was created. This validation added only this RESULTS artifact.

## Environment Note

A concurrent workspace modification appeared during validation in `tests/postgres/test_pause_report_orphan_reconciliation_pg.py`. It was not made or altered by this validation and is outside the executed Core concurrency pack; no conclusions here depend on that file.

## Summary

- **Critical:** 4/4 passed
- **Important:** 2/2 passed
- **Nice-to-have:** 1/1 passed
- **Overall:** 7/7 Core requirements passed
