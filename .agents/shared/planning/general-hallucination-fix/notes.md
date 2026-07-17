# Working Notes: General Hallucination Loop Breaker

## Exploration Findings Summary

### graph.py Architecture (Verified)

- **Single LLM call site**: `agent_node()` at graph.py:891-898 (`loop.run_in_executor(None, lambda: current_llm.invoke(full_messages))`)
- **`full_messages` construction**: `[SystemMessage(content=system_prompt)] + list(messages)` (graph.py:738)
- **Conditional routing**: `should_continue()` at graph.py:458-507 — routes to tools/agent/nudge/END
- **NO loop guards** on ghost-promise ("agent" return) or nudge paths — only `recursion_limit=100` protects against infinite loops
- **Reactive compaction**: graph.py:899-958 — catches `ContextLengthExceededError`, runs `compact_state`, calls `aupdate_state`, re-appends `injected_msg`, re-invokes LLM
- **InjectionSlot**: graph.py:73-109 — duck-typed `getattr` delegation to `manager._pending_injections`
- **ToolThrottleSlot**: graph.py:112-146 — same pattern, delegates to `manager._gii_throttle`
- **Factory closure**: `create_agent_node()` (graph.py:690-702) captures all deps including slots

### manager.py State (Verified)

- `_gii_throttle: dict[str, int]` at manager.py:731
- `_pending_injections: dict[str, dict[str, str]]` at manager.py:727
- Accessor methods: `bump_gii_throttle`, `reset_gii_throttle`, `get_gii_throttle_count` at manager.py:2028-2045
- `_cleanup_instance_state` at manager.py:2047-2110 — centralized cleanup helper

### compaction.py Reuse Patterns (Verified)

- `RemoveMessage(id=X)` — sentinel for `add_messages` reducer to remove by ID
- `_build_replacement_messages()` (compaction.py:1013-1048) — builds `[RemoveMessage(...) * N, summary, preserved...]`
- `_call_summarization_llm()` (compaction.py:969-1011) — LLM call with `clean_llm_config`, `asyncio.to_thread`
- `_partition_injected_messages()` (compaction.py:95-120) — splits by `additional_kwargs["injected_message"]`
- `graph.aupdate_state(thread_config, {'messages': replacement}, as_node='agent')` — the canonical state update pattern

### 5 Cleanup Paths (Verified — Exact Locations)

| # | Location | File:Line | Pattern |
|---|----------|-----------|---------|
| 1 | `_cleanup_instance_state` | manager.py:2084 | `self._gii_throttle.pop(instance_id, None)` |
| 2 | `terminate_instance` | instance_lifecycle.py:1419 | `self._manager._gii_throttle.pop(instance_id, None)` |
| 3 | `hard_delete_instance` zombie sweep | instance_lifecycle.py:1843 | `self._manager._gii_throttle.pop(iid, None)` (in loop) |
| 4 | `cancel_graph_task` done-branch | manager.py:4541 | `self._gii_throttle.pop(instance_id, None)` |
| 5 | `pause_instance_cascade` | instance_lifecycle.py:1992 | `self._manager._gii_throttle.pop(node_id, None)` |

### 2 Build Sites (Must Stay in Sync)

| # | Location | File:Line |
|---|----------|-----------|
| 1 | Spawn path | instance_lifecycle.py:1155-1169 |
| 2 | Restore path | instance_lifecycle.py:2460-2474 |

## Key Gotchas

1. **Fresh UUID for repair message**: `f"repair-{uuid4()}"`. Reusing an existing ID replaces instead of appends (LangGraph `add_messages` reducer behavior).

2. **RemoveMessage order**: Sentinels must come BEFORE the repair message in the replacement list (reducer processes left-to-right).

3. **clean_llm_config**: MUST call before constructing `ThinkingChatOpenAI` from shared `llm_config` dict. Strips `model_vision`.

4. **Injected message re-append**: After state re-read, re-append `injected_msg` to `full_messages` (C3 pattern, graph.py:944-950). Otherwise the user's injected message is lost.

5. **Both build sites in sync**: Spawn path (instance_lifecycle.py:1165-1169) and restore path (2470-2474) must have identical slot instantiation. Phase 2 of user-msg-injection flagged this as a risk.

6. **Test stubs must mirror manager attrs**: Grep `tests/` for `_ManagerStub` or similar. Add `_loop_breaker_state` + accessor methods.

7. **Reactive compaction double-LLM-call**: graph.py:899-958 can invoke the LLM twice in one `agent_node` entry (first call fails with ContextLengthExceededError, compaction runs, second call succeeds). The loop breaker runs BEFORE the first LLM call, so it won't double-count. But if repair changes the message count, it could affect whether reactive compaction triggers.

8. **`aupdate_state` inside agent_node**: The exploration confirmed that calling `aupdate_state(as_node='agent')` INSIDE `agent_node` then re-invoking LLM directly works (unlike calling it outside and then `astream(None)` which hits LangGraph issue #6433). The reactive compaction pattern at graph.py:928 proves this works.

9. **Summarization LLM call timeout (REVIEW FIX)**: The `_summarize_loop` call MUST be wrapped in `asyncio.wait_for(timeout=summarization_timeout_seconds)`. Without this, a hung LLM provider blocks `agent_node` indefinitely, freezing the agent. On `asyncio.TimeoutError`, fall back to a static truncation summary. Config field: `LoopBreakerConfig.summarization_timeout_seconds` (default 30).

10. **Evidence message retention (REVIEW FIX)**: The scan algorithm MUST populate `evidence_message_ids` with the IDs of the oldest matching call+result pair. These IDs are excluded from `RemoveMessage` sentinels so the agent retains context about what it was doing. Without evidence, the agent sees only the repair message with no context.

11. **max_repairs config field (REVIEW FIX)**: `LoopBreakerConfig` MUST include `max_repairs: int = 3`. Phase 3's `agent_node` integration uses this to cap repair attempts. Without it, the agent could be repaired infinitely, wasting LLM calls.

## Test File References

- `tests/test_gii_throttle.py` (~970 lines) — Template for slot-based tests
- `tests/unit/test_nudge_behavior.py` — Template for routing tests
- `tests/unit/test_compaction.py` — Template for compaction/replacement tests

## Open Questions (Resolved)

1. **Should GII throttle be replaced?** → No, coexist (D3). Complementary mechanisms.

2. **RAM-only or DB-persisted state?** → RAM-only (D4). Follows `_gii_throttle` pattern. No migration needed.

3. **SystemMessage or HumanMessage for repair?** → SystemMessage (D9). System-level intervention.

4. **Detection in should_continue or agent_node?** → agent_node (D11). Has all needed context.

5. **Handle ghost-promise/nudge cycles?** → Deferred (D8). Tool-call loops first. Repair mechanism is extensible.

## Implementation Order Recommendation

1. **Phase 1** (4-5h): Detection + config + state — can be fully unit-tested in isolation
2. **Phase 2** (4-5h): Repair engine — can start once Phase 1 interfaces are defined (loose coupling)
3. **Phase 3** (4-5h): Integration + cleanup + tests — must wait for Phase 1+2 (tight coupling)

**Total estimated time**: 12-15 hours (1.5-2 days)
