# Virtual Job Management Surface — Approval Tracking

Plan: Virtual Job Management Surface (D14)
File: docs/plans/virtual-job-management-surface.md

---

## Iteration 001 — 2026-06-27

**Verdict: REJECTED**

### Blocking Issues

1. **§7 Defer queue is dangerously under-specified against the claim statement it must modify.**
   `claim_pending_task` (`task/repository.py:387`) is a ~90-line, 10-guard atomic raw-SQL UPDATE with nested subqueries (pause gate, cross-system job-coordination guard, per-instance serialization guard, retry-time gate). §7 describes adding `is_deferred` in 3 bullet points with NO SQL sketch, NO interaction analysis with the existing guards, and NO test plan entry (§6 tests 1-10 cover the facade; tests for defer-queue gating are entirely absent). The idle-gating port from `job_queue_service.py:1036-1080` is referenced but its adaptation to the task-table claim statement is not designed. This is a correctness risk: a mis-placed `is_deferred` guard in that SQL could silently starve deferred tasks or break the existing pause/job-coordination invariants.
   - Expected: SQL-level design for the `is_deferred` guard interaction with all 10 existing guards + ≥3 test cases for defer-queue claim behavior.
   - Found: 3 bullet points, zero SQL, zero tests.

2. **SSE wiring is mis-scoped as P4 frontend-only — it requires backend changes that are not in any phase.**
   The backend SSE endpoint (`jobs_streaming.py:30 stream_job_events`) polls `JobItem` exclusively via `service.get_job(job_id)` and reads `job.status`, `job.result_summary`, `job.queue_id`, `job.error_message` — all `JobItem` fields. The plan's P4 §3.10 says "reuse `job-sse.service` against `work_id` so task status flips stream live." But making that endpoint resolve a `work_id` to a task requires rewiring `stream_job_events` through the resolver (P2-level backend work), normalizing the polled status to canonical form, and handling the `running`→`processing` and `paused` mapping in the poll loop. This work appears in NO phase. As written, SSE on a task work_id would return 404 (resolver not wired into the streaming router).
   - Expected: SSE rewire explicitly in P2 (backend), or §3.10 §6-test-12 explicitly deferred.
   - Found: SSE work listed only in P4 as "frontend," backend dependency unacknowledged.

3. **Notification dedup mechanism is underspecified for the concurrency case the author partially acknowledges.**
   §3.6 relies on `notify_watchers` removing watch rows on terminal as the dedup mechanism. The existing terminal repositories (`complete_task`/`fail_task`/`cancel_task`) use `WHERE status = running` atomic guards and return `None` if already terminal — this is the correct primitive to prevent double-transition. BUT the plan does not specify that the new `notify_work_watchers` calls at the 7 terminal sites (`worker_pool.py:565/613/631`, `stale_task_recovery.py:251/307/396/456`) must be **conditional on the repository return value** (only notify if `complete_task`/`fail_task` returned a non-None row). Without this guard, if `stale_task_recovery.fail_task` races with `worker_pool.complete_task`, both could attempt to call `notify_watchers` — the watch-removal dedup saves the second call from double-notifying, but the notification ordering/content (completed vs failed) becomes nondeterministic. The plan says "the guard is: notify_watchers already removes watches" but does not specify the prerequisite: gate the call on the repo return value.
   - Expected: Explicit specification that `notify_work_watchers` is called only when the terminal repo method returns a non-None row (the transition was won).
   - Found: Dedup attributed solely to watch-row removal; the transition-guard prerequisite unspecified.

### Notes (non-blocking)

- The read-facade core design (resolve at read time, UUID `work_id`, never copy status) is structurally sound and correctly avoids the dual-record divergence class. This is the right architectural approach.
- The restart-rebuild risk (`get_watched_processing_job_ids` UNION rewrite) is correctly identified by the author and adequately mitigated by test #8. The status vocabulary mismatch (`PROCESSING` vs `RUNNING`) is real but the canonicalization layer (§2.2) addresses it — this is sound.
- Dropping the FK on `job_watchers.job_id` is safe — no `ON DELETE CASCADE` exists; `notify_watchers` does soft cleanup via `remove_all_watches_for_job`. No silent referential-integrity break.
- Feature flag default ON is the correct call for a facade (the kill switch regresses to today's behavior, not corruption).
