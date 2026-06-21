# Quality Requirements

## Critical
_MUST pass before testing is complete_

- [ ] All non-integration tests pass (pytest exit code 0)
  - Validation: Run `python -m pytest tests/ -x --tb=short -q`
- [ ] Deadlock fix tests pass (test_deadlock_fix.py)
  - Validation: Run `python -m pytest tests/test_deadlock_fix.py -v`
- [ ] No sync DB calls remain on the asyncio event loop thread
  - Validation: Thread-identity tests verify asyncio.to_thread wrapping for all DB helpers
- [ ] dev.sh includes `--timeout-graceful-shutdown 10`
  - Validation: Check dev.sh content for the flag
- [ ] E2E: Normal parent→child workflow completes (happy path)
  - Validation: Run `python -m pytest tests/e2e/test_e2e_workflows.py::test_parent_child_workflow_happy_path -v -m integration` (requires daemon running via ./dev.sh). Leader spawns coder child, coder completes, leader reaches terminal status.
- [ ] E2E: Pause after spawn, then resume works correctly
  - Validation: Run `python -m pytest tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume -v -m integration` (requires daemon running via ./dev.sh). Both leader and coder pause, then resume and complete.
- [ ] E2E: Terminate after spawn, then revive documented
  - Validation: Run `python -m pytest tests/e2e/test_e2e_workflows.py::test_terminate_after_spawn_then_revive -v -m integration` (requires daemon running via ./dev.sh). Termination succeeds, behavior after "continue" message documented.

## Important
_Should pass, flag if failed_

- [ ] All callers of converted async functions properly await
  - Validation: `_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats` callers use await
- [ ] Original deadlock scenario (parent→child→complete) works without blocking
  - Validation: Parent spawn child → child responds → parent completes flow

## Nice-to-have
_Informational, report status only_

- [ ] No dead code from the fix (deleted code was truly unused)
  - Validation: Verify dead code deletion didn't break imports
