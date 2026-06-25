# Phase 5: Test Suite Migration

## Objective

Update all 213+ existing tests across 18 files to account for the new PAUSED state for jobs and tasks. Add new tests specifically for the redesigned pause/resume flow. Ensure all tests pass on both SQLite and PostgreSQL.

## Coupling

- **Depends on**: Phase 1-4 (all core implementation phases) — **STRICT dependency on Phase 4** (W4)
- **Coupling type**: loose (but strictly sequential after Phase 4)
- **Shared files with other phases**: Test files that verify behavior from all phases
- **Shared APIs/interfaces**: Test fixtures, `_seed_instance`, `_seed_job`, `_seed_child_task`
- **Why this coupling**: Tests verify the implementation from Phases 1-4. The 19 cascade tests (Section 5A Task 6, 8) specifically depend on Phase 4's cascade behavior being complete. Phase 5 CANNOT start until Phase 4 is done.

## Context

- 213 tests across 18 files touch pause/resume/cancellation
- Many tests assert on instance status but don't yet check job/task status
- The E2E test `test_pause_after_spawn_then_resume` needs PAUSED state assertions
- PostgreSQL tests in `tests/postgres/` must also be updated
- Some tests are currently skipped (`@pytest.mark.skip(reason="Phase 5: DependencyBus not initialized")`) — these may need updating

## Tasks

### 5A: Update Existing Test Files

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `TestPauseSafety` (5 tests) | Add assertions for job/task PAUSED state. Current tests only check task claim is blocked; add assertions that job transitioned to PAUSED. | `tests/test_report_lane_phase2.py:685-799` |
| 2 | Update `TestPauseSafetyPG` (4 tests) | PostgreSQL equivalents of TestPauseSafety. Add same job/task PAUSED assertions. | `tests/postgres/test_report_lane_phase2_pg.py:165-259` |
| 3 | Update `test_cancellation.py` (48 tests) | Verify cancellation tests still work with PAUSED state. Ensure PAUSED jobs are NOT cancelled by CANCELLED transitions (they must be resumed first or explicitly cancelled). | `tests/test_cancellation.py` |
| 4 | Update `test_write_pause_guard.py` (25 tests) | Verify write pause guard tests account for PAUSED job/task state. | `tests/unit/test_write_pause_guard.py` |
| 5 | Update `test_paused_instance_ttl.py` (20 tests) | TTL eviction tests. Verify PAUSED jobs are handled correctly during TTL eviction — should they be transitioned or evicted? | `tests/unit/test_paused_instance_ttl.py` |
| 6 | Update `test_pause_instance_cascade.py` (19 tests) | Add job/task PAUSED assertions for cascade pause/resume. Some tests are currently skipped — un-skip and fix. | `tests/unit/test_pause_instance_cascade.py` |
| 7 | Update `test_cancellation_cascade.py` (16 tests) | Verify cancellation cascade doesn't affect PAUSED jobs unless explicitly cancelled. | `tests/job_queue/test_cancellation_cascade.py` |
| 8 | Update `test_tree_aware_pause_resume.py` (10 tests) | Add job/task assertions to tree-aware tests. Verify deep hierarchy cascade. | `tests/unit/test_tree_aware_pause_resume.py` |
| 9 | Update `test_graph_task_cancellation.py` (9 tests) | Verify graph task cancellation during pause does NOT complete the job (job stays PAUSED). | `tests/test_graph_task_cancellation.py` |
| 10 | Update `test_child_resume.py` (8 tests) | Verify child resume with PAUSED job state. | `tests/unit/test_child_resume.py` |
| 11 | Update `test_instance_pause.py` (8 tests) | Verify instance pause with PAUSED job/task state. | `tests/job_queue/test_instance_pause.py` |
| 12 | Update remaining test files | Update `test_pause_terminate_matrix.py` (7), `test_resume_gate.py` (7), `test_resume_waiting_children.py` (7), `test_resume_child_notification.py` (7), `test_resume_message_append.py` (6), `test_pause_while_processing.py` (2), `test_multi_turn_resume.py` (3). | Various test files |

### 5B: Add New Tests

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 13 | Test job PROCESSING → PAUSED transition | Unit test: pausing an instance with PROCESSING job → job becomes PAUSED. | `tests/unit/test_pause_flow_redesign.py` (new) |
| 14 | Test task RUNNING → PAUSED transition | Unit test: pausing an instance with RUNNING task → task becomes PAUSED. | `tests/unit/test_pause_flow_redesign.py` (new) |
| 15 | Test bus watchers preserved during pause | Unit test: pause does NOT cancel bus watchers (dependency_watchers stay PENDING). | `tests/unit/test_pause_flow_redesign.py` (new) |
| 16 | Test job PAUSED → PROCESSING on resume | Unit test: resuming transitions job back to PROCESSING. | `tests/unit/test_resume_flow_redesign.py` (new) |
| 17 | Test task PAUSED → PENDING on resume | Unit test: resuming transitions task to PENDING for re-claim. | `tests/unit/test_resume_flow_redesign.py` (new) |
| 18 | Test no premature completion on resume | Integration test: resume does NOT call complete_job directly. Verify finalize goes through _process_event. | `tests/unit/test_resume_flow_redesign.py` (new) |
| 19 | Test delayed child report processed after resume | Integration test: child completes during pause → PROCESS_REPORT task blocked → resume → task claimed → report processed. | `tests/integration/test_delayed_child_report.py` (new) |
| 20 | Test zombie job elimination | Unit test: resume does NOT create new jobs, reuses existing PAUSED job. | `tests/unit/test_resume_flow_redesign.py` (new) |
| 21 | Test no-op resume does NOT leave job stuck PROCESSING (C1 regression) | Unit test: resume where graph turn is a no-op (all children already reported) → `_process_resume_finalize` fires → job reaches correct terminal state. Verify A9 hard-error for `bus is None`. | `tests/unit/test_resume_flow_redesign.py` (new) |
| 22 | Test crash recovery for PAUSED jobs | Unit test: startup with PAUSED instance + PROCESSING job → job transitioned to PAUSED. | `tests/unit/test_crash_recovery_paused.py` (new) |
| 23 | Test PostgreSQL PAUSED state transitions | PG-specific tests for PAUSED state transitions. | `tests/postgres/test_paused_state_pg.py` (new) |
| 24 | **Test serialization guard edge case** (S3) | Unit test: RUNNING instance + 1 PAUSED task + 1 PENDING task → PENDING task CAN be claimed (PAUSED task does NOT block sibling). Verify the serialization guard at repository.py:315-318 only blocks on `status='running'`. | `tests/unit/test_pause_flow_redesign.py` (new) |
| 25 | Test PAUSED → CANCELLED transition (W3) | Unit test: terminate a PAUSED instance → job transitions PAUSED → CANCELLED, locks released, bus watchers cancelled. | `tests/unit/test_paused_state.py` or `tests/test_cancellation.py` |
| 26 | Test new-message-during-pause queuing (W5) | Unit test: message arrives for PAUSED instance → Task created PENDING → claim blocked → resume → task claimed and processed. | `tests/unit/test_pause_flow_redesign.py` (new) |

### 5C: Fix Skipped Tests

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 27 | Un-skip DependencyBus tests | Many tests in `test_pause_instance_cascade.py` and `test_tree_aware_pause_resume.py` are skipped with "Phase 5: DependencyBus not initialized". Fix and un-skip these. | `tests/unit/test_pause_instance_cascade.py`, `tests/unit/test_tree_aware_pause_resume.py` |

## Key Files

- `tests/test_report_lane_phase2.py` — TestPauseSafety (line 685)
- `tests/postgres/test_report_lane_phase2_pg.py` — TestPauseSafetyPG (line 165)
- `tests/test_cancellation.py` — 48 cancellation tests
- `tests/unit/test_write_pause_guard.py` — 25 write pause guard tests
- `tests/unit/test_paused_instance_ttl.py` — 20 TTL tests
- `tests/unit/test_pause_instance_cascade.py` — 19 cascade tests
- `tests/unit/test_tree_aware_pause_resume.py` — 10 tree-aware tests
- `tests/test_graph_task_cancellation.py` — 9 graph task cancellation tests
- `tests/job_queue/test_instance_pause.py` — 8 instance pause tests
- `tests/migration/test_jsonb_migration.py` — Migration tests (may need PAUSED state additions)
- `tests/e2e/test_e2e_workflows.py` — E2E test (line 1064)

## Test Fixtures to Update

The following fixtures and helpers need updating to support PAUSED state:
- `_seed_instance(engine, *, status=RUNNING)` — `tests/test_report_lane_phase2.py:116` — Add job/task seeding when status=PAUSED
- `_seed_job(engine, *, status=PROCESSING)` — `tests/test_report_lane_phase2.py:140` — Support PAUSED status
- `_seed_child_task(engine, *, child_instance_id)` — `tests/test_report_lane_phase2.py:175` — Support PAUSED status

## Constraints

- All tests must pass on both SQLite and PostgreSQL
- PostgreSQL is the PRIMARY dev/test DB — run tests against PostgreSQL first
- Tests must use the repository pattern for dual-driver support
- New tests should follow existing fixture patterns (`_seed_instance`, `_seed_job`, etc.)
- E2E tests require a running daemon at `localhost:8079`

## Deliverables

- [ ] All 213+ existing tests updated to account for PAUSED state
- [ ] New tests for pause flow redesign (job/task transitions, bus watcher preservation)
- [ ] New tests for resume flow redesign (no premature completion, no-op resume C1)
- [ ] New tests for crash recovery with PAUSED jobs
- [ ] Serialization guard edge case test (S3: RUNNING + PAUSED + PENDING)
- [ ] PAUSED → CANCELLED transition test (W3)
- [ ] New-message-during-pause queuing test (W5)
- [ ] Skipped DependencyBus tests fixed and un-skipped
- [ ] All tests passing on SQLite
- [ ] All tests passing on PostgreSQL
- [ ] Test fixtures updated to support PAUSED state seeding

## Notes

- This is the largest phase by test count but lowest in architectural risk
- The key challenge is finding all assertion sites that need PAUSED state checks
- Start with the highest-value tests: TestPauseSafety, TestPauseSafetyPG, E2E test
- Many tests may pass unchanged if they only test instance-level behavior (not job/task)
- Focus on tests that make assertions about job or task status during pause/resume
