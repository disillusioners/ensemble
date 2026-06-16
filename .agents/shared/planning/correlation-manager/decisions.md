# Architectural Decisions Log — CorrelationManager Migration

## Decision Format
Each decision follows: **Context → Decision → Rationale → Alternatives → Consequences**

---

## ADR-001: In-Memory State for CorrelationManager (No DB Table)

**Date**: 2026-06-16
**Status**: Accepted (Revised — Fix C1)
**Revision**: 2026-06-16 — Corrected semantic: CM tracks message-response pairs, not child existence

### Context
CorrelationManager needs to track which messages a parent has sent to children and is awaiting responses for. The system already has `waiting_for` as a durable counter in the `instances` table that tracks pending message responses (NOT child existence — `spawn_instance` does NOT increment `waiting_for`, only `send_message` does). Adding a separate DB table for correlation would introduce a second source of truth.

### Decision
CorrelationManager state is purely in-memory. It tracks `(parent_id, child_id, message_id)` triples — one entry per `send_message` that expects a response. On restart, it rebuilds from the `instances` table by querying `waiting_for > 0` to find waiting parents, then cross-references the `message_queue` table for real `message_id` UUIDs of in-flight messages to children. Uses those real UUIDs as correlation keys so `resolve_response()` can match them (Fix N1 — placeholder keys would never match real UUIDs).

### Rationale
- Durable state already exists (`waiting_for` in `instances` table, messages in `message_queue`)
- `waiting_for` tracks pending message RESPONSES, NOT child existence (critical semantic distinction)
- `spawn_instance` does NOT increment `waiting_for` — only `send_message` does (at `tools/instance.py:571`)
- Volume profile is low (max 100 instances, 50 children/parent, ~1 msg/sec) — rebuild is trivial cost
- Mirrors the proven `_graph_tasks` pattern: volatile cache, DB is source of truth
- Avoids sync complexity between DB table and in-memory event-driven state

### Alternatives Considered
1. **DB-backed correlation table**: Rejected — second source of truth, sync complexity, unnecessary durability for derivable state
2. **Redis/external state store**: Rejected — adds external dependency, no multi-node requirement, overkill for ~1 msg/sec volume
3. **Derive from events log**: Rejected — replaying events on restart is complex and slow vs. a simple SQL query

### Consequences
- Crash recovery requires rebuild (best-effort for correlation keys; `waiting_for > 0` identifies which parents need tracking)
- No queryable correlation state in DB (but `instances.waiting_for` + `message_queue` provides equivalent queryability)
- CorrelationManager must handle rebuild correctly or lose tracking

---

## ADR-002: EventBus for Inbound Events Only (Revised — Fix C2/C3)

**Date**: 2026-06-16
**Status**: Accepted (Revised — outbound events use direct callback per ADR-008)

### Context
CorrelationManager needs to receive lifecycle events (child spawned, child completed, child errored). The system has an existing `EventBus` with pub-sub support via `subscribe_all()`.

### Decision
CorrelationManager subscribes to the existing `EventBus` using `subscribe_all("correlation_manager")` for **inbound** lifecycle events only. Outbound `correlation.complete` events use direct callback (per ADR-008).

### Rationale
- EventBus already publishes `instance_lifecycle` events with all needed data (`instance_id`, `status`, `parent_id`)
- `subscribe_all()` supports N subscribers with per-subscriber queues
- Inbound subscription is safe even if events are dropped (CM rebuilds from DB)
- Outbound events cannot use EventBus due to C2 (always persists to DB) and C3 (silent drop on queue full)

### Alternatives Considered
1. **New dedicated event channel**: Rejected — unnecessary complexity, EventBus handles inbound fine
2. **Direct method calls (no pub-sub)**: Rejected — tight coupling, can't add more consumers later
3. **JobWatcherRepository pattern**: Rejected — that's DB-backed pub-sub for durable notifications; CM needs ephemeral in-memory events

### Consequences
- EventBus becomes more critical (2 subscribers instead of 1)
- Event ordering matters — EventBus is single-threaded async, events processed FIFO per subscriber
- If EventBus is down, CorrelationManager can't track (but it can rebuild from DB)
- Outbound correlation events bypass EventBus entirely — consumers must register as callbacks

---

## ADR-003: Shadow Mode Before Cutover

**Date**: 2026-06-16
**Status**: Accepted

### Context
CorrelationManager will eventually replace the `waiting_for` counter for all decisions. Directly switching risks introducing new bugs in production.

### Decision
Phase 1 runs CorrelationManager in **shadow mode** — it tracks state and compares with `waiting_for`, logging mismatches, but doesn't affect runtime behavior. Phases 2-3 progressively switch consumers to use CorrelationManager.

### Rationale
- Validates correctness before cutting over
- Can run for days/weeks gathering confidence data
- Zero risk to production during validation period
- Pattern proven by feature-flag / canary deployment practices

### Alternatives Considered
1. **Direct cutover (big bang)**: Rejected — too risky for a system managing parent-child lifecycle
2. **Feature flag with instant toggle**: Partially adopted — shadow mode IS the flag; "toggle" = switching consumers in Phases 2-3
3. **A/B testing (route some traffic to CM)**: Rejected — can't split parent-child correlation by traffic (a parent and its children must use the same decision path)

### Consequences
- Extra development time for shadow mode logic
- Mismatch logs need monitoring
- Must have clear "exit criteria" for shadow mode (zero mismatches for 24h)

---

## ADR-004: CorrelationManager Owns Status Transition + Event Publication

**Date**: 2026-06-16
**Status**: Accepted

### Context
Currently, 3 cascade sites each independently: (1) check conditions, (2) set parent status, (3) publish lifecycle event. The publication timing and conditions differ across sites.

### Decision
`CorrelationManager.check_parent_completion()` is the single method that sets parent status AND publishes the lifecycle event. Callers no longer do either.

### Rationale
- Eliminates divergent event publication timing (Site 1A publishes from caller; Site 2 publishes inline)
- Single code path for status transition → consistent ordering
- CorrelationManager can ensure `correlation.complete` and `instance_lifecycle` events are published in correct order
- Simplifies callers — they just get a `ParentCompletionDecision` result

### Alternatives Considered
1. **Callers set status, CM publishes event**: Rejected — still 3 places setting status, divergence risk remains
2. **CM sets status, callers publish event**: Rejected — event publication timing varies by caller (async vs sync context)
3. **Separate status setter and event publisher classes**: Rejected — over-engineering for a single decision

### Consequences
- CorrelationManager becomes a critical path component (failure = no status transition)
- Must handle its own DB writes for status (or delegate to instance repository)
- Callers must trust CM's decision and not double-check

---

## ADR-005: Remove `WAITING_CHILDREN` Status (Replace with CM In-Memory State)

**Date**: 2026-06-16
**Status**: Accepted (Revised — Fix C2/C3)

### Context
`WAITING_CHILDREN` is a transient status indicating "parent is processing but children's message responses are still pending." It appears in 43 code locations across 12 files, adding complexity to every status-checking path.

### Decision
After Phase 4, `WAITING_CHILDREN` is removed. Instances stay `PROCESSING` while children are running. CorrelationManager tracks "waiting for responses" in-memory.

### Rationale
- `WAITING_CHILDREN` is a transient state that adds complexity everywhere
- The information it carries is exactly what CorrelationManager tracks
- Removing it simplifies the status machine: `IDLE → PROCESSING → COMPLETED | ERROR | TERMINATED`
- 43 references → 0 references is a major simplification
- API consumers get `PROCESSING` (which is accurate)

### Alternatives Considered
1. **Keep `WAITING_CHILDREN` but derive it from CM**: Rejected — still requires all 43 check sites to know about it
2. **Replace with a boolean flag on instance**: Rejected — just another `waiting_for` variant
3. **Keep status but reduce check sites**: Rejected — partial solution, doesn't address root complexity

### Consequences
- External API consumers may depend on `WAITING_CHILDREN` in responses → need backward compat shim
- Status queries change: `WHERE status = 'waiting_children'` → `WHERE status = 'processing'` + CM check
- Recovery/restart logic must use CM instead of status to find waiting parents

---

## ADR-008: Direct Callback for Correlation Events (Not EventBus Queue)

**Date**: 2026-06-16
**Status**: Accepted (Fix C2 + C3)

### Context
CorrelationManager needs to emit `correlation.complete` events when all message responses are resolved. The existing EventBus was the obvious candidate, but two critical limitations prevent its use for this purpose:
- **C2**: `EventBus.create_event()` ALWAYS persists to DB (`event_bus.py:174-181`) — there is no ephemeral mode. Correlation events are in-memory state that shouldn't be persisted.
- **C3**: `EventBus._broadcast_to_global()` uses `put_nowait()` (`event_bus.py:347`) which silently drops events on queue full (`except QueueFull: logger.warning(...)`). A dropped `correlation.complete` means a parent stuck in PROCESSING forever.

### Decision
CorrelationManager delivers `correlation.complete` via a **direct async callback** registered at initialization, NOT through EventBus. The callback is invoked within CorrelationManager's per-parent Lock.

### Rationale
- No DB persistence needed (in-memory state, rebuildable on restart)
- No queue overflow risk (direct function call within async context)
- No event ordering issues (called within the per-parent Lock)
- Simpler than modifying EventBus to support ephemeral mode
- CM still subscribes to EventBus for *inbound* lifecycle events (shadow validation) — it just doesn't use EventBus for *outbound* correlation events

### Alternatives Considered
1. **(A) Separate lightweight in-memory pub-sub channel**: Rejected — unnecessary complexity; direct callback is simpler and sufficient for 1 subscriber
2. **(B) Use EventBus but accept DB persistence**: Rejected — unnecessary DB writes for ephemeral state; correlation events fire frequently (~1/sec) and have no replay value
3. **(C) Modify EventBus to support `ephemeral=True` flag**: Rejected — modifying shared infrastructure for one consumer; risk of breaking existing EventBus behavior

### Consequences
- CorrelationManager has a direct dependency on its callback consumer (JobFeedbackObserver)
- If the callback is slow (LLM fetch), it blocks within the Lock — acceptable at ~1 msg/sec
- Future consumers of correlation events would need to be wired as additional callbacks or a future migration to a proper pub-sub channel

---

## ADR-009: Per-Parent asyncio.Lock for Concurrency (Not EventBus Serialization)

**Date**: 2026-06-16
**Status**: Accepted (Fix C4)

### Context
The original plan assumed that all calls to `check_parent_completion()` (Phase 3) happen within the EventBus consumer loop, providing serialization. Codebase verification revealed this is wrong — the cascade decision sites are called from 4 different concurrent contexts:
1. `task_processor.py:389` — WorkerPool thread
2. `message_job_handler.py:317` — JobQueue asyncio task
3. `manager.py:2743` — resume background asyncio task
4. `worker_pool.py:400` — WorkerPool thread via MainLoopBridge

These are NOT within the EventBus loop. Without explicit concurrency control, two concurrent `resolve_response` calls for the same parent could both see `pending_count > 0` and both decide "not complete" — then neither fires `correlation.complete`.

### Decision
Each `parent_id` gets its own `asyncio.Lock`. All `register_message_send`, `resolve_response`, and state checks for the same parent are serialized through this Lock. Different parents process in parallel (different Locks).

### Rationale
- Per-parent locks are fine-grained: different parents process concurrently
- At ~1 msg/sec volume, lock contention is negligible (max 50 children per parent, each completing at ~1/sec)
- The Lock makes the ordering of register/resolve deterministic, not racy
- `asyncio.Lock` is the standard asyncio primitive — no external dependencies

### Alternatives Considered
1. **(B) Route ALL completion checks through EventBus**: Rejected — would require every cascade caller to publish to EventBus, massive refactor, and EventBus still has C2/C3 issues
2. **(C) DB-level locking (`SELECT ... FOR UPDATE`)**: Rejected — not portable (SQLite doesn't support `FOR UPDATE`), adds DB round-trips, and the decision is fundamentally in-memory (set operations)
3. **Global lock**: Rejected — serializes all parents unnecessarily; only same-parent operations need serialization

### Consequences
- Each `resolve_response` call acquires and releases a Lock — minor overhead
- If callback is slow (LLM fetch within Lock), other operations for the same parent wait — acceptable at ~1 msg/sec
- Lock dict grows with number of active parents — cleaned up when parent is deregistered

---

## ADR-010: Pure In-Memory Set Operations for Race #3 Elimination

**Date**: 2026-06-16
**Status**: Accepted (Fix C5)

### Context
The original Phase 3 plan claimed to eliminate Race #3 (the `pending_count` snapshot race in `child_reports.py:478-524`) but still used `SELECT COUNT(*) FROM MessageQueue` to check pending messages. This left the exact same TOCTOU window: a concurrent `enqueue_message` could insert between the COUNT and the status commit.

### Decision
Race #3 is eliminated by replacing the DB `count_pending` query with **pure in-memory set operations**:
1. `register_message_send(parent, child, message_id)` adds to `pending[parent]` set
2. `resolve_response(parent, child, message_id)` removes from `pending[parent]` set
3. When set is empty → fire `correlation.complete` callback

No `SELECT COUNT(*)` anywhere in the completion path. The in-memory set IS the source of truth.

### Rationale
- Set `discard` within per-parent Lock is atomic — no window for concurrent inserts
- A concurrent `register_message_send` for the same parent acquires the Lock first, adds to set, then releases — the `resolve_response` sees the non-empty set and doesn't fire premature completion
- The "message arrives after child completes" edge case is handled by the Lock: register either runs before resolve (set grows, no premature completion) or after (new cycle starts)
- No DB query means no DB-level race condition

### Alternatives Considered
1. **Keep count_pending but within a DB transaction**: Rejected — SQLite isolation levels are unreliable for this; PostgreSQL `SERIALIZABLE` would work but isn't portable
2. **Use DB-level advisory locks around the count**: Rejected — not portable, adds latency
3. **Accept the race and add retry logic**: Rejected — doesn't eliminate the race, just masks it

### Consequences
- CorrelationManager's in-memory set must perfectly mirror the actual message lifecycle
- If CM loses an entry (e.g., crash without rebuild), a parent could be stuck — mitigated by rebuild_from_db
- The `count_pending` query is removed entirely from the completion path — simpler code

---

## ADR-006: Pipeline with Strategy Callbacks for Dual-Path Unification

**Date**: 2026-06-16
**Status**: Accepted (Phase 5 — Optional)

### Context
WorkerPool and JobQueue paths share 14 processing stages but differ in dispatch mechanism (Task table vs JobQueue table). Full convergence would require unifying the dispatch layer.

### Decision
Extract a `MessageProcessingPipeline` that encapsulates shared stages. Each path provides callbacks for path-specific concerns (`on_success`, `on_error`, `on_defer`).

### Rationale
- Strategy/callback pattern is established in the codebase (ExecutionGate's `work_fn`)
- Avoids inheritance (which would create fragile base class with conditionals)
- Each path customizes only its unique concern
- Centralizes event emission (permanently fixes "missing error reporting" class of bugs)

### Alternatives Considered
1. **Full dispatch layer unification**: Rejected — huge scope, requires architectural decision beyond this plan
2. **Inheritance with template method**: Rejected — fragile, hard to test, conditionals in base class
3. **Shared mixin/module functions**: Rejected — implicit dependencies, harder to reason about

### Consequences
- New `MessageProcessingPipeline` class to maintain
- Callbacks must be async (adds slight complexity)
- Both paths must be kept in sync with pipeline interface changes
- Mirroring points reduced from 14 to ≤5

---

## ADR-007: Conservative Error Propagation in Cascade

**Date**: 2026-06-16
**Status**: Accepted

### Context
When a child errors, the parent's terminal status could be either "completed" (if other children succeeded) or "error" (if any child failed). Current behavior is inconsistent: Site 1A preserves `ERROR`, Site 2 overwrites to `COMPLETED`.

### Decision
If any child errored, parent terminal status is "error" (conservative approach). CorrelationManager's `_determine_terminal_status()` reads `ParentCorrelation.had_error` flag, which is set to `True` when any `resolve_response` call receives `status in ("error", "failed")` — BEFORE the entry is popped from the pending set (Fix N2). This ensures the error flag survives even when the pending set is empty at completion time.

### Rationale
- Conservative is safer: a parent with an errored child should reflect that
- Fixes the divergence where error path cascade was asymmetric
- Users/API consumers get accurate signal that something went wrong
- The parent's own processing result is still available in `result_summary`

### Alternatives Considered
1. **Optimistic (succeed unless ALL children errored)**: Rejected — hides failures, misleading status
2. **Majority vote**: Rejected — over-engineered, and "2 of 3 succeeded" still has a failure
3. **Configurable policy**: Rejected — unnecessary complexity for this use case

### Consequences
- Parents with any failed child always get `ERROR` status
- Error cascade (error report to grandparent) fires more often
- Must ensure error reports don't loop infinitely (existing dedup at `error_reporting.py:97-138` handles this)

---

## ADR-011: `waiting_for` Kept as Rebuild-Only Cache (Not Dropped)

**Date**: 2026-06-16
**Status**: Accepted (Fix A1)

### Context
Phase 1's `rebuild_from_db()` queries `instances WHERE waiting_for > 0` to identify parents needing correlation tracking after daemon restart. The original Phase 4 plan proposed dropping the `waiting_for` column entirely. This would break crash recovery: after a restart, `rebuild_from_db()` would find no parents with pending correlations → CM state empty → parents stuck in PROCESSING forever.

The `message_queue` table alone cannot reconstruct correlation because it is **direction-blind** — there is no `sender_id` column. A message in the queue could be a parent→child send or a child→parent completion report. Without direction, reconstructing `(parent_id, child_id, message_id)` correlation pairs from `message_queue` alone is unreliable.

### Decision
Keep the `waiting_for` column permanently as a **rebuild-only cache**. Continue writing to it (increment at `send_message`, decrement at child completion). Remove all *reads* of `waiting_for` for runtime control-flow decisions — those use CorrelationManager. The column is never dropped.

### Rationale
- `rebuild_from_db()` needs a durable signal for "which parents have pending correlations" — `waiting_for > 0` provides this
- Writes are cheap (single atomic SQL per send_message/completion — already implemented and proven)
- The column is one integer per instance row — negligible storage cost
- `message_queue` direction-blindness makes pure-queue rebuild unreliable
- Keeping the column avoids a risky schema migration with no clear benefit

### Alternatives Considered
1. **(B) Persistent `correlation_state` table**: Rejected — `waiting_for` already serves the same purpose; adding a second table is redundant
2. **(C) `source_instance_id` column on `message_queue`**: Rejected — too invasive for this migration; would be a separate project. Would permanently solve direction-blindness but scope creep
3. **(D) PROCESSING job_queue_items + hierarchy join**: Rejected — fragile; depends on job state which may not exist for WorkerPool path; complex multi-table join for rebuild

### Consequences
- `waiting_for` column persists permanently (never dropped)
- Code must maintain two parallel tracking systems (CM in-memory + DB column) — but they're already aligned by Phase 1's shadow mode
- Future developers must understand the column is rebuild-only, not for runtime decisions — mitigated by deprecation logging and model docstring

---

## ADR-012: Root Completion Uses Two Independent Conditions (Not resolve_response)

**Date**: 2026-06-16
**Status**: Accepted (Fix A2)

### Context
Site 1B (`child_reports.py:685-715`) handles root instance self-completion: when a root instance finishes processing a message, it checks whether it has pending work. The original Phase 3 plan proposed calling `cm.resolve_response(parent_id=instance_id, child_id=instance_id, ...)` — a self-referential call that would create a key `f"{instance_id}:{message_id}"` that never matches any registered key. The call would always return `False`, silently skipping the completion transition.

The root cause: root instance self-completion is a fundamentally different concern from parent-child response correlation. The plan conflated "does this instance have pending queue messages?" with "has this instance received all child responses?"

### Decision
Site 1B uses **two independent conditions**, both of which must be true for root completion:
1. **All child responses received** → read-only check: `cm.is_complete(instance_id)` (does NOT modify CM state)
2. **No pending messages in own queue** → existing `SELECT COUNT(*) FROM MessageQueue` logic, kept as-is

Site 1B does NOT call `resolve_response`. The existing queue count query stays because it checks a different concern (self-pending-work, not child correlation).

### Rationale
- `resolve_response` is for child→parent response correlation — the root checking its own queue is not a response
- The self-referential key would never match — `resolve_response` always returns `False`
- The queue count in Site 1B is NOT subject to Race #3 (it checks the root's own messages from external sources, not child-sent messages that race with cascade decisions)
- `cm.is_complete()` is a pure read that doesn't modify state — safe to call for informational checks

### Alternatives Considered
1. **Register root's own messages in CM**: Rejected — would require registering every `enqueue_message` call as a correlation entry; conflates two different concepts
2. **Skip Site 1B entirely (let CM callback handle root completion)**: Rejected — root instances without children would never fire `correlation.complete` (no registered correlations)

### Consequences
- Site 1B retains a `SELECT COUNT(*)` query (the only cascade site that does)
- This query is acceptable because it's a different concern (self-pending-work check, not correlation)
- Root completion logic is explicit: two conditions, both documented

---

## Decision Summary

| ADR | Title | Status | Phase |
|-----|-------|--------|-------|
| 001 | In-memory state (no DB table) — message-response tracking | Accepted (Revised C1) | 1 |
| 002 | Subscribe to existing EventBus (inbound only) | Accepted | 1 |
| 003 | Shadow mode before cutover | Accepted | 1 |
| 004 | CM owns status + event publication (via callback) | Accepted | 3 |
| 005 | Remove WAITING_CHILDREN status | Accepted | 4 |
| 006 | Pipeline with callbacks | Accepted (optional) | 5 |
| 007 | Conservative error propagation | Accepted | 3 |
| 008 | Direct callback for correlation events (not EventBus queue) | Accepted (Fix C2+C3) | 1-2 |
| 009 | Per-parent asyncio.Lock for concurrency | Accepted (Fix C4) | 1-3 |
| 010 | Pure in-memory set operations for Race #3 | Accepted (Fix C5) | 3 |
| 011 | `waiting_for` kept as rebuild-only cache (not dropped) | Accepted (Fix A1) | 4 |
| 012 | Root completion uses two independent conditions | Accepted (Fix A2) | 3 |
