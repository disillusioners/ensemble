# 02 — Jobs, Missions, AdmissionState

last-verified-against: v0.12.4

Jobs are the **single front primitive** for public work. Internal agent-to-agent messaging does NOT flow through jobs.

## Four-value AdmissionState
`daemon/repositories/job_queue/models.py:21-42`:

| Value | Old vocabulary | Meaning |
|-------|----------------|---------|
| `QUEUED` | PENDING | in queue, awaiting dequeue |
| `ACTIVE` | PROCESSING / PAUSED | dequeued, lock held, instance spawned |
| `DONE` | COMPLETED / FAILED / CANCELLED | terminal, no retry pending |
| `DEAD` | DEAD_LETTER | dead-lettered |

Phase 7b removed the legacy `JobStatus` enum; shim at `:107-126` keeps ~14 `tests/job_queue/` files (200+ refs) importing. **Production code MUST NOT import the shim** — use `AdmissionState` or inline strings.

## State machine
`daemon/services/job_state_machine.py` declares `VALID_TRANSITIONS` keyed on `AdmissionState` + event label (`RETRY` / `DEAD_LETTER` / `START`). Terminal transitions:
- `QUEUED → DONE` (cancel pending)
- `ACTIVE → DONE` (complete / fail / cancel / abort, NO_RETRY)
- `ACTIVE → QUEUED` (RETRY)
- `ACTIVE → DEAD` (DEAD_LETTER)
- `DONE → QUEUED` (replay)
- `DEAD → QUEUED` (replay from DLQ)
- `DONE → ACTIVE` (orphan-race post-commit re-arm)

## Lock-first concurrency
`daemon/services/job_queue_service.py:3629-3642`: lock INSERT and status UPDATE happen in a SINGLE transaction via `JobRepository.start_job_atomic_with_lock`. PG trigger `trg_job_locks_active_guard` fires at COMMIT, requires matching `job_queue_items.admission_state = 'active'` row visible with the `job_locks` row. Pre-fix ran two commits — trigger false-fired at lock commit, every job start aborted. On status mismatch the transaction rolls back BOTH atomically.

## Post-commit re-arm
`DONE → ACTIVE` is the recovery transition for jobs whose commit landed but whose status write was swallowed (e.g. crash between commit and status UPDATE). `is_terminal` MUST be re-checked at CAS time, not read-time, to absorb this race (§04 trap (vii) — terminal↔INSERT TOCTOU).
