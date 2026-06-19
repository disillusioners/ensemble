# Lesson: Mock Granularity Causing Carve-Out Guard False Positives

**Date**: 2026-06-18
**Commit**: 547a0f0f
**Files affected**: `tests/unit/test_root_instance_completion.py`, `tests/test_phase4_deprecation.py`

## The Issue
3 pre-existing tests fail because their MagicMock session returns the SAME value for ALL `session.exec().scalar_one()` calls:
- `create_session_with_pending(pending_count=1)` sets `exec_result.scalar_one = MagicMock(return_value=1)`
- This means EVERY query — including the new terminal-job count check — returns `1`
- The new carve-out guard: `_terminal_job_exists = session.exec(SELECT COUNT terminal jobs).scalar_one() > 0`
- With mock: `1 > 0 = True` → guard FALSELY fires → skips WAITING_CHILDREN write
- Test expects status `waiting_children`, gets `running` → FAIL

## Root Cause
The mock conflates two semantically distinct queries:
1. **Pending-message count** (should return `1`)
2. **Terminal-job count** (should return `0`, but mock returns `1`)

## Failing Tests (all mock issues, NOT production bugs)
1. `test_root_instance_completion.py::TestRegressionBug::test_root_with_pending_messages_stays_waiting_children`
2. `test_root_instance_completion.py::TestSimpleAgentHappyPath::test_root_with_pending_then_drained_completes`
3. `test_phase4_deprecation.py::TestRootVsNonRootWaitingChildren::test_root_with_pending_own_queue_gets_waiting_children`

## Proof It's a Mock Issue (Not Production Bug)
The companion test `test_child_reports.py::test_normal_path_sets_waiting_children_when_job_processing` uses a **REAL in-memory SQLite DB**:
- Seeds a PROCESSING (non-terminal) message job
- The terminal-job query correctly returns `0`
- Guard does NOT fire
- WAITING_CHILDREN written correctly → test PASSES

## Fix Direction (not applied — user said don't fix)
- Option A: Use real in-memory SQLite DB (like test_child_reports.py)
- Option B: Use `side_effect` to return different values for different queries
- Option C: Inspect SQL in the mock to return appropriate values

## Key Takeaway
When production code adds a NEW database query inside an existing method, tests using MagicMock sessions that return fixed values for ALL queries will break. **Always differentiate queries in mocks** when a method runs multiple queries.

## Status: RESOLVED on commit 81c127b0
The hardening commit changed the predicate to `_has_no_active_message_job = count == 0` (checking ACTIVE jobs, not terminal). With mock returning `1`: `1 == 0 = False` → guard does NOT fire → WAITING_CHILDREN written correctly → tests PASS. See `LESSONS/predicate-inversion-resolves-mock-issue.md` for full analysis.

**Latent risk**: The 3 tests still use the coarse MagicMock. They currently pass because the predicate makes the mock value inert, but a future predicate change could silently break them. Consider converting to real in-memory SQLite for robustness (quality-of-test improvement, not correctness bug).
