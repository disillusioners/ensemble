# Job-as-Queue-Proxy Phase 1 Testing — Findings

**Date:** 2026-06-27
**Branch:** `feature/job-as-queue-proxy` (commit `04f36724` + test commit `260a90f9`)
**Sessions:** `jq-proxy-existing-suite`, `jq-proxy-functional-edge`

## Key Findings

### 1. Phase 1 Read Cutover is Sound
All job reads route through WorkResolverService/Instance/WorkRecord. No direct JobItem mirror-column reads remain. 18 new functional + edge case tests pass cleanly.

### 2. N+1 Query Elimination Confirmed
The `_batch_instances` helper fetches all instances in ONE query regardless of job count (1→5→10). Query count stays flat — the N+1 problem from the old per-job Instance lookup is gone.

### 3. Timing Column Fallback Semantics (Important Contract)
- `completed_at` for a **non-terminal** Instance intentionally falls back to `JobItem.completed_at` (the mirror column)
- This is a **transitional contract** — when Instance is terminal, `completed_at` comes from Instance timing
- When both Instance and mirror are missing, returns None
- **Pattern:** Test assertions must account for this transitional fallback, NOT assume None for non-terminal instances

### 4. dead_letter is JobItem-Only (Special Case)
The `dead_letter` status bypasses Instance lookup entirely — it's always sourced from JobItem.status directly. This is intentional since dead_letter is a queue-level state, not an execution state.

### 5. Status Mapping Verified (Canonical Map)
| Instance Status | Canonical Status |
|----------------|-----------------|
| idle/waiting/waiting_children/queued | processing |
| completed | completed/succeeded |
| error | failed |
| terminated | cancelled |

### 6. Write Paths Untouched (Regression Verified)
All 7 production code files changed are READ-only. No `UPDATE job_queue_items SET status=...`, no enqueue/cancel/complete/fail/delete/restore/retry writes modified. Finalization logic untouched.

### 7. Pre-existing PostgreSQL Failure (NOT Phase 1)
`tests/postgres/test_dependency_bus_pg.py::test_pg_restart_survival` fails with `assert 0 == 1`. Verified pre-existing by running against parent commit `077483f1` — same failure. DependencyBus restart-survival is an architectural question, not a Phase 1 issue. Recommend separate bug ticket.

## Testing Strategy
2 parallel sessions:
1. **Existing suite** (job_queue, work_router, work_resolver, cascade_pause_resume, job_queue_tools, postgres) — broad regression detection
2. **Functional + edge + regression** (new test file + git diff analysis) — targeted Phase 1 validation

Both completed within ~4 minutes total wall time.
