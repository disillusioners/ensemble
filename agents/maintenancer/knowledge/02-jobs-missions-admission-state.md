# 02 — Jobs, Missions, AdmissionState

last-verified-against: v0.12.4

Jobs are the **single front primitive** for public work; internal agent↔agent messaging does NOT flow through jobs.

## Four-value AdmissionState
`daemon/repositories/job_queue/models.py:21-42`:

| Value | Old vocabulary | Meaning |
|-------|----------------|---------|
| `QUEUED` | PENDING | awaiting dequeue |
| `ACTIVE` | PROCESSING / PAUSED | dequeued, lock held |
| `DONE` | COMPLETED / FAILED / CANCELLED | terminal, no retry |
| `DEAD` | DEAD_LETTER | dead-lettered |

Phase 7b removed the legacy `JobStatus` enum; shim at `:107-126` keeps ~14 `tests/job_queue/` files (200+ refs) importing. **Prod MUST NOT import the shim** — use `AdmissionState` or inline strings.

## State machine
Transitions in `daemon/services/job_state_machine.py`: `VALID_TRANSITIONS` on `AdmissionState` + event (`RETRY`/`DEAD_LETTER`/`START`).
- `QUEUED → DONE` (cancel pending)
- `ACTIVE → DONE` (complete / fail / cancel / abort, NO_RETRY)
- `ACTIVE → QUEUED` (RETRY)
- `ACTIVE → DEAD` (DEAD_LETTER)
- `DONE → QUEUED` (replay)
- `DEAD → QUEUED` (replay from DLQ)
- `DONE → ACTIVE` (orphan-race post-commit re-arm)

## Lock-first concurrency
`daemon/services/job_queue_service.py:3629-3642`: lock INSERT + status UPDATE happen in a SINGLE transaction via `JobRepository.start_job_atomic_with_lock`. PG trigger `trg_job_locks_active_guard` fires at COMMIT, requires matching `job_queue_items.admission_state = 'active'` row visible with the `job_locks` row. Pre-fix ran two commits — trigger false-fired at lock commit, every job start aborted. On mismatch the transaction rolls back BOTH atomically.

## Post-commit re-arm
`DONE → ACTIVE` is the recovery transition for jobs whose commit landed but whose status write was swallowed (e.g. crash between commit and status UPDATE). Re-check `is_terminal` at CAS time, not read-time, to absorb the race (§04 trap (vii) — terminal↔INSERT TOCTOU).
