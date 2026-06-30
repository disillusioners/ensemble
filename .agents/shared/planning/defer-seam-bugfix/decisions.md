# Architecture Decisions: Defer Seam Bugfix

## Decision 1: Keep the Two-Table Model

**Status:** Accepted (carried forward from bug document)  
**Context:** The D13 unification created a dual work-tracking system: JobItems (queue-policy) and Tasks (execution). A single-table merge was evaluated.  
**Decision:** Keep the two tables. Harden the seam in place. All 19 bugs are closable by hardening, not merging.  
**Rationale:** The two-table split is a deliberate decoupling of queue-policy from execution. Merging would fold two orthogonal responsibilities into one object, creating a large hard-to-debug logic blob.  
**Consequences:** The seam requires explicit join-key management (`message_id`) and shared predicates (`has_active_non_deferred_work`).

## Decision 2: Post-Enqueue Stamp vs Atomic Stamp for `message_id`

**Status:** Recommended — Post-enqueue stamp  
**Context:** `message_id` is generated inside `_prepare_enqueued_message` AFTER the JobItem is admitted. Two approaches: (1) stamp after enqueue returns, (2) stamp atomically at admission.  
**Decision:** Post-enqueue stamp (Option 1). The real fix is the NULL-safe reader guard. The stamp is defense-in-depth.  
**Rationale:** The `message_id` doesn't exist until `_prepare_enqueued_message` generates it. Atomic stamping would require restructuring the admission flow. The NULL-safe guard prevents self-deadlock regardless of stamp success.  
**Consequences:** A crash between enqueue and stamp leaves NULL `message_id`, but the NULL-safe guard handles this. The carve-out won't fire (not a deadlock, just a missed optimization).

## Decision 3: Task Table as Source of Truth for "Active Work"

**Status:** Accepted  
**Context:** Post-D13, every runnable unit (job or virtual) is a Task. The defer idle-gate was counting JobItem rows only. The shared predicate must serve both project-scoped (defer idle-gates) and system-wide (`_is_idle`) consumers.  
**Decision:** The `task` table is the source of truth for "what work is in flight." The shared predicate `has_active_non_deferred_work(project_id: str | None = None)` queries Tasks, not JobItems. When `project_id` is None, the query is system-wide (omits the `WHERE i.project_id = :p` clause).  
**Rationale:** Virtual jobs write zero JobItem rows. The task table captures all work. Overloading with `project_id=None` avoids a separate method and keeps one code path.  
**Consequences:** `count_active_jobs_in_non_defer_queues` (JobItem-side) is superseded by the shared predicate. The JobItem-side count may still be used for queue-concurrency accounting, but NOT for idle detection.

## Decision 4: Lock Release Scoped per Job, Not per Instance

**Status:** Accepted  
**Context:** `release_by_instance` deletes ALL locks for an instance. In multi-queue setups, this releases locks on queues unrelated to the finalized job.  
**Decision:** Use `release_by_job(project_id, queue_id, job_id)` in `_finalize_terminal`. Fall back to `release_by_instance` only when `job_id` is None (virtual job).  
**Rationale:** `release_by_job` already exists and targets the exact lock. This prevents F4/F7 over-admission.  
**Consequences:** The `finally` block in `_finalize_terminal` must have access to `(project_id, queue_id)` — fetched from the JobItem before entering the finally.

## Decision 5: Conservative Reconciler (independent of `_is_idle` gate)

**Status:** Accepted  
**Context:** A periodic reconciler that force-cancels or force-completes tasks risks false positives. After Phase 1 fixes `_is_idle`, it returns False during active work — but the reconciler must run precisely during active work to catch drift. MaintenanceService._loop is gated on `_is_idle` and runs on a 15-min interval.  
**Decision:** The reconciler only acts on clear drift states with conservative thresholds. Log-only mode for ambiguous cases. Configurable interval. The reconciler is registered alongside StaleTaskRecovery's loop (or its own asyncio task with 60s sleep), NOT on MaintenanceService._loop. It completely bypasses the `_is_idle` gate.  
**Rationale:** False-positive force-cancels are worse than leaving a stuck job. The `_is_idle` gate is a maintenance-guard, not a reconciler-guard — they have opposite needs.  
**Consequences:** Some drift states may persist longer than ideal. The reconciler's first deployment should log aggressively before acting.

## Decision 6: F6 — Watcher Migration (not work_id reuse or prefix matching)

**Status:** Accepted (reworked from original Option c)  
**Context:** `notify_work_watchers` (`work_notifier.py:233`) does an exact `get_watchers_for_job(work_id)` match — no prefix matching. The original Option (c) (derived handle `f"{parent_work_id}#retry:{retry_count}"` matchable by prefix) would NOT work. Additionally, the parent Task is only `cancelled` (not deleted), so reusing `parent.work_id` would violate the UNIQUE constraint.  
**Decision:** Keep generating a fresh `work_id` for the retry Task. Inside `schedule_retry`'s transaction, migrate watcher rows: `UPDATE job_watchers SET job_id = :child_work_id WHERE job_id = :parent_work_id`. This keeps the exact-match contract unchanged.  
**Rationale:** The migration is atomic (same transaction as the retry Task INSERT). The exact-match contract is preserved. No API changes to `notify_work_watchers` or `get_watchers_for_job`.  
**Consequences:** Watcher rows are transiently re-pointed during retry. Stale watchers from failed retries are cleaned by the existing `reconcile_terminal_watches` mechanism at daemon restart.

## Decision 7: F3 — NULL `terminal_reason` Fallback + Backfill Migration

**Status:** Accepted  
**Context:** Databases upgraded before Phase 7c have NULL `terminal_reason` on existing `done`-state rows. The status filter must handle these gracefully.  
**Decision:** The filter uses `WHERE admission_state = 'done' AND (terminal_reason = :canonical_status OR (terminal_reason IS NULL AND :canonical_status = 'completed'))`. NULL rows default to `completed` semantics (per `_ADMISSION_TO_LEGACY_STATUS: done → completed`). A one-time backfill migration sets `terminal_reason` based on `error_message` presence (non-empty → `failed`, else → `completed`).  
**Rationale:** The fallback preserves backward compatibility without requiring all databases to be migrated first. The backfill eliminates NULLs over time.  
**Consequences:** A brief window where `status="completed"` may include pre-7c failed jobs (if their `error_message` was empty). The backfill migration closes this gap.

## Decision 8: F12 — Cancel BEFORE start_job Ordering

**Status:** Accepted  
**Context:** On retry, a stale PENDING Task for the same `instance_id` must be cancelled before the new instance/Task is spawned. If `start_job` runs first, both tasks can contest the same LangGraph checkpoint.  
**Decision:** The retry flow executes in strict order: (1) `atomic_retry` → JobItem `done → queued`, (2) `cancel_pending_tasks_for_instance` → stale PENDING cancelled, (3) `start_job` → fresh instance/Task.  
**Rationale:** The ordering prevents checkpoint contention. The cancel is idempotent (if no PENDING tasks exist, it's a no-op).  
**Consequences:** None — the ordering is always safe.

## Decision 9: F9 and F16 Deferred

**Status:** Deferred to follow-up  
**Context:** F9 (PostgreSQL-only post-commit re-arm trigger violation) is PG-specific and isolated to re-arm logic. F16 (lossy legacy API fallback) is a narrow fallback path.  
**Decision:** Both are addressed in a separate follow-up, not in these 3 PRs.  
**Rationale:** F9 requires deep investigation of the re-arm transaction semantics. F16 only affects legacy paths when the WorkResolver is unwired. Neither blocks the P1/P2 fixes.  
**Consequences:** F9 may still cause PostgreSQL trigger violations in specific re-arm scenarios. F16 may report failed jobs as `completed` in legacy paths.
