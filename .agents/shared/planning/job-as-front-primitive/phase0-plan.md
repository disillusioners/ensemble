# Phase 0: GO/NO-GO Prototype — Fast-Dispatch Streaming Validation

## Objective
Validate two GO/NO-GO gates:
1. **Latency gate**: creating a JobItem alongside the Task in the `enqueue_message` path adds **no measurable latency** vs the current Task-only path (target: <5ms p99 delta).
2. **RF1 guard-performance gate**: the cross-system guard in `claim_pending_task` (`repository.py:607-646`) does NOT regress under universal message-JobItem traffic — where **every** `process_message` Task claim hits the JobItem subquery, not just edge cases.

If either gate fails, the architecture proposal needs revisiting before committing to full implementation.

## Coupling
- **Depends on**: None
- **Coupling type**: independent (throwaway prototype branch)
- **Shared files with other phases**: none (prototype only)
- **Why this coupling**: Phase 0 validates the core assumption; all other phases depend on its outcome

## Context

### The Original Concern (§4.1)
The architecture plan §4.1 states: "A message-Job must NOT wait on `job_processor`'s poll loop. The HTTP handler creates the JobItem, dispatches its driving message **inline** (the existing enqueue path), and streams the resulting Task execution directly."

### What D13 Already Solved
Post-D13, `enqueue_message` already dispatches instantly via `worker_pool.notify_work()` — there is **no poll loop bottleneck**. The prototype question narrows to: **does adding an INSERT into `job_queue_items` in the same transaction as the MessageQueue + Task INSERT add measurable latency?**

### Current `enqueue_message` Flow (the baseline)
```
InstanceMessagingService.enqueue_message()
  ├─ _prepare_enqueued_message()  [single TX]
  │   ├─ INSERT MessageQueue
  │   └─ INSERT Task (status=PENDING, work_id=UUID4)
  ├─ live_hub.stream_status_change()  (if IDLE→RUNNING)
  └─ worker_pool.notify_work()
```

### Prototype Target Flow
```
InstanceMessagingService.enqueue_message_job()
  ├─ _prepare_enqueued_message_with_job()  [single TX]
  │   ├─ INSERT MessageQueue
  │   ├─ INSERT Task (status=PENDING, work_id=job_id)
  │   └─ INSERT JobItem (job_id=same UUID, admission_state=queued)
  ├─ live_hub.stream_status_change()
  ├─ stamp_message_id(job_id, message_id)  [best-effort]
  └─ worker_pool.notify_work()
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Write latency benchmark harness | Script that calls `enqueue_message` 100x (baseline) and `enqueue_message_job` 100x (prototype) against PostgreSQL, measuring p50/p95/p99 latency per call | `tests/benchmarks/bench_enqueue_latency.py` (new) |
| 2 | Prototype `enqueue_message_job()` | Add a new method to `InstanceMessagingService` that creates MessageQueue + Task + JobItem in a single transaction, stamps `work_id=job_id`, then calls `worker_pool.notify_work()` | `daemon/services/instance_messaging.py` |
| 3 | Single-transaction `_prepare_enqueued_message_with_job()` | Modify `_prepare_enqueued_message` or add a variant that also INSERTs a JobItem row in the same DB session | `daemon/services/instance_messaging.py` |
| 4 | Validate JobFeedbackObserver lifecycle | Send a message-Job, verify the observer receives the `instance_lifecycle` event and finalizes the JobItem correctly. Check that `_get_processing_job_for_instance()` finds the message-JobItem | `daemon/services/job_feedback_observer.py` (read-only validation) |
| 5 | Validate cross-system guard correctness | Verify that a message-JobItem doesn't block its own Task from being claimed by `claim_pending_task`. The `_admitted_task_carve_out_sql` NULL-safe guard should handle this if `message_id` is stamped | `daemon/repositories/task/repository.py` (read-only validation) |
| **6** | **RF1: Load-test cross-system guard under universal message-JobItem traffic** | **This is the critical RF1 validation.** Today the guard's JobItem subquery (`repository.py:607-646`) fires only for TASK-type JobItems (orchestration — rare). Post-cutover, **every** `process_message` claim will hit the subquery because every public message creates a JobItem. Benchmark `claim_pending_task` p50/p95/p99 latency under: (a) current load (0% message-JobItems), (b) 50% message-JobItems, (c) 100% message-JobItems. Measure against PostgreSQL with 10+ instances and 100+ pending tasks. Check `EXPLAIN ANALYZE` on the guard subquery — verify it uses the `idx_job_queue_admission_state` and `idx_job_queue_instance` indexes, not a seq scan. | `daemon/repositories/task/repository.py:607-646`, `tests/benchmarks/bench_claim_guard.py` (new) |
| 7 | Run benchmarks + produce GO/NO-GO report | Compare p99 latencies for all gates. Document findings. | `docs/plans/prototype-results.md` (new) |
| **8** | **RF3: Validate `_finalize_job` throughput at chat-message scale** | **RF3 resolved to 🟡 YELLOW (load concern only).** The D13 reversal concern is invalidated (JobItem is pure queue proxy). The remaining question is finalize throughput: when every public message creates a JobItem, the `JobFeedbackObserver._finalize_job()` fires for every turn completion. Benchmark finalize throughput at 5-10 messages/sec sustained. Verify no queue buildup in the observer's event loop. Check `_get_processing_job_for_instance()` query plan under load. | `daemon/services/job_feedback_observer.py`, `tests/benchmarks/bench_finalize_throughput.py` (new) |

## Key Files
- `daemon/services/instance_messaging.py` — `_prepare_enqueued_message()` (lines ~890-993), `enqueue_message()` (lines 994-1107)
- `daemon/repositories/job_queue/models.py` — `JobItem` model (lines 238-433)
- `daemon/repositories/job_queue/repository.py` — `create()`, `stamp_message_id()` (lines 1286-1324)
- `daemon/repositories/task/repository.py:367-676` — `claim_pending_task()` with all 3 guard layers
- **`daemon/repositories/task/repository.py:607-646` — cross-system guard subquery (RF1 target)**

## Constraints
- Prototype on a throwaway branch — do NOT merge
- Test against PostgreSQL (the primary dev/test DB)
- The prototype JobItem must use `job_type="message"` or a new type to distinguish from TASK jobs
- Do NOT change `enqueue_message` itself — add a new method

## Deliverables
- [ ] Benchmark script committed to prototype branch (enqueue latency)
- [ ] **RF1: Guard load-test benchmark committed** (`bench_claim_guard.py`)
- [ ] **RF3: Finalize throughput benchmark committed** (`bench_finalize_throughput.py`)
- [ ] `enqueue_message_job()` prototype working
- [ ] GO/NO-GO decision documented with latency numbers for all gates
- [ ] `EXPLAIN ANALYZE` output for guard subquery under load
- [ ] Confirmation that observer + cross-system guard handle message-JobItems
- [ ] **If guard modification needed**: scope documented for Phase 2
- [ ] **RF3**: Finalize throughput at 5-10 msg/sec confirmed (no queue buildup)

## Decision Matrix

### Gate 1: Enqueue Latency

| p99 Latency Delta (enqueue_message vs enqueue_message_job) | Decision |
|---|---|
| < 5ms | **GO** — proceed to Phase 1 |
| 5-10ms | **GO with optimization plan** — proceed but batch the JobItem INSERT or use a background fire-and-forget |
| > 10ms | **NO-GO** — revisit architecture: consider async JobItem creation, or collapsing JobItem to be a view over Task |

### Gate 2: RF1 Cross-System Guard Performance

| `claim_pending_task` p99 Latency Delta (0% vs 100% message-JobItem traffic) | Decision |
|---|---|
| < 2ms | **GO** — guard handles universal load transparently |
| 2-5ms | **GO with guard optimization** — proceed to Phase 2; scope explicit index/query optimization task |
| > 5ms | **GUARD MODIFICATION REQUIRED** — the guard subquery must be optimized or simplified before proceeding. Scope as explicit Phase 2 task (NOT frozen backend). Options: (a) add covering index on `job_queue_items(instance_id, admission_state, deleted_at)`, (b) simplify the `_admitted_task_carve_out_sql` to avoid the `EXISTS` subquery for message-JobItems, (c) skip the cross-system guard entirely for `job_type="message"` JobItems (they're inline-dispatched, not poll-loop-driven) |

### Gate 3: RF3 Finalize Throughput at Chat Scale

| `_finalize_job` sustained throughput | Decision |
|---|---|
| ≥ 10 msg/sec with no queue buildup | **GO** — observer handles chat-scale finalize load |
| 5-10 msg/sec | **GO with monitoring** — add observer queue-depth metric in Phase 2; alert if backlog grows |
| < 5 msg/sec | **OPTIMIZATION REQUIRED** — the observer's `_get_processing_job_for_instance()` query or `_finalize_job_db_sync()` write path is too slow for chat scale. Options: (a) batch finalize, (b) async finalize with background worker, (c) optimize the JobItem lookup query |
