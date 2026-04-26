# Plan Approval Tracking: RAG Knowledge Toolset

## Plan Identity
- **Name**: RAG Knowledge Toolset
- **File**: `.agents/shared/planning/rag-knowledge-toolset/plan-overview.md`
- **Phases**: 6 (Core Infrastructure → RAG Client → Tools → Explorer → Experiencer → Migration)

---

## Iteration 001 — 2026-04-26

### Verdict: REJECTED

### Blocking Issues

1. **Worker Thread Deadlock with explore()** — CRITICAL
   - Expected: explore() blocks calling agent synchronously while Explorer instance runs, returns result
   - Found: The system uses a worker thread pool (default 4) where each worker blocks on `MainLoopBridge.run_async()` → `future.result()`. When `explore()` does `await registry.wait_for()`, it releases the asyncio event loop BUT the worker thread remains blocked. The spawned Explorer needs a free worker thread to be processed. If all N workers are blocked on explore() calls, no worker is available to process the Explorer instances. This is a **performance deadlock** (recovers after 40-minute graph timeout, not an infinite hang).
   - The plan does not address this at all. No mention of worker pool sizing, no concurrency semaphore, no alternative execution model.
   - **Fix required**: Either redesign explore() to run Explorer's graph directly on the event loop (bypassing worker pool), OR implement async task claiming, OR add a dedicated worker pool for nested instances, OR change explore() to non-blocking.

2. **CompletionRegistry Race: complete() Before register()** — HIGH
   - Expected: Registry always has a registered event before complete() is called
   - Found: In `invoke_agent_and_wait()`, the sequence is: spawn_instance() → register() → enqueue_message(). But if the spawned instance processes extremely fast (unlikely but possible in error cases), complete() could fire before register() is called. The plan's `complete()` returns False when nobody is registered, which means the result is silently lost. The `wait_for()` would then timeout.
   - **Fix required**: Implement a buffered-completion mechanism where complete() stores the result even if nobody is registered yet, and register() checks for pre-existing completions.

### Notes (Non-blocking)

- Phase 2 (RAG Client) is clean and well-scoped — no issues
- Phase 3 tool registration follows existing patterns correctly (verified against codebase)
- Phase 4/5 agent definitions are standard markdown — no code concerns
- Phase 6 inner_soul redirect logic is well-designed with thorough edge case coverage
- The plan's investigation of hook points in child_reports.py (lines 507, 557, 597) and error_reporting.py (line 166) are accurate per codebase verification
- Singleton pattern for CompletionRegistry is acceptable but InstanceManager attribute would be cleaner
- 15 RAG tools in Phase 3 is a lot but they're thin wrappers — acceptable scope


---

## Iteration 002 — 2026-04-25

### Verdict: APPROVED

### Previously Rejected Issues — Resolution Check

1. **Worker Thread Deadlock (CRITICAL)** — ✅ RESOLVED
   - Semaphore approach (`asyncio.Semaphore(WORKER_POOL_SIZE - 1)`) verified correct against codebase
   - Worker threads DO block on `future.result()` — semaphore correctly limits concurrent invoke_agent_and_wait calls
   - Codebase confirms: sequential worker loop, atomic task claiming, 1:1 worker-to-task mapping
   - Ensures ≥1 worker free for spawned agent tasks

2. **CompletionRegistry Race: complete() before register() (HIGH)** — ✅ RESOLVED
   - Buffered completion mechanism correctly handles the timing window
   - Codebase verification confirms: workers claim TASKS not instances; tasks only created in enqueue_message() AFTER register()
   - Race is structurally impossible under current architecture, but buffered mechanism is sound defense-in-depth
   - `complete()` buffers result, `register()` consumes buffer and immediately sets event

### Notes (Non-blocking)

- `cleanup_stale()` has a dead list comprehension: `buffered_to_clean` is created but never used. Buffered entries are only cleared when >100 (safety valve). Minor code quality issue — should clean buffered entries by age, not just by count threshold.
- `'instance_id' in dir()` pattern in `invoke_agent_and_wait()` works correctly in Python function scope for both "assigned" and "not yet assigned" cases.
- Phase 6 inner_soul redirect logic verified against actual `_classify_request()` — all classification type mappings are correct, no types map to only memory/memories that shouldn't redirect.
- Phase 2 (RAG Client) remains clean — standard async HTTP client module.
- Phase 4/5 agent definitions are self-contained markdown — no code concerns.
