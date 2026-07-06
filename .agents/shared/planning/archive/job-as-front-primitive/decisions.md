# Architecture Decisions & Technical Risks

## Architecture Decisions

### AD-1: Message-Jobs Create JobItem Inline (Not Via Poll Loop)

**Decision**: Message-Jobs create a JobItem + Task + MessageQueue in a single transaction inside `enqueue_message_job()`, then dispatch immediately via `worker_pool.notify_work()`. The JobProcessor poll loop does NOT pick up message-Jobs.

**Rationale**: Post-D13, the dispatch path is already instant (event-driven via `DispatchEventBus` + `worker_pool.notify_work()`). Creating the JobItem inline means zero latency overhead vs the current Task-only path. The poll loop remains for TASK-type jobs (orchestration) and crash recovery only.

**Alternative Considered**: Route message-Jobs through `JobQueueService.enqueue()` → poll loop → `spawn_instance` → `enqueue_message`. **Rejected**: adds poll-loop latency (even with event-driven wake), and the instance already exists for most message-Jobs (no need to spawn).

---

### AD-2: JobItem `job_type="message"` (New Type)

**Decision**: Message-JobItems use `job_type="message"` to distinguish from `job_type="task"` orchestration jobs.

**Rationale**: The D13 guard currently rejects `job_type="message"` in `JobQueueService.enqueue()`. We relax this by routing message-Jobs through a new `enqueue_message_job()` method that creates the JobItem directly via `JobRepository.create_message_job()`, bypassing the `enqueue()` guard entirely.

**Implication**: The `JobProcessor._process_next_job()` must continue to skip `job_type="message"` jobs (they're already dispatched inline). Add a filter in the pending-jobs query to exclude `job_type="message"`.

---

### AD-3: Per-Instance Serialization — Correctness Handled, Performance Pending (RF1)

**Decision**: The existing 3-layer serialization handles message-Jobs **correctly** without SQL changes. However, the cross-system guard (`claim_pending_task:607-646`) becomes **load-bearing** under universal message-JobItem traffic (RF1). Guard **performance** is validated in Phase 0 Gate 2 and may require optimization in Phase 2.

**Rationale**:
- **Layer 1** (Task guard: 1 RUNNING task per instance) is the primary serialization gate — works for message-Jobs because each message-Job creates exactly one Task.
- **Layer 2** (Cross-system guard: checks for active JobItems) uses the NULL-safe carve-out: a JobItem only blocks if it has `message_id` AND no matching Task exists. Since message-Jobs stamp `message_id` and create the Task in the same TX, the carve-out passes → the JobItem doesn't block its own Task.
- Two message-Jobs on the same instance: Task A is claimed (RUNNING), Task B is PENDING. Layer 1 blocks Task B until Task A completes. No deadlock.

**RF1 caveat**: Today the guard's JobItem subquery fires only for TASK-type JobItems (rare). Post-cutover it fires on **every** `process_message` claim. If Phase 0 Gate 2 shows >2ms regression, Phase 2 includes an explicit guard modification task — NOT frozen backend.

---

### AD-4: Retry Policy — `max_retries=0` for Message-Jobs

**Decision**: Message-JobItems are created with `max_retries=0`. No retry, no dead-letter.

**Rationale**: Chat/continuation traffic is user-facing. A failed turn should be surfaced as an error, not silently retried. The user can re-submit. Orchestration jobs (TASK-type) keep their retry policy.

**Implementation**: `JobRepository.create_message_job()` sets `max_retries=0`. The retry scheduler (if any) must skip JobItems with `max_retries=0`.

---

### AD-5: Feature Flag — `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED`

**Decision**: Boolean flag in `JobSystemConfig`, default `False`, env var `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED`.

**Rationale**: Allows gradual rollout. Flag OFF = current behavior (raw messages). Flag ON = message-Jobs. The flag is checked at each entry point via a manager helper. After cutover (Phase 5), the flag is removed.

---

### AD-6: Partial Facade Collapse — Retain `kind="report"` Task Rows (RF2 Resolution)

**Decision**: The WorkResolver facade collapse eliminates `kind="turn"` Tasks (process_message) only. `kind="report"` Task rows (process_report, send_report) are **retained** in `list_work` / `resolve_work`. The Task union is NOT fully eliminated — it becomes JobItem ∪ report-Tasks (not JobItem-only).

**Rationale**: Deep code review (RF2) found 6 backend code paths that branch on `kind != "job"`. While none distinguishes turn from report (both fall into the same `kind != "job"` bucket), removing report records would:
- Degrade error precision on `job_retry`/`job_delete`/`job_restore` (precise "(report)" error → generic "Job not found")
- Make report work_ids uncancellable via DELETE/cancel endpoints (return 404 instead of cooperative cancel)
- Break the architectural contract that report Tasks are persisted work records designed to surface on the parent work board

Retaining report Tasks keeps all 6 backend paths working with **zero code changes**. The partial collapse still eliminates all turn-specific complexity (dedup, F10 drift, promotion, _kind_from_task_type for turns).

**Alternative Considered: Approach 2 (Create JobItem rows for reports)** — Rejected. Reports are child→parent completion payloads, not user-submitted work. Creating JobItems for them would inflate the `job_queue_items` table and the cross-system guard (RF1) with non-user-initiated records, and would require updating all 6 backend paths to handle `kind="report"` → `kind="job"` migration semantics.

**Alternative Considered: Approach 3 (Document as follow-up)** — Rejected. The 6 backend paths have specific error semantics and cooperative-cancel behavior that would silently degrade. Explicit treatment is required.

**What changes (partial collapse scope)**:
| Element | Status | Rationale |
|---------|--------|-----------|
| `kind="turn"` Task query | **DELETED** | Turns are now JobItems — turn-specific dedup, promotion, drift all unnecessary |
| `kind="report"` Task query | **RETAINED** | Reports have no JobItem equivalent; backend paths need them |
| Dedup loop (turn-shadowed-by-JobItem) | **DELETED** | Only applied to turns (`r.kind == "turn"`) |
| F10 status-drift warning | **DELETED** | Only fires on dropped turns |
| Active-orchestration promotion | **DELETED** | Only promotes turns; JobItem already sources from Instance |
| `_kind_from_task_type()` | **RETAINED** | Still needed for report discrimination |
| `REPORT_TASK_TYPES` constant | **RETAINED** | Still needed |
| `TURN_TASK_TYPES` constant | **DELETED** | No more turn-specific filtering |
| `task_repo` injection | **RETAINED** | Still needed for report query |

### Backend Code Path Impact Analysis (6 paths)

**Cross-cutting finding**: Of the 6 paths, **NONE distinguishes `kind="turn"` from `kind="report"`**. Both fall into the same `kind != "job"` bucket. With the partial collapse (AD-6), all paths continue to work unchanged because report records still exist.

| # | Path | File:Line | What it does for `kind != "job"` | Impact of AD-6 |
|---|------|-----------|----------------------------------|----------------|
| 1 | Cancel-by-work_id (DELETE) | `jobs_management.py:104` | Cooperative Task cancel via `cancel_task_by_work_id`; terminal → 200 | **No change** — report records still resolved by `get_work` |
| 2 | Cancel-by-work_id (POST) | `jobs_management.py:232` | Cooperative cancel; terminal → 400 error | **No change** |
| 3 | List-jobs defensive filter | `jobs_crud.py:520` | `if kind != "job": continue` — drops Task rows | **No change** — reports already excluded |
| 4 | `job_cancel` tool | `tools/job_queue.py:497` | Cooperative `request_cancel` (sets flag, worker stops at checkpoint) | **No change** |
| 5 | `job_retry` / `job_delete` / `job_restore` | `tools/job_queue.py:555,580,605` | Precise error: "task-type work ({kind}), no retry/delete/restore path" | **No change** — report records still resolved, precise error retained |
| 6 | P-C(i) dedup + F10 drift | `work_resolver.py:1149,1170` | Dedup index built from `kind="job"` only; dedup gate fires on `kind="turn"` only | **Simplified** — dedup gate never fires (turns gone); report records bypass dedup by design |

**Cooperative cancel vs instant cancel** (important semantic distinction):
- `kind != "job"` (Task): cooperative cancel — sets `cancel_requested=True` flag; worker thread observes it at next heartbeat and stops gracefully. Row stays RUNNING until then (prevents orphaned graph state).
- `kind == "job"` (JobItem): instant cancel — atomic `admission_state` flip to CANCELLED.

---

## Architecture Review Findings (RF1-RF3)

> The following findings were identified during architecture review and ruled on by the project owner.

### RF1: Cross-System Guard Becomes Load-Bearing (NOT blocking — must cover explicitly)

**Finding**: The cross-system guard in `claim_pending_task` (`repository.py:607-646`) currently fires only on edge cases (TASK-type JobItems — orchestration, rare). Post-cutover, **every** `process_message` Task claim hits the JobItem subquery because every public message creates a JobItem. The guard becomes a universal hot path.

**Owner Ruling**: NOT blocking, but the plan MUST explicitly cover it.

**Plan Coverage**:
- **Phase 0 Task 6 + Gate 2**: Load-test `claim_pending_task` under 0%/50%/100% message-JobItem traffic. `EXPLAIN ANALYZE` the guard subquery. Verify index usage.
- **Phase 2 Task 6 (conditional)**: If Gate 2 shows >2ms regression, scope explicit guard modification: (a) covering index, (b) simplify carve-out for `job_type="message"`, (c) bypass guard for message-JobItems.
- **Phase 2 Task 7**: High-contention serialization test (20 instances × 5 rapid message-Jobs).
- **Scope exception**: The cross-system guard is NOT treated as frozen backend. If modification is needed, it is an explicit task.

---

### RF2: Report Task Visibility — Backend Blast Radius (🔴 RED — must address)

**Finding**: The original plan treated this as "FE changes acceptable." Deep review revealed **6 backend code paths branch on `kind != "job"`**, not just the FE. Additionally, a **conceptual conflation** exists in the plan:
- **Internal messages** (transport): `internal_report:*` source prefix, ephemeral, NOT persisted as work
- **Report Tasks** (execution records): `process_report` / `send_report` Task rows, persisted, parent-bound, designed to surface in parent work board

The plan must NOT treat report Tasks as "internal messages." They are architecturally distinct.

**Owner Ruling**: 🔴 RED — must address deeply. Plan must choose an approach and enumerate all affected backend paths.

**Chosen Approach: AD-6 (Retain `kind="report"` Task rows — partial collapse)**

The facade collapse eliminates `kind="turn"` Tasks only. `kind="report"` Task query is retained. All 6 backend paths continue to work unchanged. See AD-6 below for full rationale and the path-by-path impact analysis.

---

### RF3: D13 Reversal / Dual-Record Coupling — RESOLVED (🟡 YELLOW — load concern only)

**Finding**: This plan reverses the D13 invariant "messages create Task-only (no JobItem)" by creating a JobItem alongside the Task for public messages.

**Resolution**: INVALIDATED by code evidence. JobItem is confirmed as a **pure queue proxy** with NO execution state — only AdmissionState (queued/active/done/dead). Instance is the sole execution authority. The plan adds **queue tickets per message**, not state coupling. This is architecturally equivalent to the existing validated `job_create` pattern, just at higher scale.

**Status**: 🟡 YELLOW — the coupling concern is resolved. The only remaining question is **load** (RF1 covers the guard; a new Phase 0 item covers finalize throughput at chat-message scale).

**Plan Coverage**:
- Phase 0 Task 8: Validate `_admitted_task_carve_out_sql` and `_finalize_job` throughput at chat-message scale (multiple/sec)
- RF1 covers the cross-system guard performance concern

---

## Technical Risks & Unknowns

### Risk 1: JobItem INSERT Latency (MEDIUM)

**Risk**: Adding an INSERT into `job_queue_items` in the same transaction as `MessageQueue` + `Task` may add latency.

**Mitigation**: Phase 0 prototype measures this. If >5ms, consider:
- Asynchronous JobItem creation (fire-and-forget after Task creation)
- Pre-allocated JobItem IDs (avoid UUID generation overhead — negligible)

**Assessment**: LOW — a single INSERT on an indexed table adds <1ms on PostgreSQL. The Task INSERT already happens; one more INSERT in the same TX is marginal.

---

### Risk 2: JobFeedbackObserver Finalization + Stuck `queued` JobItems (🔴 RED → mitigated by two-part fix)

**Risk**: The observer must finalize message-JobItems. Additionally (BLOCKING ISSUE 2), if the `queued→active` activation UPDATE fails, the JobItem stays `queued` and `_get_processing_job_for_instance()` never finds it → permanent leak.

**Analysis**: The observer's finalize path has **two** gates that filter on `admission_state == active`:
1. `_get_processing_job_for_instance()` (line ~615-741) — the lookup query
2. `_finalize_job_db_sync()` Step 1 UPDATE (line 2941) — the terminal atomic write

If only the lookup (gate 1) is fixed, the UPDATE (gate 2) still filters on `active` only → `rowcount == 0` → SELECT finds the row in `queued` state → raises `InvalidTransitionError` → caught at line ~1633 → **silent DEBUG return** → Steps 2+3 (instance status + lock release) never execute → instance is NOT finalized → JobItem stays `queued` forever. This is the exact permanent leak the fallback claims to prevent.

**Mitigation (BLOCKING ISSUE 2 — two-part finalize-on-completion fallback)**:
- **Part A**: Change `_get_processing_job_for_instance()` to match `queued` AND `active` JobItems
- **Part B**: Change `_finalize_job_db_sync()` Step 1 UPDATE WHERE clause (line 2941) from `== ACTIVE` to `.in_([ACTIVE, QUEUED])`

Both parts are Phase 1 Task 6b. See Phase 1 design section for full rationale and the failure-trace analysis showing why Part A alone is insufficient.

---

### Risk 3: Cross-System Guard Race Window (LOW — distinct from RF1 performance concern)

**Risk**: The `_admitted_task_carve_out_sql` in `claim_pending_task` might behave unexpectedly with message-JobItems that have `message_id` stamped but the Task hasn't been claimed yet (race window between INSERT and stamp). **This is a correctness concern, distinct from RF1 which is a performance concern.**

**Analysis**: The stamp happens in the same `enqueue_message_job()` flow, before `notify_work()`. By the time a worker tries to claim, the stamp is done. Even if not, the NULL-safe guard tolerates missing `message_id` (falls back to legacy sibling check). **Low risk for correctness.**

---

### Risk 4: Scheduler Path Complexity (MEDIUM)

**Risk**: The scheduler's `_route_via_job_queue()` currently creates a TASK-type JobItem via `JobQueueService.enqueue()` and lets the poll loop handle dispatch. Converting it to `enqueue_message_job()` changes the dispatch model from poll-loop-driven to inline.

**Mitigation**: The scheduler must call `get_or_create_instance()` before `enqueue_message_job()` since message-Jobs assume the instance exists. The current `_route_via_job_queue` path goes through the poll loop which spawns the instance — this changes.

**Action**: Phase 3 task 6 handles this explicitly. The scheduler's `_execute_immediate()` path already uses the instance-creation flow from the registry, so it's a natural fit.

---

### Risk 5: Report Task Visibility and Backend Path Degradation (🔴 RED → mitigated by AD-6)

**Risk**: Originally treated as FE-only. Deep review found 6 backend code paths branch on `kind != "job"`. Full collapse would degrade error precision and cancel semantics for report Tasks.

**Mitigation**: AD-6 (partial collapse) retains `kind="report"` Task rows. All 6 backend paths work unchanged. Only turn-specific complexity is deleted. See AD-6 path-by-path impact table.

---

### Unknown 1: `job_continue` + Message-Job Interaction

**Question**: When `job_continue` creates a message-Job on an existing terminal instance, does the JobFeedbackObserver correctly handle a NEW active JobItem when the instance was previously terminal?

**Action**: Test this in Phase 3 (entry point #5 conversion).

---

### Unknown 2: Multiple Active JobItems Per Instance

**Question**: The plan §4.5 says "A conversation yields N JobItems (one per submitted turn)." Can an instance have multiple `admission_state=active` JobItems simultaneously? The F13 fix handles "two ACTIVE JobItems exist" — does it work for the message-Job case?

**Action**: Test in Phase 2 (serialization test). The observer's `_get_processing_job_for_instance()` with the F13 fix should handle this, but verify.

---

### Unknown 3: `JobProcessor` Pending Query Filter — RESOLVED (BLOCKING ISSUE 3)

**Question**: Does `JobProcessor._process_next_job()` query for pending jobs filter out `job_type="message"`?

**Answer**: NO. `list_pending_by_queue` (`repository.py:703-723`) queries `WHERE queue_id = ? AND admission_state = 'queued' AND deleted_at IS NULL` with **no `job_type` filter**. Without this fix, the poll loop picks up message-JobItems and double-dispatches them.

**Resolution**: Phase 1 Task 0 (hard prerequisite). Add `.where(JobItem.job_type != "message")` to `list_pending_by_queue`. This is a one-line fix that MUST land before any message-JobItem is created. Also verified: the poll loop calls `list_pending_by_queue` → `_process_next_job` → `start_job` → `spawn_instance_with_mcp` → `enqueue_message(work_id=job.job_id)`, which would create a DUPLICATE Task for the message-Job if not filtered.
