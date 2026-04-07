# Architecture Decisions: Job Queue Improvement

## Decision 1: Completion Callback Approach (Callback vs Polling vs Event-Based)

**Decision**: Callback pattern — add a helper method to InstanceManager that calls JobQueueService directly.

**Alternatives Considered**:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Callback** (chosen) | Direct, simple, no new infrastructure | Couples manager to job queue service | ✅ Chosen — `_job_queue_service` already wired in |
| **Polling** | Decoupled, job processor polls instance status | High latency (2s poll interval), wasteful, complex state tracking | ❌ Rejected — defeats purpose of async completion |
| **Event-based** (EventBroadcaster) | Decoupled, reusable | Requires new event types + subscriber infrastructure, over-engineering for this use case | ❌ Rejected — would need to add job-specific subscriber to manager, more complex than callback |
| **Hybrid** (EventBroadcaster + JobProcessor listens) | Decoupled, uses existing infrastructure | JobProcessor would need to subscribe to instance events, creates cross-cutting dependency | ❌ Rejected — JobProcessor is a polling worker, not an event consumer |

**Rationale**: The `_job_queue_service` is already injected into InstanceManager (via `set_job_queue_service()`). Adding a direct callback method is the simplest, most maintainable approach. The coupling is already there (lock release on terminate), we're just extending it.

---

## Decision 2: Sync vs Async in `terminate_instance()`

**Decision**: Add a `complete_job_sync()` method to JobQueueService that wraps the async operations using the event loop.

**Context**: `terminate_instance()` is a synchronous method. The `release_locks_by_instance_sync()` already exists as a sync wrapper. We need the same pattern for job completion.

**Implementation**:
```python
def complete_job_sync(self, job_id: str, success: bool, error: str | None = None) -> Optional[JobItem]:
    """Sync wrapper for complete_job() — used by sync callers like terminate_instance()."""
    try:
        loop = asyncio.get_running_loop()
        return asyncio.run_coroutine_threadsafe(
            self.complete_job(job_id, success, error), loop
        ).result(timeout=5.0)
    except RuntimeError:
        # No running loop — should not happen in daemon context
        logger.warning(f"Cannot complete job {job_id} synchronously: no event loop")
        return None
```

**Alternative**: Make `terminate_instance()` async. **Rejected** — too many callers to update (API router, tools, registry).

---

## Decision 3: Result Summary Content

**Decision**: Use the last assistant message content (truncated to 500 chars) as the result summary for completed jobs.

**Alternatives**:
| Source | Quality | Complexity |
|--------|---------|------------|
| Last assistant message (chosen) | Good — captures what the agent actually did | Low — already available in `_process_queue()` result |
| LLM-generated summary | Best — human-readable summary | High — extra LLM call, latency, cost |
| Static "Job completed successfully" | Poor — unhelpful | Minimal |

**Rationale**: The last assistant message is already available in the `_process_queue()` flow (the `result.content` variable). Truncating to 500 chars prevents unbounded storage. An LLM summary can be added later as an enhancement.

---

## Decision 4: Schema Extension Strategy

**Decision**: Additive optional fields only. No breaking changes.

**Rules applied**:
- All new fields (`source`, `job_metadata`, `cancelled_at`) are `Optional[...]` with `None` default
- Existing fields unchanged
- No fields removed or renamed
- Frontend types updated to match (make `message` optional)

**Impact**: Zero breaking changes. Old API consumers continue working. New consumers get richer data.

---

## Decision 5: Frontend Test Framework

**Decision**: Jest (via `jest-preset-angular`)

**Alternatives**:
| Framework | Pros | Cons |
|-----------|------|------|
| **Jest** (chosen) | Fast, modern, no browser needed, Angular 17+ default | Setup required |
| Karma + Jasmine | Angular default historically | Slow, needs browser, deprecated in Angular 17+ |
| Vitest | Very fast, modern | Less Angular ecosystem support |

**Rationale**: Angular 17+ recommends Jest. It's faster and simpler. The project should adopt it regardless of this feature.

---

## Decision 6: Phase Execution Order

**Decision**: Phases 1 and 2 in parallel, then 3, 4, 5 can overlap.

**Critical path**: Phase 1 (completion callback) → Phase 3 (backend tests for callback)

**Parallel opportunities**:
- Phase 1 + Phase 2: Independent backend changes
- Phase 3 + Phase 5: Backend tests + frontend tests (different codebases)

**Recommended scheduling by Leader**:
```
Coder A: Phase 1 → Phase 3
Coder B: Phase 2 → Phase 4 → Phase 5
```

Or sequential:
```
Phase 1 + Phase 2 (parallel) → Phase 3 → Phase 4 → Phase 5
```
