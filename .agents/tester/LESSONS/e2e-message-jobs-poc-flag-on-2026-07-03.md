# Job-as-Front-Primitive POC E2E Findings (2026-07-03)

## Key Findings

### 1. POC Works End-to-End — 3 of 4 Criteria Pass
- ✅ `work_id == job_id` linkage holds (exact UUID match)
- ✅ No double-dispatch (exactly 1 Task per message)
- ✅ Instance responds normally (completes with assistant message)
- ⚠️ JobItem admission_state stays at `queued` — never transitions to active/done

### 2. Cross-System Guard Carve-Out Blocks Second Message (FIXED)
**Root cause**: The carve-out in `_admitted_task_carve_out_sql` only matched Tasks with `status IN ('pending', 'running')`. After Phase 1's Task completed, its JobItem mirror was stuck in `queued` (eager activation no-ops due to PG trigger constraint). The stuck JobItem then permanently blocked Phase 2's new Task from being claimed.

**Fix**: Broadened the carve-out to match ANY Task with the same `message_id`, regardless of status. Commit `386a22be`.

**Lesson**: When a mirror record's lifecycle is decoupled from its source (JobItem from Task), guard conditions must account for the mirror persisting in intermediate states after the source has moved on.

### 3. VJM Kind Mapping Changes Under Flag ON
With flag ON, VJM dedup keys on `(instance_id, message_id)` and prefers JobItem when both Task and JobItem exist. So message-driven work surfaces as `kind="job"` instead of `kind="turn"`. Test assertions must accept both kinds.

**Lesson**: E2E test assertions that check VJM kind must be flag-aware. Use `assert "turn" in kinds or "job" in kinds` instead of strict `assert "turn" in kinds`.

### 4. Terminate Cleanup Cancels Message JobItems (FIXED)
`terminate_instance` cleanup loop called `find_jobs_by_instance(job_type=None)` which returned ALL jobs including MESSAGE type. MESSAGE JobItems are informational mirrors, not lifecycle-managed jobs, so cancelling them caused false `cancelled` status.

**Fix**: Added `if remaining_job.job_type == "message": continue` to skip message mirrors in cleanup. Commit `78dc9e3c`.

### 5. Pause/Resume Cancels JobItem Mirror (ARCHITECTURE ISSUE)
The resume cascade intentionally cancels the Task (paused→cancelled) to prevent WorkerPool re-claim races. But the parallel terminal-write path treats this cancelled Task as terminal and writes `terminal_reason='cancelled'` to the JobItem mirror. This is the RF3 dual-record coupling concern from the architecture review.

**Classification**: Architecture issue, not quick-fixable. Needs designed solution where resume cascade manages JobItem mirror state.

**Lesson**: When two records (Task + JobItem) have independent state machines but are semantically linked, any lifecycle event that writes to one must coordinate with the other. Otherwise the mirror diverges from reality.

### 6. Eager Activation No-Ops Due to PG Trigger
The eager `queued→active` transition in `enqueue_message_job` fails silently because PostgreSQL trigger `trg_job_queue_items_active_lock_guard` requires a `job_locks` row, which the message flow never creates. The `IntegrityError` is caught and logged at DEBUG.

**Lesson**: Message-type JobItems are mirrors, not queue participants. They don't have lock rows. Any code that tries to activate them will hit the trigger guard. Either relax the trigger for message-type jobs or accept that mirrors stay in `queued`.

### 7. Environment Setup Gotchas (Reusable)
Same as prior E2E sessions:
- SSL_CERT_FILE/SSL_CERT_DIR must be unset (`env -u`)
- `data_dev/ensemble.json` must have `"database": "postgres"`
- RAG_IS_REQUIRED must be `false`
- Clean `__pycache__` before starting daemon
- Use `/api/health` not `/health` (the latter hits frontend catchall)

## Commits Applied
1. `78dc9e3c` — test: fix E2E assertions + skip message jobs in terminate cleanup
2. `827649e7` — fix: eager activate message JobItem (no-op, kept for contract alignment)
3. `386a22be` — fix: broaden cross-system guard carve-out (KEY FIX for Phase 2)
