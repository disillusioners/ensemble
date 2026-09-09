# 02 — Jobs, Missions, AdmissionState

last-verified-against: v0.12.4

Jobs are the **single front primitive** for public work. Internal
agent-to-agent messaging does NOT flow through jobs.

## Four-value AdmissionState

`daemon/repositories/job_queue/models.py:21-42` defines:

| Value | Old vocabulary | Meaning |
|-------|----------------|---------|
| `QUEUED` | PENDING | in queue, awaiting dequeue |
| `ACTIVE` | PROCESSING / PAUSED | dequeued, lock held, instance spawned |
| `DONE` | COMPLETED / FAILED / CANCELLED | terminal, no retry pending |
| `DEAD` | DEAD_LETTER | dead-lettered |

Phase 7b removed the legacy `JobStatus` enum from production semantics;
the shim at `daemon/repositories/job_queue/models.py:107-126` is a
real `str, Enum` so ~14 `tests/job_queue/` files (200+ references) keep
importing `JobStatus.X.value` / `JobStatus.is_valid(...)` / `for s in
JobStatus`. **New production code MUST NOT import the shim** — use
`AdmissionState` for queue-admission concerns, or the inline string
literals (`"pending"`, `"processing"`, ...) for the legacy API surface.

## State machine

`daemon/services/job_state_machine.py` declares `VALID_TRANSITIONS`
keyed on `AdmissionState` plus an event label (`RETRY` / `DEAD_LETTER` /
`START` / etc.). Terminal transitions:
- `QUEUED → DONE` (cancel pending)
- `ACTIVE → DONE` (complete / fail / cancel / abort, NO_RETRY)
- `ACTIVE → QUEUED` (RETRY)
- `ACTIVE → DEAD` (DEAD_LETTER)
- `DONE → QUEUED` (replay)
- `DEAD → QUEUED` (replay from DLQ)
- `DONE → ACTIVE` (orphan-race post-commit re-arm)

## Receipts vs stateful proxies

`message/mirror` job types are **receipts** — read-model projections,
not stateful proxies of instance state. The job row reflects what
happened; the source of truth for instance state is the `Instance` row
(joined on `instance_id`).

## Lock-first concurrency

`daemon/services/job_queue_service.py:3629-3642`: the lock INSERT and
the status UPDATE happen in a SINGLE transaction via
`JobRepository.start_job_atomic_with_lock`. The PostgreSQL constraint
trigger `trg_job_locks_active_guard` fires at COMMIT and requires the
matching `job_queue_items.admission_state = 'active'` row to be visible
together with the `job_locks` row. Pre-fix flow ran two separate
commits — the trigger false-fired at the lock commit and every job
start in production aborted. On status mismatch the transaction rolls
back BOTH the lock INSERT and the failed UPDATE atomically, so no
try/finally is needed to release the lock on failure.

## Post-commit re-arm

`DONE → ACTIVE` (orphan-race post-commit re-arm) is the recovery
transition for jobs whose commit landed but whose status write was
swallowed (e.g. crash between commit and status UPDATE). The
`is_terminal` predicate MUST be re-checked at CAS time, not at
read-time, to absorb this race (see §04 — terminal↔INSERT TOCTOU).

## Cross-refs

- §01 architecture (engine + repos bound to ~16)
- §04 traps (terminal↔INSERT re-spawn; idle_predicate SQL)
- §05 repair runbooks (DLQ replay, orphan ACTIVE sweep)
