# Phase 3: Integration & Testing (REVISED v4)

## Changes in This Revision (v4)
**Blocker fix:**
- Added Path 7 — `JobRecoveryService._fail_orphaned_job()` → FAILED (daemon startup orphan recovery)
- Added edge case **2m** (orphan recovery notification during startup)
- Updated Task 1 to verify all 7 paths
- Added `job_recovery_service.py` to Key Files Summary
- Updated regression checklist to verify recovery flow + bootstrap ordering
- Updated edge case count: 13 (was 12)
- Updated deliverables count: all 7 paths

---

## Objective
Verify the complete end-to-end flow works across **ALL 7 terminal paths**: jober creates a job → watches it → receives a notification (whether job completes via observer, cancel, direct complete, terminate, dead_letter standalone, retry exhaustion, or orphan recovery at startup) → makes a decision → reports to parent. Handle edge cases, verify no regressions, and refine jober's prompts if needed.

## Coupling
- **Depends on**: Phase 1 (infrastructure) + Phase 2 (agent definition)
- **Coupling type**: tight — tests the actual integration of all components
- **Shared files with other phases**: 
  - `daemon/services/job_queue_service.py` — may need bug fixes from edge cases
  - `daemon/services/job_feedback_observer.py` — may need tweaks from testing
  - `daemon/services/dead_letter_service.py` — may need tweaks from testing
  - `daemon/services/job_retry_engine.py` — may need tweaks from testing
  - `daemon/tools/job_queue.py` — may need bug fixes from edge cases
  - `agents/jober/` — may need prompt refinements

## Context
- Phase 1 provides: `JobWatcher` model (JSON events, includes `dead_letter`), `JobWatcherRepository`, shared `notify_watchers()` in JobQueueService, 4 watch tools, atomic `watch=True`, hooks in ALL 7 terminal paths, startup reconciliation, auto-cleanup
- Phase 2 provides: Complete jober agent definition in `agents/jober/`
- This phase verifies they work together and handles production concerns
- **Important**: Path 7 (orphan recovery) runs at daemon startup BEFORE observer starts. Notifications queue as DB messages for later delivery when watching instance resumes.

## Tasks

### Task 1: Verify End-to-End Flow (ALL 7 Terminal Paths)
**Details**: Walk through each terminal path to ensure notifications are delivered.

**Verification checklist**:
1. `AgentRegistry.discover()` finds the jober agent
2. Spawning a jober instance creates it with correct tools
3. Jober's system prompt is composed correctly from soul → rule → skill → tools_note → workflow
4. **Path 1 (Observer)**: `job_create(watch=True)` → job runs → agent completes → `_process_event()` → `notify_watchers()` → notification received
5. **Path 2 (Cancel)**: Watch job → `job_cancel()` → `notify_watchers("cancelled")` → notification received
6. **Path 3 (Complete)**: `complete_job()` → `notify_watchers()` → notification received
7. **Path 4 (Terminate)**: Instance terminated → `complete_job_sync(TERMINATED)` → `notify_watchers()` → notification received
8. **Path 5 (Dead Letter Standalone)**: `move_to_dlq_standalone()` → after commit → `notify_watchers("dead_letter")` → notification received
9. **Path 6 (Retry Exhaustion → DLQ)**: `maybe_retry()` → retries exhausted → `move_to_dlq()` → after commit → `notify_watchers("dead_letter")` → notification received
10. **Path 7 (Orphan Recovery)**: Daemon restart with orphaned PROCESSING job → `_fail_orphaned_job()` → `notify_watchers("failed")` → notification queued in DB → delivered when watching instance resumes
11. Notification message has `internal_agent:job_event:` source prefix → classified as `MessageType.AGENT`
12. Notification contains structured JSON block with `job_id`, `status`, `agent_id`, `result`, `error`, `timestamp`
13. Jober can call `send_message()` to report results to parent
14. After notification, the watch is auto-cleaned from `job_watchers`

**Key Files to verify**:
- `daemon/registry.py` — `discover()` picks up jober
- `daemon/tools/instance.py` — `create_instance_tools()` creates correct tools for jober
- `daemon/loader.py` — `compose_system_prompt()` assembles jober's files correctly
- `daemon/services/job_queue_service.py` — `notify_watchers()` works for all paths
- `daemon/services/dead_letter_service.py` — notification fires after commit
- `daemon/services/job_retry_engine.py` — notification fires after commit on DLQ move
- `daemon/services/job_recovery_service.py` — notification fires after `atomic_transition()` in `_fail_orphaned_job()`
- `daemon/api.py` — bootstrap ordering: `watcher_repo` ready before `recover_on_startup()`

---

### Task 2: Handle Edge Cases

#### 2a: Race condition — atomic watch on job_create
- **Scenario**: Job completes immediately after creation
- **Solution**: `watch=True` registers watch BEFORE `enqueue()` — job is PENDING, observer only processes PROCESSING
- **Verify**: Create a fast-completing job with `watch=True`, confirm notification arrives

#### 2b: Standalone watch_job on already-terminal job
- **Scenario**: Agent calls `watch_job()` on a job that already completed/failed
- **Solution**: `watch_job()` checks terminal status, sends immediate notification
- **Verify**: Complete a job, then call `watch_job()`, confirm immediate response

#### 2c: Cancel notification
- **Scenario**: `job_cancel()` is called on a watched job
- **Solution**: `cancel_job()` calls `notify_watchers(job_id, "cancelled")` after transition
- **Verify**: Watch a job, cancel it, confirm notification with status "cancelled"

#### 2d: Terminate notification
- **Scenario**: Instance terminated while job is processing
- **Solution**: `terminate_instance()` → `complete_job_sync(TERMINATED)` → `notify_watchers()`
- **Verify**: Watch a processing job, terminate its instance, confirm notification

#### 2e: Watching instance terminates
- **Scenario**: Jober crashes or is terminated while watches are active
- **Solution**: Auto-cleanup in `terminate_instance()`. On restart, startup reconciliation.
- **Verify**: Spawn jober, create watches, terminate jober, check table is clean

#### 2f: Multiple instances watching the same job
- **Scenario**: Two jober instances both watch the same job
- **Solution**: Both receive notifications. One job can have multiple watchers.
- **Verify**: Two instances watch same job, both get notified

#### 2g: Watch on non-existent job
- **Scenario**: `watch_job()` called with invalid job_id
- **Solution**: Return error "Job not found"
- **Verify**: Call `watch_job("nonexistent-id")`, confirm error message

#### 2h: Duplicate watch
- **Scenario**: Same instance calls `watch_job()` twice for same job
- **Solution**: No-op / return "Already watching". Composite unique constraint.
- **Verify**: Double-watch same job, confirm idempotent behavior

#### 2i: Max watches exceeded
- **Scenario**: Instance tries to watch more than 50 jobs
- **Solution**: Return error "Watch limit (50) reached."
- **Verify**: Create 50 watches, attempt 51st, confirm error

#### 2j: Startup reconciliation (crash recovery)
- **Scenario**: Daemon crashes with watches for already-terminal jobs
- **Solution**: `reconcile_terminal_watches()` finds terminal jobs, sends notifications
- **Verify**: Create watches, kill daemon, restart, confirm notifications delivered

#### 2k: Dead letter via standalone DLQ move (NEW — Blocker 2)
- **Scenario**: `move_to_dlq_standalone()` is called on a watched job (e.g., manual DLQ move)
- **Solution**: After `session.commit()`, `notify_watchers(job_id, "dead_letter", error)` is scheduled
- **Verify**: Watch a failed job, call `move_to_dlq_standalone()`, confirm dead_letter notification arrives

#### 2l: Dead letter via retry exhaustion (NEW — Blocker 2)
- **Scenario**: A watched job fails repeatedly, exhausts retries, `maybe_retry()` calls `move_to_dlq()`
- **Solution**: After `session.commit()` in `maybe_retry()`, `notify_watchers(job_id, "dead_letter", error)` is scheduled
- **Verify**: Watch a job that will exhaust retries, let it fail max_retries times, confirm dead_letter notification arrives

#### 2m: Orphan recovery during startup (NEW — v4 Blocker)
- **Scenario**: Daemon crashes. On restart, a watched job was left in PROCESSING state. `JobRecoveryService._fail_orphaned_job()` detects the instance is dead and marks the job FAILED.
- **Solution**: `_fail_orphaned_job()` calls `await self._job_queue_service.notify_watchers(job.job_id, "failed", error_message)` after successful `atomic_transition()`
- **Special considerations**:
  - Runs during daemon startup BEFORE `JobFeedbackObserver` starts
  - The watching instance may not be running yet — `enqueue_message()` persists to DB, message delivered when instance resumes
  - `watcher_repo` must be initialized BEFORE `recover_on_startup()` runs (bootstrap ordering verified in Task 7)
- **Verify**: 
  1. Create a watched job in PROCESSING state
  2. Kill the daemon (simulating crash)
  3. Restart daemon
  4. Verify `_fail_orphaned_job()` runs and notification is queued in DB
  5. Spawn the watching instance — verify notification is delivered

---

### Task 3: Verify Notification Message Format
**Details**: Ensure the notification format includes structured JSON and correct source prefix.

**Expected format**:
```
[JOB_EVENT] Job abc12345... reached status 'completed'.
Agent: coder
Result: Successfully implemented feature X
Error: None

```json
{"job_id": "abc12345-...", "status": "completed", "agent_id": "coder", "result": "Successfully implemented feature X", "error": null, "timestamp": "2026-04-25T12:00:00.000000"}
```
```

**Source field**: `internal_agent:job_event:abc12345-def6-7890-ghij-klmnopqrstuv:completed`

**Verification**:
- [ ] Source field starts with `internal_agent:` → classified as `MessageType.AGENT`
- [ ] JSON block is present and valid JSON at end of message
- [ ] JSON contains: `job_id`, `status`, `agent_id`, `result`, `error`, `timestamp`
- [ ] `status` is one of: completed, failed, cancelled, terminated, dead_letter
- [ ] `error` is null for success, contains message for failures
- [ ] Watch is removed from `job_watchers` after notification delivery
- [ ] Notification for dead_letter includes `error_message` from original failure

**Key Files**:
- `daemon/services/job_queue_service.py` — `notify_watchers()` message construction
- `daemon/services/instance_messaging.py` — verify `internal_agent:` → AGENT classification

---

### Task 4: Regression Testing
**Details**: Verify that adding watches doesn't break existing job processing.

**Regression checklist**:
- [ ] Jobs created without `watch=True` work exactly as before
- [ ] `JobFeedbackObserver` performance is not noticeably impacted
- [ ] Watch cleanup doesn't interfere with normal job completion flow
- [ ] Existing job tools still work
- [ ] `job_create` without `watch` param still works
- [ ] Job retry flow still works (including notification after retry)
- [ ] Dead letter flow still works (`move_to_dlq`, `move_to_dlq_standalone`, `replay_from_dlq`)
- [ ] Orphan recovery flow still works (`_fail_orphaned_job()` recovers orphaned PROCESSING jobs)
- [ ] Queue concurrency limits still enforced
- [ ] `notify_watchers()` failure does NOT break job processing (try/except wrapper)
- [ ] `move_to_dlq()` shared-session version works correctly (no notification code inside transaction)
- [ ] Bootstrap ordering: `watcher_repo` + `job_queue_service` wired into `JobRecoveryService` before `recover_on_startup()` runs

---

### Task 5: Verify Tool Category Registration
**Details**: Ensure the new watch tools are properly registered in the `job` category.

**Verification**: All 4 tools have `@register_tool_category("job")` and appear when agent has `"job"` in tool filter `allow`.

---

### Task 6: Prompt Refinement (if needed)
**Details**: After initial testing, refine jober's prompts based on observed behavior.

**Watch for**:
- Jober doesn't handle `dead_letter` notifications → strengthen skill.md decision framework
- Jober doesn't use `watch=True` on `job_create` → strengthen rule.md
- Jober tries to do work directly → strengthen rule.md "NEVER EXECUTE" section
- Jober doesn't parse JSON block correctly → improve skill.md

**Key Files to refine**:
- `agents/jober/rule.md`, `agents/jober/skill.md`, `agents/jober/workflow.md`

---

### Task 7: Verify Startup Reconciliation (including dead_letter)
**Details**: Test crash recovery including dead_letter scenarios.

**Test procedure**:
1. Create watched jobs in various states
2. Let some reach terminal states (completed, failed, dead_letter)
3. Kill the daemon process (simulating crash)
4. Restart daemon
5. Verify `reconcile_terminal_watches()` runs and finds ALL terminal jobs including dead_letter
6. Verify notifications delivered for each

---

## Key Files Summary

| File | Action | Purpose |
|------|--------|---------|
| `daemon/registry.py` | **VERIFY** | Jober is discoverable via `discover()` |
| `daemon/tools/instance.py` | **VERIFY** | Correct tool assembly |
| `daemon/loader.py` | **VERIFY** | System prompt composition |
| `daemon/services/job_queue_service.py` | **VERIFY/TWEAK** | `notify_watchers()` + paths 2, 3 |
| `daemon/services/job_feedback_observer.py` | **VERIFY/TWEAK** | Path 1 calls shared notifier |
| `daemon/services/dead_letter_service.py` | **VERIFY/TWEAK** | Path 5 notification after commit |
| `daemon/services/job_retry_engine.py` | **VERIFY/TWEAK** | Path 6 notification after commit |
| `daemon/services/job_recovery_service.py` | **VERIFY/TWEAK** | Path 7 notification after atomic_transition |
| `daemon/api.py` | **VERIFY** | Bootstrap ordering: watcher_repo before recovery |
| `daemon/services/instance_messaging.py` | **VERIFY** | `internal_agent:` → AGENT classification |
| `daemon/tools/job_queue.py` | **VERIFY/TWEAK** | Edge case handling |
| `agents/jober/*` | **VERIFY/REFINE** | Prompt refinement |

## Constraints
- Zero regressions in existing job processing
- All 13 edge cases handled gracefully
- Notification format must include structured JSON block
- Source prefix must be `internal_agent:`
- `notify_watchers()` failure must not break job processing
- `move_to_dlq()` shared-session version must NOT contain notification code
- Bootstrap ordering: `watcher_repo` ready before `recover_on_startup()`

## Deliverables
- [ ] End-to-end flow verified across ALL 7 terminal paths
- [ ] All 13 edge cases handled and verified (2a through 2m)
- [ ] Structured JSON notification format confirmed
- [ ] Source prefix `internal_agent:` correctly classified as `MessageType.AGENT`
- [ ] Dead letter notifications verified (both standalone and retry exhaustion)
- [ ] Orphan recovery notification verified during daemon startup (Path 7)
- [ ] Startup reconciliation verified (crash recovery including dead_letter)
- [ ] Bootstrap ordering verified: watcher_repo ready before recovery
- [ ] No regressions in existing job processing
- [ ] New tools properly registered in `job` category
- [ ] Jober agent prompts refined based on testing results
