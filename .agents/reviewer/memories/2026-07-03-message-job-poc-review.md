# Message-Job POC Review (commit 7d42f6b5)

## Deep-Review triggered
Data Integrity, Concurrency, Business-Critical, Architecture changes.

## Key Findings
- 🔴 C1: `_get_processing_job_for_instance` matching `queued` can finalize never-processed messages
- 🔴 C2: Fire-and-forget activation race (`run_async_no_wait`) — observer can finalize before activation commits
- 🟡 Non-atomic three-write (Task + JobItem + stamp) — degrades gracefully but creates noise
- 🟡 JobRepository.create redundant default_factory (job_id param shadows model default)
- ✅ Feature flag OFF = zero behavior change (verified)
- ✅ AsyncMessageResult single definition in messaging_types.py (verified)
- ✅ list_pending_by_queue excludes message jobs correctly
- ✅ VALID_TRANSITIONS includes (queued, done)

## Lessons
- Informational mirrors that follow an authoritative primitive MUST complete their state transitions BEFORE the primitive becomes visible to other consumers.
- OR the observer/consumer must JOIN against the authoritative primitive's status.
- Matching `queued` (a pre-processing state) in finalize logic is inherently dangerous.

## Verification (commit f50b9989) — APPROVED
- C1 fixed: `_get_processing_job_for_instance` now gates `queued` matches on `Task.status == RUNNING` via `_get_task_row_by_work_id` helper on BOTH lookup paths. Correct because `claim_pending_task` atomically sets Task→RUNNING before the worker reaches the activation call.
- C2 fixed: activation changed from `run_async_no_wait` (fire-and-forget) to `run_async` (blocking, 5s timeout). No deadlock risk — worker is a separate thread, activation is a single UPDATE (ms-scale).
- W2 cleanup: `job_id=None` passed to JobItem constructor is safe — SQLAlchemy re-evaluates `default_factory` at flush time on both SQLite and PostgreSQL. Verified directly against PG.
- Tests: 9/9 POC tests pass. 1323 job/queue/message tests pass. 1 pre-existing flaky concurrency test, 1 pre-existing mock test — both confirmed identical at original POC commit.
- Remaining: stale comment at worker_pool.py:285-287 ("worker does not await the result") contradicts the C2 blocking fix.
