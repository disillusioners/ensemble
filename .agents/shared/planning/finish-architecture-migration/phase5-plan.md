# Phase 5: Guard Removal + Regression Invariants

## Objective

Remove the now-redundant `_has_no_active_message_job` defense-in-depth guard and its tests (its premise — separate JobItem lifecycle for messages — no longer holds after D13+D11), and extend `TestBusSoleAuthority` with the single-record invariant that prevents regression. Remove the Phase 0 xfail marker for the D13 invariant test.

## Coupling

- **Depends on**: Phase 2 (D13) + Phase 2.5 (consumption-site rewrites) + Phase 3 (D11) + Phase 4 (dispatch_path removed) — **tight coupling**
- **Coupling type**: tight — the guard is only redundant when MESSAGE JobItems no longer exist; the invariant test can only pass after the dispatch path is unified
- **Shared files with other phases**: `child_reports.py` (guard removal), test files
- **Shared APIs/interfaces**: `_has_no_active_message_job` is a private method — its removal affects 4 call sites
- **Why this coupling**: The guard checks for active `job_queue_items` rows with `job_type="message"`. After D13, such rows never exist. The guard always returns `True` (no active jobs), making it a no-op. Removing it is safe only after verifying the invariant holds.

## Context

### The `_has_no_active_message_job` Guard

**Location**: `daemon/services/child_reports.py:348-429`

**What it checks**: `SELECT COUNT(*) FROM job_item WHERE instance_id=? AND job_type='message' AND deleted_at IS NULL AND status IN ('pending','processing') == 0`

**Why it was kept** (per cleanup plan Task 6.2, 2026-06-24): Defense-in-depth guard that runs before writing `WAITING_CHILDREN` instance status. It catches the task-claim race where a `task.claim_pending_task` call claims a task but the corresponding `JobItem` ends up terminal/soft-deleted before any worker picks it up.

**Call sites** (4, all in `child_reports.py`):
- Line 966 — parent carve-out for terminal job
- Line 1399 — parent error reporting
- Line 1523 — child status write
- Line 1806 — parent carve-out for terminal job

**Tests to delete**: `tests/unit/services/test_child_reports.py` — any tests specifically for `_has_no_active_message_job` behavior (search for the method name or `message_job` references in the test file). These tests set up MESSAGE JobItems to verify the guard, which is no longer possible after D13. If no such tests exist, this is a no-op.

**Post-D13 state**: After D13, no `job_queue_items` rows with `job_type="message"` are ever created. The `COUNT(*)` query always returns 0, so `_has_no_active_message_job` always returns `True`. It is a no-op.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| **5.1** | Extend `TestBusSoleAuthority` with single-record invariant | Add a test that verifies: after a user message is enqueued (via `enqueue_message`), exactly ONE `task` row exists for the message and ZERO `job_queue_items` rows with `job_type="message"` exist. This is the structural regression guard that replaces the `_has_no_active_message_job` guard. **Remove xfail from Phase 0 test 0.4** (D13 invariant) if it now passes. | `tests/unit/test_dependency_bus.py` (or `tests/integration/test_bus_sole_authority.py`) |
| **5.2** | Review `_has_no_active_message_job` call sites | Examine all 4 call sites in `child_reports.py`. For each, determine: (a) what the guard was protecting against, (b) whether the bus (`count_pending_for_target`) now covers that case, (c) whether removal is safe. Document findings. | `daemon/services/child_reports.py:966, 1399, 1523, 1806` |
| **5.3** | Remove `_has_no_active_message_job` guard, method, and tests | Remove the method definition (`child_reports.py:348-429`), all 4 call sites, and **delete any tests for `_has_no_active_message_job` in `tests/unit/services/test_child_reports.py`** (W3). Search the test file for references to the method name or `message_job` — if such tests exist, delete them. Replace any guard conditions that were gating logic with the bus equivalent (`bus.count_pending_for_target(instance_id) > 0`) if needed. If the guard was purely additive (never blocked on `False`), simply remove it. | `daemon/services/child_reports.py:348-429, 966, 1399, 1523, 1806`, `tests/unit/services/test_child_reports.py` |
| **5.4** | Add D13 acceptance grep | Verify the acceptance criteria: `grep -rn 'job_type.*==.*"message"\|dispatch_path.*jobqueue\|_has_no_active_message_job' daemon/ --include="*.py"` returns 0 hits in source code. | — |
| **5.5** | Review `job_queue_items.job_type` column removability | After D13+D11, the `job_type` column on `job_queue_items` only has the value `"task"`. Assess whether the column can be removed (separate migration). **Decision**: Document assessment; do NOT remove in this phase (column removal is a separate migration with its own risk profile). Note in plan that it's a future cleanup candidate. | `daemon/repositories/job_queue/models.py` |
| **5.6** | Update `test_watch_job_integration.py` and `test_jq_error_reporting.py` | **W3**: `TestMixedMessageAndJob` class in `test_watch_job_integration.py` may need adjustment (it tests message+job correlation). `test_jq_error_reporting.py` references MESSAGE job error reporting — verify it still works without JobItem. | `tests/test_watch_job_integration.py`, `tests/test_jq_error_reporting.py` |
| **5.7** | Run full test suite — all phases green | **W3**: This is the final gate. Run `pytest tests/ -x` on PostgreSQL. ALL 17 modified test files must pass. This is the point where Phase 0's acceptance test should be fully green (all xfails removed). | — |

## Key Files

- `daemon/services/child_reports.py` — `_has_no_active_message_job` (348-429), 4 call sites (966, 1399, 1523, 1806)
- `tests/unit/services/test_child_reports.py` — delete `_has_no_active_message_job` tests (438-490)
- `tests/unit/test_dependency_bus.py` — `TestBusSoleAuthority` test class
- `tests/test_watch_job_integration.py` — `TestMixedMessageAndJob` class
- `tests/test_jq_error_reporting.py` — MESSAGE job error reporting tests
- `tests/e2e/test_06f500af_bug_class_eliminated.py` — remove final xfail markers

## Constraints

- **Guard removal is ONLY safe after D13+D11+dispatch_path removal**: The guard's premise (active MESSAGE JobItems) no longer holds. But verify with the invariant test first.
- **Bus coverage**: The bus's `count_pending_for_target` is the correct replacement for the guard's "is there still pending work" check. Verify all 4 call sites are either purely additive (safe to remove) or already have a bus check in the same code path.
- **W3 — Test deletion**: `tests/unit/test_child_reports.py:438-490` tests specifically verify `_has_no_active_message_job`. These MUST be deleted (not updated) since the method no longer exists and its premise (MESSAGE JobItems) is gone.
- **Do NOT remove `job_queue_items.job_type` column in this phase**: Column drops require separate migration + PostgreSQL handling. Document as future work.
- **Full test suite must pass** — this is the final gate before the migration is declared complete.

## Deliverables

- [ ] `TestBusSoleAuthority` extended with single-record invariant test
- [ ] `_has_no_active_message_job` method, all 4 call sites, and any related tests removed
- [ ] `test_watch_job_integration.py` and `test_jq_error_reporting.py` updated
- [ ] D13 acceptance grep returns 0 hits
- [ ] `job_queue_items.job_type` column removability assessed and documented
- [ ] Phase 0 acceptance test fully green (all xfails removed)
- [ ] Full test suite passes on PostgreSQL (all 17 modified files)
