# Worker Pool Optimization Implementation

## Date: 2026-04-08

## What Was Done
Replaced polling-based worker wake-up (0.5s interval) with notification-based system using `threading.Condition`.

## Key Files Modified
- `daemon/services/worker_pool.py` — Core Condition, notify_work(), wait_for_work(), metrics
- `daemon/repositories/task/repository.py` — on_pending_task callback
- `daemon/manager.py` — Callback wiring + notify_work() after commit
- 4 test files updated for new Worker constructor signature

## Architecture Decisions
1. **threading.Condition + counter** (not Event) — handles burst enqueues correctly
2. **Callback injection** — TaskRepository takes `on_pending_task: Callable` to avoid circular imports
3. **3s safety-net timeout** — workers never permanently sleep even if notification is missed
4. **commit-before-notify** — session.commit() always called before notify_work()

## Gotchas Found During Implementation
1. **Metrics bug**: `empty_claim_attempts` wasn't being incremented — review caught this. Must increment when claim_task returns None.
2. **Worker constructor change**: Adding `worker_pool` parameter required updating ALL test files that create Worker instances.
3. **MockWorkerPool duplication**: Tests defined MockWorkerPool in 3 separate files — could centralize in conftest.py.
4. **Lambda with None guard**: Manager uses `lambda: self._worker_pool.notify_work() if self._worker_pool else None` because _worker_pool is None at TaskRepository creation time.

## Metrics Added
- `notifications_sent` — incremented in notify_work()
- `empty_claim_attempts` — incremented when claim_task returns None
- `workers_woken_by_timeout` — incremented in wait_for_work() on timeout
- `wakeup_efficiency` — calculated: notifications / max(1, notifications + empty_claims)

## Commit
`cff91b1` on branch `feature/worker-pool-optimization`
