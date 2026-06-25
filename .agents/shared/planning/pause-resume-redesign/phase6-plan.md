# Phase 6: E2E & Integration Validation

## Objective

Update the E2E test to validate the new PAUSED state behavior, verify crash recovery handles PAUSED jobs correctly (at the correct location), verify bus watcher recovery doesn't drop PAUSED-instance watchers, add cold-resume TTL test, and run full integration validation to ensure the redesigned system works end-to-end.

## Coupling

- **Depends on**: Phase 1-5 (all prior phases)
- **Coupling type**: loose
- **Shared files with other phases**: E2E test file, crash recovery code, job recovery service
- **Shared APIs/interfaces**: HTTP API endpoints, crash recovery service
- **Why this coupling**: Validates the complete system after all implementation and unit tests are done.

## Context

- The existing E2E test `test_pause_after_spawn_then_resume` (tests/e2e/test_e2e_workflows.py:1064-1185) validates the full pause/resume cycle
- It currently asserts on instance status only — needs job/task PAUSED assertions
- **Job recovery is in `daemon/services/job_recovery_service.py:96-156`** (`JobRecoveryService.recover_on_startup`), NOT in `daemon/api.py:672-803` (which is bus watcher recovery) — reviewer C2
- At line 132-143 of `job_recovery_service.py`, PAUSED instances currently fall into the "alive" branch → job stays PROCESSING
- The bus's in-memory state resets on restart — crash recovery must handle this for PAUSED jobs

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update E2E test with PAUSED job assertions | Modify `test_pause_after_spawn_then_resume` to assert that after pause: (a) the leader's job is PAUSED, (b) the child's job is PAUSED. After resume: (c) jobs return to PROCESSING. Query job status via API or DB. | `tests/e2e/test_e2e_workflows.py:1064-1185` |
| 2 | Update E2E test hold window | The current test holds for 5 seconds after pause to verify no rogue processing. Add assertion that job STAYS PAUSED during the hold window (not completed). | `tests/e2e/test_e2e_workflows.py:1095-1100` |
| 3 | Update E2E test completion check | After resume, verify the job reaches COMPLETED (not just the instance). Verify the workflow completes correctly. | `tests/e2e/test_e2e_workflows.py:1170-1178` |
| 4 | Add E2E test: pause child only | New E2E test: pause a child instance (not parent). Verify only the child's job goes PAUSED, parent stays PROCESSING. Resume child → child job back to PROCESSING. | `tests/e2e/test_e2e_workflows.py` (new test) |
| 5 | Add E2E test: delayed child report | New E2E test: spawn parent + child, pause parent, let child complete (child report queued), resume parent. Verify parent processes the child report and finalizes correctly. | `tests/e2e/test_e2e_workflows.py` (new test) |
| 6 | **Implement job crash recovery for PAUSED** (C2 fix) | In **`daemon/services/job_recovery_service.py:132`** (`JobRecoveryService.recover_on_startup`), add handling at the point where PAUSED instances currently fall into the "alive" branch (line 132-143). If `instance.status == PAUSED` and `job.status == PROCESSING`, transition job to `PAUSED`. This is the correct location — NOT `api.py:672-803` which is bus watcher recovery. | `daemon/services/job_recovery_service.py:96-156` (specifically line 132) |
| 7 | **Fix bus watcher recovery for PAUSED instances** (C4 fix) | In **`daemon/api.py:743-760`** (bus watcher crash recovery), the code calls `_get_processing_job_for_instance` which only returns PROCESSING jobs. For PAUSED instances, this returns None → watchers get stamped as "processed" at line 760 → **silently dropped**. Fix: explicitly SKIP PAUSED-instance jobs (leave watchers for resume). Check instance status before stamping: if instance is PAUSED, skip the watcher (don't stamp as processed). | `daemon/api.py:743-760`, `daemon/services/job_feedback_observer.py:516-535` |
| 8 | Test crash recovery for PAUSED jobs (C2) | Integration test: simulate crash with PAUSED instance + PROCESSING job (the old hack state). On recovery via `job_recovery_service.py`, verify job is transitioned to PAUSED. | `tests/integration/test_crash_recovery_paused.py` (new) |
| 9 | Test bus watcher recovery skips PAUSED instances (C4) | Integration test: simulate crash with PAUSED instance + FIRED bus watchers. On recovery via `api.py`, verify watchers are NOT stamped as "processed" — they survive for resume. | `tests/integration/test_crash_recovery_paused.py` (new) |
| 10 | Verify bus state reset on restart | After crash, the bus's in-memory state (`_parent_errored`, `_parent_error_message`, per-parent Lock dict) resets. Verify PAUSED jobs with preserved watchers survive the restart correctly — the watcher rows in `dependency_watchers` table are persisted. | `tests/integration/test_crash_recovery_paused.py` (new) |
| 11 | **Add cold-resume after TTL eviction test** (S2) | `_cleanup_cached_instances` (`manager.py:1435`) includes PAUSED instances in TTL eviction (24h). After eviction, in-memory graph is released. Test: pause → wait past TTL (mock) → verify resume works from checkpoint (cold-resume). The LangGraph checkpoint is persisted in SQLite/Postgres, so cold-resume should work. | `tests/integration/test_cold_resume_ttl.py` (new) |
| 12 | Run full test suite on PostgreSQL | Run the complete test suite (unit + integration + E2E) against PostgreSQL. Verify all tests pass. | — |
| 13 | Run full test suite on SQLite | Run the complete test suite against SQLite. Verify all tests pass. | — |
| 14 | Performance validation | Run the E2E test and measure: pause latency, resume latency, time to complete after resume. Verify no significant regression. | — |

## Key Files

- `tests/e2e/test_e2e_workflows.py` — E2E test (line 1064)
- **`daemon/services/job_recovery_service.py`** — Job recovery on startup (line 96-156, specifically line 132) — **CORRECT location for C2 fix**
- `daemon/api.py` — Bus watcher crash recovery (line 672-803, specifically line 743-760) — **C4 fix location**
- `daemon/services/job_feedback_observer.py` — `_get_processing_job_for_instance` (line 516-535), `_process_event` (line 698)
- `daemon/repositories/task/repository.py` — `has_inflight_task` (line 149)
- `daemon/manager.py:1435` — `_cleanup_cached_instances` (TTL eviction, includes PAUSED)

## Crash Recovery Design

### Scenario: Upgrade with existing in-flight jobs (C2)

When upgrading to the new PAUSED state, existing databases may have:
- PAUSED instances with PROCESSING jobs (the old hack)
- These need to be reconciled on startup

### Recovery Logic (C2 — at `job_recovery_service.py:132`)

```python
# In JobRecoveryService.recover_on_startup, line ~132:
# For each PROCESSING job:
#   1. Look up the associated instance
#   2. If instance.status == PAUSED:
#      - Transition job PROCESSING → PAUSED (reconciliation)
#   3. If instance.status == RUNNING:
#      - Normal crash recovery (job stays PROCESSING, execution resumes)
#   4. If instance is terminal (COMPLETED, ERROR, TERMINATED):
#      - Finalize the job based on instance status
```

### Bus Watcher Recovery for PAUSED Instances (C4 — at `api.py:743-760`)

```python
# Current (BUGGY for PAUSED):
job = await observer._get_processing_job_for_instance(target_id)  # Returns None for PAUSED
if job is None:
    await bus.mark_enqueued(watch_id)  # Stamps as "processed" → WATCHER DROPPED
    continue

# Fixed:
# Check if instance is PAUSED before stamping
instance = await instance_repo.get(target_id)
if instance and instance.status == 'paused':
    # PAUSED instance — skip, leave watcher for resume
    continue

job = await observer._get_processing_job_for_instance(target_id)
# ... normal flow
```

### Bus State Considerations

On restart after crash:
- `dependency_watchers` rows are persisted in DB → watchers survive
- Bus in-memory state (`_parent_errored`, etc.) resets → known limitation (see critical notes)
- PAUSED jobs with preserved watchers → on resume, watchers are still valid
- Bus watcher recovery must NOT drop watchers for PAUSED instances (C4 fix)

## Constraints

- E2E tests require a running daemon at `localhost:8079` (use `pytest.mark.skipif(not _daemon_running())`)
- E2E test takes ~45 seconds — account for CI time
- Crash recovery tests must use real DB (not mocks) to test actual recovery behavior
- PostgreSQL is the PRIMARY dev/test DB
- **Job recovery is at `job_recovery_service.py`, NOT `api.py`** (C2)

## Deliverables

- [ ] E2E test updated with PAUSED job/task assertions
- [ ] E2E test verifies job stays PAUSED during hold window (no premature completion)
- [ ] New E2E test for pause-child-only scenario
- [ ] New E2E test for delayed child report during pause
- [ ] Job crash recovery at `job_recovery_service.py:132` reconciles PAUSED + PROCESSING → PAUSED (C2)
- [ ] Bus watcher recovery at `api.py:743-760` skips PAUSED instances (doesn't drop watchers) (C4)
- [ ] Cold-resume after TTL eviction works from checkpoint (S2)
- [ ] Bus watcher persistence verified across restart
- [ ] Full test suite passing on PostgreSQL
- [ ] Full test suite passing on SQLite
- [ ] No significant performance regression in pause/resume

## Migration Path Summary

For existing deployments upgrading:

1. **Before upgrade**: May have PAUSED instances with PROCESSING jobs (the old hack)
2. **During upgrade**: Code is updated — no DB schema migration needed (enum values are app-level VARCHAR)
3. **On first startup**: Job recovery at `job_recovery_service.py:132` detects PROCESSING jobs on PAUSED instances → transitions to PAUSED (C2). Bus watcher recovery at `api.py:743` skips PAUSED instances, preserving watchers (C4).
4. **After startup**: New pause/resume flow is active with first-class PAUSED state

This is a **zero-downtime-compatible** upgrade because:
- No schema change required (enum values are app-level VARCHAR)
- No data transformation needed for existing rows
- Recovery logic handles the transition state
- Existing PROCESSING jobs remain valid
