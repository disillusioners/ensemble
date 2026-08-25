# Research: Watcher/Obligation/Report-Delivery Semantics for B2+B3

Date: 2026-08-24
Author: plan-explorer-obligations (61f012ba) — recovered by planner from the explorer's final report after a silent file-write failure

## Verified Claims

| Claim | Verdict | Evidence |
|-------|---------|----------|
| WATCHER lifecycle states (PENDING→FIRED, PENDING→CANCELLED) are terminal (no further transitions) | ✓ | daemon/repositories/dependency_bus/models.py:49-71 |
| Watcher DB backing: single table `dependency_watchers` with columns `state`, `fired_at`, `enqueued_at` | ✓ | daemon/repositories/dependency_bus/models.py:84-126 |
| Watcher registration via `DependencyBus.watch` inserts PENDING row and updates in-memory cache | ✓ | daemon/services/dependency_bus.py:450-549 |
| Terminal events fire watchers via `emit_terminal`: fetches PENDING for source, atomically transitions each to FIRED with `fired_at`, returns FollowUps for enqueueing | ✓ | daemon/services/dependency_bus.py:551-712 |
| Atomic backpressure: repo `transition_state` uses `WHERE state='PENDING'` Core UPDATE; returns True iff transitioned | ✓ | daemon/repositories/dependency_bus/repository.py:462-536 |
| Cancel path: same `transition_state` with new_state='CANCELLED', fired_at=None | ✓ | daemon/repositories/dependency_bus/repository.py:462-536 |
| `_compact_fired_watchers_for_paused` lives in instance_lifecycle.py:3608-3696 | ✓ | instance_lifecycle.py:3608-3696 |
| Compaction deletes FIRED rows where `enqueued_at IS NOT NULL` AND `fired_at <= cutoff` (60s grace) | ✓ | instance_lifecycle.py:3666-3679 |
| Compaction original intent: bound FIRED growth during long partial-tree pauses; Phase 2 Decision 3 (C3) | ✓ | instance_lifecycle.py:3609-3647 docstring |
| Resume `invalid_or_missing_handle` when no `resume_target_turn_id` (SuspendTurn handle) | ✓ | instance_lifecycle.py:2325-2328 + logs |
| Resume flips PAUSED→PENDING without JobItem check → drift if cancelled mid-pause | ✓ | instance_lifecycle.py:3821-3826 |
| JobItem cancelled mid-pause → Task PAUSED; drift reconciler has no paused-on-terminal pattern | ✓ | job_recovery_service.py:528-534 |
| `report_injection` table + partial unique index `uq_report_injections_oblig_triple` | ✓ | migration 20260624_000004_report_injection_partial_unique.sql |
| `claim_for_injection` called at graph dispatch | ✓ | grep call sites in dispatch |
| ReportDeliveryRecoveryService 5-lane sweep exists; lanes not enumerated this pass | PARTIAL | daemon/services/report_delivery_recovery.py (read end-to-end during implementation) |
| B3: terminate cancels parent-side watcher via repo transition_state to CANCELLED | ✓ | repository.py:462-536; cancel helpers in repo layer |
| B3: parent logs `waiting for N children (bus=True), deferring completion` | ✓ | daemon/services/child_reports.py (grep confirmed) |
| Firing watcher with terminal outcome would use `emit_terminal` with Outcome(status=completed/error/terminated) | ✓ | dependency_bus.py:551-712 |
| Parent consumes FollowUp via `claim_for_injection` or direct PROCESS_REPORT to unblock waiting_children | ✓ | child_reports.py |
| Safeguard (a) `_is_instance_paused()` before `_check_child_completion` | ✓ | child_reports.py |
| Safeguard (b) child_reports.py PAUSED idempotency guards ~:1244, ~:898, ~:1845 | ✓ | child_reports.py:1244, 898, 1845 |
| Safeguard (c) job_feedback_observer `_TERMINAL_INSTANCE_STATUSES` includes PAUSED | ✓ | job_feedback_observer.py |
| Other PAUSED-aware guards in error_reporting.py, job_feedback_observer.py | ✓ | grep "PAUSED" |
| Revive semantics: send_message revives COMPLETED/TERMINATED/ERROR/FAILED | ✓ | instance_messaging.py:1486-1510 |
| Prior art: pause-report-recovery + fix-pause-report-turn-orphan plans exist | ✓ | .agents/shared/planning/{pause-report-recovery,fix-pause-report-turn-orphan}/ |

## Watcher Lifecycle State Machine

- States: PENDING (initial), FIRED (child terminated), CANCELLED (parent stopped before child). All terminal after first transition. (models.py:49-71)
- Transitions: atomic via `repo.transition_state(watch_id, new_state, fired_at?)` with WHERE state='PENDING'. (repository.py:462-536)
- Fire path: `emit_terminal(task_id, outcome)` → fetch PENDING for source → transition to FIRED with fired_at=now → return FollowUps. (dependency_bus.py:551-712)
- Cancel path: same transition_state with new_state='CANCELLED', fired_at=None. (repository.py:462-536)
- Compaction: `_compact_fired_watchers_for_paused(instance_id)` deletes FIRED rows where enqueued_at IS NOT NULL and fired_at <= cutoff (60s grace). (instance_lifecycle.py:3666-3679)

## Resume Path Anatomy

- Route filters instances with status='paused' only (instance_lifecycle.py:2325-2328).
- Blindly flips PAUSED→PENDING in DB without checking JobItem state (:3821-3826).
- No SuspendTurn handle because pause does not suspend at a boundary → outcome invalid_or_missing_handle → no dispatch.
- JobItem cancelled mid-pause leaves Task PAUSED; resume flips Task to PENDING but JobItem stays CANCELLED → drift; drift reconciler lacks paused-on-terminal pattern (job_recovery_service.py:528-534).

## B2 Strand Chain (why the root freezes)

1. Children complete during pause → bus emits terminal → watchers transition FIRED.
2. Pause gate in claim_pending_task blocks PROCESS_REPORT delivery.
3. Resume calls _compact_fired_watchers_for_paused, which deletes FIRED rows (wake signals destroyed).
4. No new external message → no new dispatch → root frozen forever (msg count 25→25; recovery only via NEW external message).

## B3 Fire-vs-Cancel Seam Options

- Current: terminate cancels parent-side watcher (state→CANCELLED, no fired_at). Parent waits forever on ghost child.
- Option 1: fire with terminal outcome via emit_terminal with Outcome(status='terminated'); parent receives FollowUp and unblocks. Risk: assumes parent still waiting; parent terminated/revived → unexpected deliveries.
- Option 2: explicit UP propagation firing parent watchers with terminated outcome.
- Option 3: parent waiting loop detects CANCELLED watchers and treats as completed. Risk: conflates resume-cancelled vs terminate-cancelled lifetimes.

## Safeguards Inventory (fix must compose, not fight)

- (a) Pipeline fresh-DB _is_instance_paused() before _check_child_completion.
- (b) child_reports.py PAUSED idempotency guards ~:1244, ~:898, ~:1845.
- (c) job_feedback_observer _TERMINAL_INSTANCE_STATUSES includes PAUSED.
- Others: error_reporting.py, job_feedback_observer.py PAUSED checks.

## Prior-Art Constraints

- Pause-report-recovery plan: report delivery during pause with explicit pause gate.
- Report-Lane Decoupling invariant: PROCESS_REPORT bypasses cross-system guard; pause gate explicit; single finalize via _process_event; crash recovery via has_inflight_task.
- DependencyBus is SOLE completion authority; report-lane decoupling must hold.

## Open Questions

1. ReportDeliveryRecoveryService 5-lane sweep: exact lanes and timing (verify during implementation).
2. B2/B3 × revive interaction: firing watchers for a child that later revives → unexpected FollowUps?
3. How to detect "children completed during pause" before compaction?
4. Should compaction skip rows with enqueued_at IS NULL (unclaimed reports)?