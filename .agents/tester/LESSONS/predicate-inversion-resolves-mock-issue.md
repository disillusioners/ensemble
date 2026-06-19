# Lesson: Predicate Inversion Resolved Mock Granularity Issue

**Date**: 2026-06-18
**Commits**: `81c127b0` (hardening), `547a0f0f` (initial fix)
**Files affected**: `tests/unit/test_root_instance_completion.py`, `tests/test_phase4_deprecation.py`

## The Issue (Round 1)
On commit `547a0f0f`, the carve-out guard used predicate `_terminal_job_exists = count > 0`:
- `create_session_with_pending()` returns MagicMock with `scalar_one = MagicMock(return_value=1)`
- Mock returns `1` for ALL queries, including the terminal-job count
- `1 > 0 = True` → guard FALSELY fires → skips WAITING_CHILDREN write → test FAIL

## The Resolution (Round 2)
Commit `81c127b0` hardened the guard to `_has_no_active_message_job = count == 0`:
- Same mock returns `1` for ALL queries
- `1 == 0 = False` → guard does NOT fire → WAITING_CHILDREN written → test PASS

The predicate inversion (from `count > 0` meaning "exists" to `count == 0` meaning "no active") made the mock's fixed return value (1) semantically inert for these test scenarios.

## Key Insight
When production code adds a NEW database query inside an existing method, and the guard predicate is later changed, the interaction between mock granularity and predicate logic can be non-obvious:
- A mock returning a fixed value for all queries may PASS or FAIL depending on the predicate's truth table.
- **This is fragile**: if the predicate changes again, these mocks could break.

## Best Practice
The companion test `test_child_reports.py::test_normal_path_sets_waiting_children_when_job_processing` uses a **real in-memory SQLite DB** which naturally returns correct values per query. This is the robust pattern — it survives predicate changes.

## Latent Risk
The 3 affected tests still use the coarse MagicMock. They currently pass because the predicate happens to make the mock value inert. A future predicate change could silently break them. Consider converting `create_session_with_pending()` to use real in-memory SQLite for long-term robustness (quality-of-test improvement, not a correctness bug).

## Status: RESOLVED (tests pass, but underlying mock fragility remains)
