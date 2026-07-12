# Phase 1: Backend Core — RAM Injection Slot, Factory Closure, Agent Node Consumption, Compaction Fixes, Cleanup

## Objective
Add a RAM-only single-slot injection mechanism to InstanceManager, thread it into the LangGraph agent node via factory closure (C1), consume it before each LLM call with checkpoint persistence (C2), fix both compaction paths to preserve injected messages (C3), clear it on pause/terminate/clear_all (W1), add a TTL sweeper (S1), and wire SSE emission hooks for Phase 2.

After this phase, the backend can store, retrieve, consume (with checkpoint persistence), and clean up injected messages — but the API routing and SSE emission are finalized in Phase 2.

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `daemon/manager.py` (Phase 2 calls `set_injection`), `daemon/graph.py` (Phase 2 adds SSE emission via `live_hub` from closure), `daemon/services/instance_lifecycle.py` (Phase 2 adds SSE emission on clear)
- **Shared APIs/interfaces**: `InstanceManager.set_injection()`, `get_injection()`, `clear_injection()` + factory closure parameters on `build_instance_graph()` — these are the contract Phase 2 depends on
- **Why this coupling**: Phase 2's send_message API and SSE events directly call Phase 1's helper methods and use the factory closure wiring. The injection mechanism must exist before the API can route to it.

## Context
- InstanceManager is a singleton with `_graph_tasks` dict (instance_id → asyncio.Task) and `_request_registry` for LLM request tracking (~line 700-750 in manager.py)
- Agent node is `create_agent_node()` in `daemon/graph.py` lines 258-391, LLM call at `current_llm.invoke(full_messages)` via `loop.run_in_executor()`
- `build_instance_graph()` (~lines 400-561) uses factory closure pattern — `compactor` and `graph_ref` are threaded through as closure parameters. **This is the pattern to follow for C1.**
- Pause is `pause_instance_cascade()` in `daemon/services/instance_lifecycle.py` with 3 cancellations per node
- Language check reminder uses `additional_kwargs={'language_check_reminder': True}` at graph.py:520-530 — **this is the pattern to follow for C2's `injected_message` flag**
- Reactive compaction handler is at graph.py:641-684 (ContextLengthExceededError)
- Proactive compaction is in `daemon/compaction.py` and called from `daemon/services/instance_messaging.py:523` (`_maybe_compact_context`)

## Tasks

### 1.1 — RAM Injection Slot on InstanceManager

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `_pending_injections` dict | Initialize `self._pending_injections = {}` alongside `_graph_tasks` and `_request_registry` (~line 700-750 in manager.py). Maps `instance_id → {"content": str, "timestamp": str}`. | `daemon/manager.py` |
| 2 | Add `set_injection(instance_id, content)` | Store `{"content": content, "timestamp": datetime.utcnow().isoformat()}` in `self._pending_injections[instance_id]`. Overwrites any existing entry (single-slot replace semantics). Return the stored dict. | `daemon/manager.py` |
| 3 | Add `get_injection(instance_id)` | Return `self._pending_injections.get(instance_id)` or `None`. Does NOT clear — consumption is separate. | `daemon/manager.py` |
| 4 | Add `clear_injection(instance_id)` | Pop and return `self._pending_injections.pop(instance_id, None)`. Safe when no injection exists. Returns cleared entry (for SSE content) or None. | `daemon/manager.py` |
| 5 | Add centralized `_cleanup_instance_state(instance_id)` helper (W1) | Consolidate cleanup that pops from `_graph_tasks`, `_request_registry`, AND `_pending_injections` in one call. Refactor existing cleanup code to use this helper where possible. | `daemon/manager.py` |

### 1.2 — Cleanup in ALL 5 Paths (W1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 6 | Cleanup in `_release_cached_instance` | Add `_pending_injections.pop(instance_id, None)` (or call `_cleanup_instance_state`) alongside existing `_graph_tasks` and `_request_registry` cleanup at manager.py:1756. | `daemon/manager.py` |
| 7 | Cleanup in `pause_instance_cascade` | In `daemon/services/instance_lifecycle.py:1478`, inside the per-instance pause logic, after the 3 cancellations: call `manager.clear_injection(instance_id)`. Store cleared content for SSE emission (Phase 2 will add the SSE call). | `daemon/services/instance_lifecycle.py` |
| 8 | Cleanup in `terminate_instance` (W1) | In `daemon/services/instance_lifecycle.py:1124`, add `manager.clear_injection(instance_id)` alongside existing graph task cancellation and job cleanup. | `daemon/services/instance_lifecycle.py` |
| 9 | Cleanup in `clear_all_instances` (W1) | In `daemon/services/instance_lifecycle.py:1992`, add `self._pending_injections.clear()` alongside existing bulk cleanup of `_graph_tasks` and `_request_registry`. | `daemon/services/instance_lifecycle.py` |
| 10 | Cleanup in project cascade delete (W1) | In `daemon/routers/projects.py`, ensure InstanceManager cleanup (including `_pending_injections`) is called for all instances in the project being deleted. May already be covered if project delete calls `clear_all_instances` or per-instance cleanup — verify and add if missing. | `daemon/routers/projects.py` |

### 1.3 — TTL Sweeper (S1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 11 | Add TTL sweeper for orphaned injection slots | Add to InstanceManager's existing periodic cleanup (alongside `_cleanup_cached_instances` which runs on 4-hour TTL). Check `_pending_injections` entries for timestamps older than 1 hour and remove them. These accumulate if instance crashes without proper cleanup or is stuck in WAITING_CHILDREN forever. | `daemon/manager.py` |

### 1.4 — Factory Closure Wiring (C1)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 12 | Add `injection_slot` parameter to `build_instance_graph()` | Following the `compactor`/`graph_ref` pattern, add `injection_slot=None` parameter. `injection_slot` is a callable or lightweight object wrapping InstanceManager methods: `get(instance_id) → dict|None`, `clear(instance_id) → dict|None`. Do NOT pass the full InstanceManager — pass a minimal handle for testability. | `daemon/graph.py` |
| 13 | Add `live_hub` parameter to `build_instance_graph()` | Add `live_hub=None` parameter. This is the LiveEventHub reference, threaded through the same closure. Used for SSE emission in Phase 2. | `daemon/graph.py` |
| 14 | Thread both parameters into `create_agent_node()` | Pass `injection_slot` and `live_hub` from `build_instance_graph()` closure into `create_agent_node()` factory. The agent_node function captures them via closure, same as `compactor`. | `daemon/graph.py` |
| 15 | Verify `instance_id` availability in agent_node | The agent_node receives LangGraph state. Trace how instance_id is accessed (likely via config or state). Verify it's available — if not, thread it through the factory as well. | `daemon/graph.py` |
| 16 | Update all callers of `build_instance_graph()` | Find where `build_instance_graph()` is called (likely in manager.py or instance spawn logic) and pass `injection_slot` and `live_hub`. Create the injection_slot handle from InstanceManager methods. | `daemon/manager.py`, `daemon/graph.py` |

### 1.5 — Agent Node Consumption with Checkpoint Persistence (C2)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 17 | Add injection check before `current_llm.invoke()` | In `create_agent_node()` before the LLM call (~line 322-328): (1) Call `injection_slot.get(instance_id)`; (2) If injection exists, create `HumanMessage(content=injection["content"], additional_kwargs={"injected_message": True})` — the `additional_kwargs` flag follows the `language_check_reminder` pattern at graph.py:520-530 (C2); (3) Append to `full_messages`; (4) Call `injection_slot.clear(instance_id)`; (5) Store `injected_msg` reference for return value. | `daemon/graph.py` |
| 18 | Return BOTH messages for checkpoint persistence (C2) | Change the return value: if `injected_msg is not None`, return `{'messages': [injected_msg, response]}` so the `add_messages` reducer persists BOTH the injected HumanMessage and the LLM response. If no injection, return `{'messages': [response]}` (existing behavior). This ensures: crash recovery preserves the message, GET /messages history includes the user turn, conversation coherence is maintained. | `daemon/graph.py` |
| 19 | Add SSE consumption hook (for Phase 2) | After `injection_slot.clear(instance_id)`, add a placeholder for SSE emission: `if live_hub and injected_msg: await live_hub.stream_message(instance_id, event_type="injection_consumed", ...)`. The actual `stream_message` call will be finalized in Phase 2 — for now, verify `live_hub` is accessible and the call structure works. | `daemon/graph.py` |

### 1.6 — Compaction Fixes (C3)

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 20 | Fix reactive compaction to preserve injected message (C3) | In the ContextLengthExceededError handler (graph.py:641-684): after building `compact_messages` from checkpoint state, re-append the `injected_msg` (if it was set in this iteration) to `compact_messages` before re-invoking the LLM. The injected message is a local variable in the agent_node closure, so it's available even though checkpoint state doesn't have it yet. | `daemon/graph.py` |
| 21 | Fix proactive compaction to skip injected messages (C3) | In `daemon/compaction.py`, add a check for `additional_kwargs.get("injected_message")` to skip injected messages from summarization. Follow the exact same pattern as `language_check_reminder` — messages with this flag are preserved verbatim and not included in the compaction summary. This ensures injected messages from prior turns survive proactive compaction. | `daemon/compaction.py` |
| 22 | Verify `_maybe_compact_context` compatibility | In `daemon/services/instance_messaging.py:523`, verify that `_maybe_compact_context` calls the updated compaction logic. No change should be needed if the compaction.py fix is properly implemented — but verify the call chain. | `daemon/services/instance_messaging.py` |

### 1.7 — Unit Tests

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 23 | Unit tests for injection slot mechanics | Test: set → get returns content; set twice → 2nd replaces 1st; get after clear → None; clear when empty → None (no error); set → clear → get → None; `_cleanup_instance_state` clears all 3 dicts. | `tests/test_injection_slot.py` (new) |
| 24 | Unit tests for agent node consumption | Test: injection present → HumanMessage appended with `injected_message` flag → both messages returned; injection absent → only response returned; injection cleared after consumption. Mock the injection_slot callable. | `tests/test_injection_graph.py` (new) |
| 25 | Unit tests for compaction preservation | Test: reactive compaction re-appends injected_msg; proactive compaction skips messages with `injected_message` flag. | `tests/test_injection_compaction.py` (new) |

## Key Files
- `daemon/manager.py` — `_pending_injections` dict + 3 helper methods + `_cleanup_instance_state` helper + TTL sweeper + update `build_instance_graph` caller
- `daemon/graph.py` — Factory closure wiring (C1) + injection consumption (C2) + reactive compaction fix (C3) + return both messages
- `daemon/compaction.py` — Skip `injected_message` flagged messages from summarization (C3)
- `daemon/services/instance_lifecycle.py` — Clear injection in pause + terminate + clear_all (W1)
- `daemon/routers/projects.py` — Clear injection in project cascade delete (W1)
- `daemon/services/instance_messaging.py` — Verify `_maybe_compact_context` compatibility (C3)
- `tests/test_injection_slot.py` — Slot mechanics unit tests (new)
- `tests/test_injection_graph.py` — Agent node consumption unit tests (new)
- `tests/test_injection_compaction.py` — Compaction preservation unit tests (new)

## Constraints
- **RAM-only**: No DB persistence for the slot itself. The injected HumanMessage IS persisted to checkpoint via C2's return value, but the `_pending_injections` dict is RAM-only.
- **Single slot**: One injection per instance. 2nd set replaces 1st.
- **Factory closure, NOT singleton (C1)**: Do NOT access InstanceManager/LiveEventHub via module-level singleton in graph.py. Thread through factory closure for test isolation.
- **Checkpoint persistence (C2)**: The injected HumanMessage MUST be returned in the agent_node return dict so `add_messages` reducer persists it. Use `additional_kwargs={"injected_message": True}` marker.
- **Compaction preservation (C3)**: Both reactive and proactive compaction paths must preserve injected messages. Reactive: re-append to compacted list. Proactive: skip from summarization via flag.
- **No SSE emission yet (partial)**: Phase 1 sets up the `live_hub` closure parameter and placeholder calls. Phase 2 finalizes the actual `stream_message` calls with proper event types.
- **No API routing yet**: Phase 1 does NOT modify send_message API. Only the slot mechanics + consumption + compaction + cleanup.
- **Cleanup in ALL 5 paths (W1)**: Every instance lifecycle cleanup path must clear `_pending_injections`.

## Deliverables
- [ ] `_pending_injections` dict initialized on InstanceManager
- [ ] `set_injection()`, `get_injection()`, `clear_injection()` methods working
- [ ] `_cleanup_instance_state()` centralized helper implemented
- [ ] Cleanup in all 5 paths: `_release_cached_instance`, `pause_instance_cascade`, `terminate_instance`, `clear_all_instances`, project cascade delete
- [ ] TTL sweeper cleans orphaned slots >1h old
- [ ] `build_instance_graph()` accepts `injection_slot` + `live_hub` parameters (C1)
- [ ] Agent node consumes injection before `current_llm.invoke()` via closure
- [ ] Agent node returns both `[injected_msg, response]` for checkpoint persistence (C2)
- [ ] `injected_message` flag on HumanMessage via `additional_kwargs`
- [ ] Reactive compaction re-appends injected message (C3)
- [ ] Proactive compaction skips `injected_message` flagged messages (C3)
- [ ] Pause cascade clears injection slot
- [ ] All unit tests pass (slot mechanics + agent node + compaction)
- [ ] No regressions in existing test suite
