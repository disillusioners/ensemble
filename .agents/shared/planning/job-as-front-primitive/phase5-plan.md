# Phase 5: Cutover + Cleanup

## Objective
Flip the feature flag default to ON, run the full E2E + regression suite, then delete the old raw-message fan-in code path. This is the final payoff — the raw-message entry path becomes internal-only, and the codebase is net-negative.

## Coupling
- **Depends on**: Phase 4 (facade partial collapse complete — JobItems + report Tasks)
- **Coupling type**: tight (deletes the fallback path that flag-OFF uses)
- **Shared files with other phases**: entry point files from Phase 3, config from Phase 1
- **Why this coupling**: Can only delete the raw-message path after the facade no longer depends on Task records for public work

## Context

### Pre-Cutover State
- Flag `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED` defaults to `False`
- Entry points have flag checks — flag OFF uses old `enqueue_message()`, flag ON uses `enqueue_message_job()`
- WorkResolver is partial collapse complete (JobItems + report Tasks, no turns — Phase 4 done)
- Both paths coexist in the codebase

### Post-Cutover State
- Flag defaults to `True`
- All entry points always use `enqueue_message_job()`
- Old `enqueue_message()` remains as **internal-only** (reports, nudges, `[JOB_EVENT]` delivery, compaction)
- Flag checks removed from entry points (dead code)
- D13 guard in `JobQueueService.enqueue(job_type="message")` can stay or be relaxed

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Flip flag default to True | Change `message_jobs_enabled: bool = Field(default=True)` in `JobSystemConfig`. Run locally and verify all entry points use message-Job path. | `daemon/config.py` |
| 2 | Run full E2E suite | Execute the 4 E2E tests in `ensure.md` on PostgreSQL. All must pass. | E2E test suite |
| 3 | Run regression suite | Full `pytest` suite on PostgreSQL. No new failures. | `pytest` |
| 4 | Run latency benchmark | Re-run Phase 0 benchmark. Verify p99 delta is within acceptable range (< 5ms). | `tests/benchmarks/bench_enqueue_latency.py` |
| 5 | Remove flag checks from entry points | Delete the `if flag: enqueue_message_job() else: enqueue_message()` branches in all 6 entry points. Always call `enqueue_message_job()`. | `daemon/sources/registry.py`, `daemon/sources/adapters/scheduler.py`, `daemon/routers/messages.py`, `daemon/tools/instance.py`, `daemon/tools/job_queue.py`, `daemon/manager.py:3356` (cascade-resume) |
| 6 | Remove flag from config | Delete `message_jobs_enabled` from `JobSystemConfig`. It's no longer needed — message-Jobs are the only public path. | `daemon/config.py` |
| 7 | Remove `_enqueue_message_with_flag()` helper | Delete the manager helper from Phase 3 task 1. | `daemon/manager.py` |
| 8 | Clean up D13 guard | The `JobQueueService.enqueue(job_type="message")` ValueError guard can be relaxed or removed. Message-Jobs now go through `enqueue_message_job()`, not `enqueue()`. Decision: keep the guard as defense-in-depth (TASK jobs only via `enqueue()`), or remove it if it adds confusion. | `daemon/services/job_queue_service.py:550-558` |
| 9 | Delete dead facade code | Verify no remaining references to deleted Phase 4 code. Clean up any dead imports. | `daemon/services/work_resolver.py` |
| 10 | Verify net-negative diff | `git diff --stat` should show more deletions than additions. Document the LOC delta. | git |

## Key Files
- `daemon/config.py:412-434` — flag flip + removal
- `daemon/sources/registry.py:822` — flag check removal
- `daemon/sources/adapters/scheduler.py:704-776` — flag check removal
- `daemon/routers/messages.py:127-145` — flag check removal
- `daemon/tools/instance.py:703` — flag check removal
- `daemon/tools/job_queue.py:742` — flag check removal
- `daemon/manager.py` — helper removal
- `daemon/services/job_queue_service.py:550-558` — D13 guard cleanup

## Constraints
- Do NOT delete `enqueue_message()` itself — it's still used by internal callers (reports, nudges, `[JOB_EVENT]`, compaction, system messages, `invoke_and_wait`)
- Do NOT delete the `Task` table or `TaskRepository` — Tasks remain as the internal execution substrate AND report Tasks are retained in the facade (AD-6)
- Do NOT delete the `MessageQueue` table — it's the message persistence layer
- The raw `enqueue_message()` path becomes **internal-only** — add a docstring marking it as such
- PostgreSQL is the primary test DB

## Exit Criteria (from Architecture Plan §7, updated for AD-6 partial collapse)

- [ ] Every public entry point creates a Job; no public path enqueues a raw message (all 6 entry points including cascade-resume)
- [ ] `WorkResolver` returns JobItems + report Tasks; turn-specific code (dedup, promotion) deleted (AD-6 partial collapse)
- [ ] Internal messages (reports/nudges/`[JOB_EVENT]`/compaction/`invoke_and_wait`) still use the raw path and are invisible to the facade
- [ ] **BLOCKING ISSUE 2**: Stuck `queued` JobItems finalized via finalize-on-completion fallback
- [ ] **BLOCKING ISSUE 3**: `list_pending_by_queue` filters `job_type="message"` — no poll-loop double-dispatch
- [ ] A parent mid-orchestration reports `processing` with no special-case code
- [ ] `job_continue` is the single continuation verb for new and existing instances
- [ ] Full E2E + regression suite green
- [ ] The diff is net-negative (more deleted than added)
- [ ] Latency E2E: message-Job dispatch startup ≤ raw-message + 5ms

## Deliverables
- [ ] Flag default ON, all E2E + regression tests pass
- [ ] Flag checks removed from all 6 entry points
- [ ] Flag removed from config
- [ ] Raw-message path marked internal-only
- [ ] Net-negative diff verified and documented
