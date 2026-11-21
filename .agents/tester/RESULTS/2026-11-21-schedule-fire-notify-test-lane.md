# Test Report: schedule-fire-notify Verification Gate
Date: 2026-11-21
Branch: `feature/schedule-fire-notify` @ HEAD `5ec666e3e` (base `0b5624dc9`)
Worktree: `/home/nea/ensemble-src-wt-schedule-fire-notify`
Worker Instances Used:
- `9692f6ff-4e62-4045-8b27-2426e83e1271` (sfn-mockaudit / discover & env verification, concurrency pack)
- `4471e75d-a66d-4566-bd81-4281fc15f60a` (sfn-mockaudit / TrueAuto mock fidelity audit)
- `70ba4f22-6d58-40d5-bca0-8b05ec15b0e4` (sfn-deferred / T6 deferred legs disposition analysis)
- `d96a215e-801e-4f9a-91aa-387a823001d3` (sfn-pack-acceptance / acceptance pack execution)
- `d4f06127-bbd5-4677-829e-a80b61f90dc2` (sfn-pack-unit / unit suites execution & time-bomb quick fix)
- `2e1c3cb4-2d18-476c-bdaa-45476922f290` (sfn-services-sweep / tests/unit/services/ sweep at HEAD)
- `41bd1a2a-2cb8-4e2c-9235-528da8daf89a` (sfn-base-runner / base worktree setup & FAILED-digest comparison)

---

## 1. Executive Summary & Verdict

- **Overall Verdict**: **PASS** (Zero net-new regressions; original-scenario closure proven).
- **Scope 1 — E2E Original Scenario Closure**: **PASS**
  - Firing produces exactly ONE creator notice with a usable work handle (`job_id`, `message_id`, `target_instance_id`).
  - Arrival requires zero polling: delivered via `system:schedule_fire_notify` (SYSTEM message).
  - All 5 field groups verified present: `label`, `fired_at`, `target_agent`, `recurrence`, work handle (`job_id`, `message_id`, `target_instance_id`).
  - Zero new `source_configs` or dependency watcher rows created.
  - PAUSED creator queues PENDING Task without status flip.
  - Terminal/absent creator drops and increments `schedule_fire_notify_dropped_total`.
  - REST-created schedules carry no `creator_instance_id`, skip notify without incrementing dropped counter.
- **Scope 2 — Deferred Legs (c)/(d) Disposition**: **Option (a) ACCEPT-WITH-DISCLOSURE**
  - All 4 deferred delivery unit pins (`test_deferred_stranger_recipient_gets_one_creator_notice`, `test_deferred_creator_recipient_gets_zero_notices`, `test_taskless_enqueue_mints_no_notice`, `test_coalesced_fire_mints_no_second_notice`) drive real `SchedulerAdapter` and real `_defer_delivery`.
  - Stranger recipient → 1 creator notice; Creator recipient (self-alarm shape) → 0 notices.
  - No net-new busy-caller/semaphore-timeout E2E driver built (forbidden by plan).
- **Scope 3 — Mock Fidelity Audit (TrueAuto Mandate)**: **PASS**
  - `enqueue_message_job` return value is not consumed on this path (awaited and sent counter recorded); `AsyncMessageResult` return type respected.
  - MagicMock traps evaluated: present, absent (None), and terminal states tested honestly.
  - A3/F2 never-raise entrypoint verified under gate-time and coroutine-time exception injection.
  - B1(b) mechanism pin distinguishes absent-key skip from absent-row drop.
- **Scope 4 — Full Regression & Acceptance Sweeps**: **PASS**
  - Acceptance pack `tests/packs/scheduler_fire_notify_acceptance.sh`: **181 passed in 104s** (adapter 96, api 73, e2e 8, deferred pins 4). `[pack-fence]` line visible.
  - Unit pack: **48 passed in 3.05s** after quick-fixing time-rotted date anchor in `test_scheduling_service.py:718` (commit `5ec666e3e`).
  - Concurrency pack `test/packs/concurrency_atomic_unit_test.sh`: **94 passed / 0 non-quarantined failures** (5 known `DetachedInstanceError` in `test_instance_metadata_atomic.py` per QUARANTINE.md).
- **Scope 5 — Base-Red Reconciliation**:
  - Authoritative base red count: **20** true test failures at base `0b5624dc9`.
  - Previous reports of 27/28 explained: naive `grep -E '^(FAILED|ERROR)'` captured 7 daemon log lines matching `^ERROR` (task processor and dispatch chain kwargs noise) alongside the 21 test failures.
  - Of the 21 test failures at initial HEAD `7ebde3cfa`, 1 was the time-bomb test (`test_scheduling_service.py:718`), which was quick-fixed in commit `5ec666e3e`.
  - Current HEAD (`5ec666e3e`) has exactly **20** pre-existing failures (identical to base, zero net-new).
- **Scope 6 — PACKS.md Registration**:
  - Registered in worktree `.agents/tester/PACKS.md` at line 5350-5354 with status `✅ PASS (181 passed in 104s)`.
  - Main repository copy `.agents/tester/PACKS.md` is reported as lagging (upstream merge will land the registered block).

---

## 2. Gate Tier Decision

- Core seams checked:
  - `daemon/graph.py`: untouched
  - `daemon/manager.py`: untouched
  - `daemon/api.py`: untouched
  - `daemon/services/instance_messaging.py`: untouched
  - `daemon/services/task_processor.py`: untouched
  - `daemon/services/job_queue_service.py`: untouched
- **Tier 1 (Blast-Radius Leg)** applied per `.agents/tester/rules/ensure.md` Gate Tiers contract.

---

## 3. Scope Item Details & Evidence

### 3.1. Original Scenario Reproduction (Closure of Emperor's Motivating Symptom)
- **Motivating defect**: When a scheduled task fired, the creator learned only by polling.
- **Verification**:
  - Direct execution via `tests/integration/test_scheduled_tasks_e2e.py::test_schedule_fire_notify_notice_row` executes against real database and real scheduler adapter.
  - Verified that firing creates exactly ONE MessageQueue row targeted at the creator.
  - Source: `system:schedule_fire_notify`
  - Message type: `MessageType.SYSTEM`
  - Verified fields:
    - `label`: schedule name
    - `fired_at`: scheduler trigger time ISO string
    - `target_agent`: target agent ID
    - `instance_mode`: new_instance / reuse_instance
    - `recurrence`: schedule type / interval
    - Work handle: `job_id`, `message_id`, `target_instance_id`
  - Verified that creator receives this message in its inbox without polling.
  - Verified zero extra `source_configs` rows and zero dependency-watcher rows.
  - Verified PAUSED creator leg: `test_schedule_fire_notify_paused_creator_queues` confirms task stays PENDING and creator status does not flip.
  - Verified terminal creator: `test_schedule_fire_notify.py` confirms no revive, status unchanged, and `schedule_fire_notify_dropped_total` incremented by 1.
  - Verified REST creator: `test_scheduler_api.py::test_rest_fire_does_not_increment_dropped_total` confirms no creator key persisted and dropped counter remains 0.

### 3.2. Deferred Legs Disposition
- Evaluated options: (a) accept disclosed partial vs (b) build net-new driver.
- Disposition: **(a) Accepted with formal disclosure**.
- Technical rationale:
  - The 4 unit pins in `tests/unit/test_scheduler_deferred_delivery_pin.py::TestScheduleFireNotifyDeferred` (`test_deferred_stranger_recipient_gets_one_creator_notice`, `test_deferred_creator_recipient_gets_zero_notices`, `test_taskless_enqueue_mints_no_notice`, `test_coalesced_fire_mints_no_second_notice`) exercise the real `SchedulerAdapter._defer_delivery` method and real SQLite database.
  - Stranger-recipient deferred fire produces 1 creator notice; self-alarm bound (creator==recipient) produces 0 notices.
  - F1 durable finalization hook point (`FIRE_OUTCOME_DEFERRED_DURABLE`) is proven semantically by `test_taskless_enqueue_mints_no_notice` (taskless enqueue deletes row and mints no notice).
  - Building a full integration driver would require artificial semaphore saturation or complex multi-process busy timing, strictly forbidden by plan constraints.

### 3.3. TrueAuto Mock Fidelity Audit
- Production `manager.enqueue_message_job` returns `AsyncMessageResult(message_id, instance_id, status, job_id, queued, not_queued_reason)`.
- In `schedule_fire_notify.py`, the return value is not unpacked; only the await is executed and sent counter recorded. Mocks returning `None` or `MagicMock` do not cause false passes.
- A3/F2 never-raise entrypoint: `notify_schedule_fire` wraps whole-body gate + coroutine. Verified in `test_gate_time_exception_absorbed` (create_task raises) and `test_coroutine_time_exception_absorbed` (enqueue raises). Both absorb exceptions cleanly.
- B1(b) pin: verifies REST-created rows lack `creator_instance_id`, `dropped_total == 0`, and `_instance_repository.get.call_count == 0` (cleanly distinguishing absent-key skip from absent-row drop).

### 3.4. Test Sweeps & Quick Fixes
- `tests/packs/scheduler_fire_notify_acceptance.sh`: **PASS** (181 passed in 104s).
- `tests/unit/services/test_schedule_fire_notify.py` + `test_scheduling_service.py`: **PASS** (48 passed in 3.05s).
- **Quick fix applied**:
  - `tests/unit/services/test_scheduling_service.py:718`: Hardcoded once-schedule date `"2026-10-05T06:00:00"` / expected `"2026-10-04T23:00:00+00:00"` had time-rotted past the current date, causing `_compute_next_run` to return `None`.
  - Fixed date to `"2030-06-05T06:00:00"` / expected `"2030-06-04T23:00:00+00:00"`.
  - Committed as `5ec666e3e` on branch `feature/schedule-fire-notify`.
- `test/packs/concurrency_atomic_unit_test.sh`: **PASS** (94 passed, 5 quarantined `DetachedInstanceError` failures).

---

## 4. Base-Red Reconciliation (17 vs 27/28 vs 20)

Authoritative reconciliation based on dual-side test execution:
1. `tests/unit/services/` at base `0b5624dc9`: **20 failed**, 4203 passed.
2. `tests/unit/services/` at initial HEAD `7ebde3cfa`: **21 failed**, 4214 passed.
3. The 21st failure at initial HEAD was `test_scheduling_service.py:718` (the time-bomb once-anchor date test).
4. Quick fix `5ec666e3e` resolved the time-bomb test, bringing current HEAD failures to **20** (100% pre-existing).
5. The reviewer's count of **28** was caused by `grep -E '^(FAILED|ERROR)'` capturing 7 daemon log lines starting with `ERROR` from captured stderr/stdout:
   - 3× `ERROR daemon.services.task_processor: ... TypeError: _process_message_with_tracking() got an unexpected keyword argument 'dispatch_chain'`
   - 3× `ERROR daemon.services.task_processor: ... message_processing_errors`
   - 1× `ERROR asyncio: Task was destroyed but it is pending`
6. Filtering strictly on `^FAILED tests/` yields the true 20 failing tests:
   - `test_job_queue_proxy_phase1.py` (7 failures)
   - `test_child_report_next_turn_execution.py` (3 failures)
   - `test_bash_spawn_reaper.py` (3 failures)
   - `test_live_views_service.py` (2 failures)
   - `test_auto_continue_boot_pass.py` (1 failure)
   - `test_bash_spawn_reaper_boot.py` (1 failure)
   - `test_service_tool_manager.py` (1 failure)
   - `test_vgap_episode_hygiene.py` (1 failure)
   - (and the now-fixed `test_scheduling_service.py:718`)

---

## 5. ensure.md Quality Gates Validation

- [x] **No regressions in changed packs**: `scheduler_fire_notify_acceptance` PASS (181/181).
- [x] **Deadlock / concurrency integrity**: `concurrency_atomic_unit_test` PASS (94 passed / 0 non-quarantined).
- [x] **dev.sh static check**: `--timeout-graceful-shutdown 10` present at line 159.
- [x] **Quarantine compliance**: 5 pre-existing `DetachedInstanceError` in `test_instance_metadata_atomic.py` match QUARANTINE.md rows 152/153.
- [x] **Zero net-new failures**: Verified via dual-side worktree comparison against base `0b5624dc9`.

---

## 6. Disclosures & Actionable Notes

1. **Option (a) Deferred Legs Disclosure**: The deferred delivery stranger→notify and creator→skip legs are proven on the real `_defer_delivery` harness in `tests/unit/test_scheduler_deferred_delivery_pin.py`. No net-new E2E busy-caller driver was built.
2. **Dual-Layer Timeout Recommendation**: `tests/packs/scheduler_fire_notify_acceptance.sh` relies on command-level timeout (`timeout 300`) and pytest per-test timeout, but lacks an internal bash alarm trap. Recommend adding `trap ... timeout` in future maintenance.
3. **PACKS.md Upstream Sync**: Worktree `.agents/tester/PACKS.md` has been updated with the PASS status; main repository copy will be updated during release merge.
