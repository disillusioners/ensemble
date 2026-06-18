# Review: Revived Instance Stale Job Lookup (b1218739)

## Verdict: NEEDS REVISION

## Key Findings

### Blocking (3)
1. **`_process_event` asymmetry** — same stale-job bug, different path (job_feedback_observer.py:378). Has no defense-in-depth re-query that handle_correlation_complete got. Extract shared `_get_processing_job_for_instance` helper.
2. **`created_at` tie-breaking** — ORDER BY created_at DESC is non-deterministic on same-microsecond inserts. Add `job_id` as secondary sort key.
3. **Non-atomic two-write in terminate** — `update_status` + `update(waiting_for=0)` not in try/except; crash leaves inconsistent state. Wrap or collapse into single update.

### Warnings (4)
1. Defense-in-depth comment is misleading — `created_at` is immutable after insert, so the documented "CANCELLED job has newer created_at" scenario is unreachable in production (belt-and-suspenders only).
2. `clear_for_instance` create-then-pop lock churn pattern (benign, mirrors resolve_response).
3. send_message guard incomplete — misses COMPLETED, FAILED terminal states.
4. terminate path (instance_lifecycle.py:563) ordering change is safer but behavior should be documented.

## Constraints Preserved
- Dual-driver (SQLite+PG): PASS (ISO 8601 ASCII sorts same on both; not PG-tested in CI)
- N3 (CM on event loop): PASS
- N4 (no CM re-entry in callback): PASS
- C1 TOCTOU: PASS
- asyncio.to_thread: PASS

## Key Insight
`JobItem.created_at` is set ONCE at row insert and NEVER updated by transitions (cancel/complete/fail only touch their own timestamp fields). Therefore in terminate→revive, the revive PROCESSING job ALWAYS has newer created_at than the stale CANCELLED job. Fix 1's ORDER BY alone resolves the documented scenario; the defense-in-depth re-query is unreachable in production.
