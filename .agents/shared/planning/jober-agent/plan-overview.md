# Plan Overview: Jober Agent — Job Orchestrator

## Objective
Add a new "jober" agent that acts as a **job orchestrator** — it creates jobs, observes their lifecycle events in real-time via a new subscription mechanism (no polling), makes decisions based on outcomes, and reports results. This requires both new infrastructure (job event watching) and the agent definition itself.

## Scope Assessment
**LARGE** — Spans multiple modules: new event subscription infrastructure (model + repository + tools + shared notification service + observer hook), new agent definition with multiple markdown files, modifications to the tool factory and bootstrap wiring. Estimated 1-2 days of work.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Key Gap**: Agents currently CANNOT subscribe to job events. The only option is polling via `job_get()`. This is the core infrastructure gap this plan addresses.
- **Existing agents for reference**: `agents/leader/` (closest pattern — coordinator with no execution tools)

## Architecture Decision: How Job Watching Works

### Approach: Shared Notification Service + ALL Terminal Path Hooks

**Why this approach (revised from v1):**
- Jobs reach terminal states through **7 code paths** — not just the observer
- We extract a **shared `notify_watchers()` function** in `JobQueueService` and call it from EVERY path
- Uses existing `instance_manager.enqueue_message()` to deliver notifications with `internal_agent:` prefix for correct message classification

**All 7 terminal paths:**
```
Path 1: JobFeedbackObserver._process_event()      → COMPLETED / FAILED
Path 2: JobQueueService.cancel_job()                → CANCELLED
Path 3: JobQueueService.complete_job()              → COMPLETED / FAILED / TERMINATED
Path 4: InstanceLifecycle.terminate_instance()      → TERMINATED (via complete_job_sync)
Path 5: DeadLetterService.move_to_dlq_standalone()  → DEAD_LETTER
Path 6: JobRetryEngine.maybe_retry()                → DEAD_LETTER (via move_to_dlq)
Path 7: JobRecoveryService._fail_orphaned_job()     → FAILED (daemon startup)
```

**Flow:**
```
Agent calls watch_job(job_id) → stored in job_watchers table
  ↓
Job reaches terminal state via ANY of the 7 paths
  ↓
notify_watchers(job_id, status, error) called from that path
  ↓
For each watcher: enqueue_message() delivers notification to watching instance
  ↓
Watching agent receives notification as a regular message (MessageType.AGENT)
  ↓
Agent decides: retry, escalate, report to parent, create next job
```

**Subscription storage**: New `JobWatcherRepository` backed by SQLite — simple table mapping `(instance_id, job_id)` pairs. Survives daemon restarts.

**Crash recovery**: On daemon start, scan watches for terminal jobs and deliver immediate notifications (reconciliation pass). Note: Path 7 (orphan recovery) also runs at startup — the watcher repo must be initialized **before** recovery runs (see bootstrap ordering in Phase 1 Task 7).

### Rejected Alternatives
| Alternative | Why Rejected |
|---|---|
| Polling via `job_get()` | Defeats purpose — jober would need to spin-loop |
| New background service | Unnecessary — can hook into existing code paths |
| SSE-based watch | SSE is for external HTTP clients, not internal agents |
| EventBus global subscription per watcher | Too noisy — each agent would get ALL events, not just their jobs |
| Hook only into `_process_event()` | **Misses 6 of 7 terminal paths** — cancel, terminate, complete, dead_letter, retry exhaustion, and orphan recovery would silently lose notifications |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Job Watch Infrastructure | Build subscription backend, tools, shared notification service, hooks in ALL 7 terminal paths, cleanup, crash recovery | None | — | 6-8h |
| 2 | Jober Agent Definition | Create complete agent with all markdown files | Phase 1 (tool names) | loose | 2-3h |
| 3 | Integration & Testing | End-to-end wiring, edge cases, prompt refinement | Phase 1 + 2 | tight | 2-3h |

### Coupling Assessment

| Phases | Coupling | Justification |
|--------|----------|---------------|
| 1 → 2 | **loose** | Phase 2 only needs tool names (`watch_job`, `unwatch_job`, etc.) — not implementation. |
| 1 → 3 | **tight** | Phase 3 tests the actual implementation from Phase 1, verifies integration with all 7 terminal paths. |
| 2 → 3 | **loose** | Phase 3 verifies the agent works but doesn't modify agent definition files (only refines prompts). |

**Scheduling**: Phase 1 and 2 can partially overlap. Phase 2 can start once tool names are known from Phase 1 (after Task 2). Phase 3 must wait for both.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Race condition: job completes before watch is registered | Medium | Watch registered BEFORE `enqueue()` in `job_create`. For standalone `watch_job()`, check if job is already terminal and immediately notify. |
| Notification missed on any terminal path | **High** | Shared `notify_watchers()` called from ALL 7 terminal paths. |
| Message classified as HUMAN triggers unwanted context injection | **High** | Use `source=f"internal_agent:job_event:{job_id}:{status}"` for correct `MessageType.AGENT` classification. |
| Memory/storage leak from abandoned watches | Low | Auto-cleanup when watching instance terminates + TTL cleanup for stale watches |
| Notification message unclear to receiving agent | Medium | Structured JSON block in notification — reliable for LLM parsing |
| JobFeedbackObserver becomes bottleneck | Low | Notification is async fire-and-forget; watcher lookup is indexed DB query |
| Too many watches for one instance | Medium | Max 50 watches per instance (configurable) |
| Breaking existing job processing | High | All changes are additive. If `watcher_repo` is None, everything works as before. |
| Daemon crash leaves stale watches | Medium | Startup reconciliation — deliver notifications for terminal jobs, clean up watches. |
| `move_to_dlq()` runs inside shared transaction | Medium | Cannot call async `notify_watchers()` inside sync session. Notify AFTER commit at each call site. |
| Bootstrap ordering: recovery runs before observer starts | Medium | `watcher_repo` must be created and wired into `JobRecoveryService` BEFORE `recover_on_startup()` runs. Path 7 notifications queue as messages for later delivery when instance resumes. |

## Success Criteria
- [ ] `watch_job` tool allows any agent to subscribe to a specific job's lifecycle events
- [ ] `unwatch_job` tool removes a subscription
- [ ] `list_watched_jobs` tool shows all active watches for the calling instance
- [ ] `job_create` can auto-watch when called with `watch=True` parameter — watch registered BEFORE job dispatch
- [ ] When a watched job reaches a terminal state via **any** of the 7 paths, a structured notification is enqueued to the watching instance
- [ ] Notification messages use `internal_agent:` source prefix for correct `MessageType.AGENT` classification
- [ ] Notifications include structured JSON block for reliable LLM parsing
- [ ] `watch_job()` checks for already-terminal jobs (including DEAD_LETTER) and sends immediate notification
- [ ] Default `watch_events` includes all terminal states: `completed`, `failed`, `cancelled`, `terminated`, `dead_letter`
- [ ] Orphan-recovered jobs (Path 7) deliver notifications during startup — messages queue for later instance delivery
- [ ] Startup reconciliation delivers notifications for terminal jobs after daemon crash
- [ ] Watches are automatically cleaned up when the watching instance terminates
- [ ] The jober agent is discoverable by `AgentRegistry.discover()` and spawns correctly
- [ ] Jober's tool filter restricts it to: `job`, `instance`, `self`, `help`, `time`, `project` (NO bash, NO filesystem)
- [ ] Jober can orchestrate a multi-job workflow end-to-end: create → watch → receive → decide → report
- [ ] Zero regressions in existing job processing flow

## Tracking
- Created: 2026-04-24
- Last Updated: 2026-04-25
- Status: draft
- **Revision**: v4 — Added Path 7 (`JobRecoveryService._fail_orphaned_job()`), now 7 total terminal paths
