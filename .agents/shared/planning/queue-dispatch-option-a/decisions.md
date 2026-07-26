# Decisions: Option A — Full D13 Reversal

## Architecture Decisions

### D1: Messages use the caller-selected `queue_id`
- **Decision**: Messages route through the `queue_id` selected by the caller (HTTP `message.queue_id`, scheduler config, tool param). If none provided, fall back to `system_parallel_queue` (preserving current message parallelism as default).
- **Rationale**: The queue selector UI already exposes `queue_id` selection for messages. Making it authoritative (rather than dead-letter) is the whole point of Option A. `system_parallel_queue` as default preserves existing behavior for callers that don't specify.
- **Alternative considered**: Force all messages to `system_fifo_queue` — rejected because it changes default parallelism behavior.

### D2: Preserve `instance_id` for messages in `start_job`
- **Decision**: Messages with a pre-set `instance_id` (targeting an existing instance) keep that `instance_id` through `start_job`. Only task-type jobs (or messages without a target) mint a fresh UUID.
- **Rationale**: All 5 callers of `enqueue_message_job` pre-resolve `instance_id` (HTTP path param, InstanceMapper, tool param). Discarding it would create duplicate instances — the #1 migration hazard.
- **Alternative considered**: Always mint fresh UUID — rejected (breaks continuation messages).

### D3: Load-existing-instance branch in `_spawn_instance_db_sync`
- **Decision**: SELECT the Instance row before INSERTing. Reuse if found (non-terminal). INSERT if not found.
- **Rationale**: `spawn_instance` currently always does a pure INSERT. Without a load-existing branch, even a preserved `instance_id` would cause an `IntegrityError` (duplicate primary key) on the second message to the same instance.
- **Race handling**: Use `ON CONFLICT DO NOTHING` (PG) or catch `IntegrityError` (SQLite) for concurrent-spawn races.

### D4: Producer creates ONLY the JobItem (no inline Task)
- **Decision**: `enqueue_message_job` creates a QUEUED JobItem via `enqueue()` and notifies `dispatch_bus`. It does NOT create the Task + MessageQueue rows inline. JobProcessor creates them after admission.
- **Rationale**: Creating the Task inline (current D13 behavior) AND having JobProcessor create it (standard path) would double-dispatch. The JobItem must be the sole authoritative dispatch primitive.
- **Consequence**: `message_id` is not available immediately in the producer. See D5.

### D5: `message_id` handling — pre-generation (option c)
- **Decision (recommended)**: Pre-generate `message_id` (UUID) in the producer and pass it through to JobProcessor's Task creation. This allows the producer to return `message_id` immediately.
- **Rationale**: Option (a) block-until-admission adds latency. Option (b) return-`job_id`-only breaks 5 callers expecting immediate `message_id`. Option (c) pre-generation preserves the current API contract with minimal change.
- **Implementation**: `message_id = str(uuid.uuid4())` in the producer → store on JobItem metadata → JobProcessor reads it when creating the Task → `stamp_message_id` links them.
- **Alternative considered**: (b) lazy `message_id` — deferred to execution time; would require API/frontend changes.

### D6: Internal `enqueue_message` stays Task-only
- **Decision**: The internal `enqueue_message` (used by JobProcessor, reports, nudges, system messages) remains Task-only (no JobItem). Only the PUBLIC `enqueue_message_job` routes through the queue.
- **Rationale**: JobProcessor calls `enqueue_message()` to create the Task for a claimed job. If it routed through the queue, it would re-dispatch its own jobs infinitely (recursion). Internal system traffic (reports, compaction) doesn't need queue concurrency control.
- **Scope boundary**: Public/external messages → authoritative JobItem. Internal messages → direct Task dispatch.

### D7: PG trigger exemption removed
- **Decision**: Remove `AND NEW.job_type != 'message'` from `trg_job_queue_items_active_lock_guard` so message jobs require a `job_locks` row for ACTIVE state.
- **Rationale**: If messages are authoritative jobs that acquire locks via `start_job_atomic_with_lock`, the trigger exemption is not just unnecessary — it's a consistency hole. Keeping it would allow active message jobs without locks, undermining concurrency enforcement.

### D8: All 5 phases merge as ONE atomic unit
- **Decision**: The migration is decomposed into 5 phases for implementation clarity, but ALL phases must merge as a single PR/release.
- **Rationale**: The system is broken in any mid-state. Partial deployment (e.g., Phase 3 producer live but Phase 4 filters not removed) leaves messages queued but never dispatched. Feature-flag the entire change if staged rollout is needed.

## Open Questions (deferred to execution)

### Q1: Hard-terminal instance handling (ERROR/CANCELLED)
- **Question**: When a message targets an instance in ERROR or CANCELLED state, should the load-existing branch reuse it or re-spawn?
- **Current behavior**: HTTP POST /messages handles this (reuses COMPLETED, may reject/respawn ERROR).
- **Recommendation**: Match current HTTP semantics. Document in Phase 2 execution.

### Q2: Frontend impact of `message_id` latency
- **Question**: If option (b) is chosen instead of (c), does the frontend chat UI need a loading state?
- **Status**: Mitigated by choosing option (c) — pre-generation avoids the issue entirely.

### Q3: Feature flag for rollback
- **Question**: Should the entire Option-A change be behind a config flag (`config.MESSAGE_STANDARD_PATH`)?
- **Recommendation**: Yes, for the initial rollout. The flag toggles between the old mirror path and the new standard path. Remove the flag after validation.
