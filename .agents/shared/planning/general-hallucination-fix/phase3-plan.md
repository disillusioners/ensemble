# Phase 3: Integration & Cleanup

## Objective

Wire the detection system (Phase 1) and repair engine (Phase 2) into `agent_node`, thread the `LoopBreakerSlot` through `build_instance_graph` → `create_agent_node`, add all 5 cleanup paths for `_loop_breaker_state`, ensure GII throttle coexistence, and write integration tests.

## Coupling

- **Depends on**: Phase 1 (LoopDetector, LoopBreakerSlot, InstanceManager state), Phase 2 (LoopRepairer)
- **Coupling type**: tight — modifies the same `agent_node` function body and the same files Phase 1 touched
- **Shared files with other phases**: `daemon/graph.py` (agent_node integration), `daemon/manager.py` (cleanup), `daemon/services/instance_lifecycle.py` (5 cleanup paths)
- **Shared APIs/interfaces**: `agent_node` (modified), `build_instance_graph` (new param), `create_agent_node` (new param)
- **Why this coupling**: Integration touches the exact same code regions — must wait for Phase 1+2 completion and review.

## Context

- Previous phases completed: Phase 1 provides detection classes + state; Phase 2 provides `LoopRepairer`
- Key decisions: GII throttle coexists (runs before loop detection). Repair runs inside `agent_node` before LLM call (same pattern as reactive compaction). The `LoopRepairer` is instantiated via factory closure (same as slots).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `loop_breaker_slot` param to `create_agent_node` | New param `loop_breaker_slot: LoopBreakerSlot \| None = None` at graph.py:702. Thread into `agent_node` closure. | `daemon/graph.py:690-702` |
| 2 | Add `loop_breaker_slot` + `loop_repairer` to `build_instance_graph` | New params at graph.py:1276. Thread into `create_agent_node(...)` call at line 1361-1373. | `daemon/graph.py:1263-1277, 1361-1373` |
| 3 | Integrate detection + repair into `agent_node` | Insert detection block AFTER GII throttle (graph.py:889), BEFORE LLM call (graph.py:891). On detection: call `LoopRepairer.repair()`, use repaired messages for LLM invocation. | `daemon/graph.py:889-898` |
| 4 | Instantiate slots at both build sites | Add `LoopBreakerSlot(self._manager)` at spawn path (instance_lifecycle.py:1167) and restore path (instance_lifecycle.py:2472). | `daemon/services/instance_lifecycle.py:1165-1169, 2470-2474` |
| 5 | Add cleanup to all 5 paths | Add `self._manager._loop_breaker_state.pop(instance_id, None)` (or `self._loop_breaker_state.pop(...)`) at all 5 cleanup locations. | `daemon/manager.py:2084`, `daemon/services/instance_lifecycle.py:1419, 1843, 1992`, `daemon/manager.py:4541` |
| 6 | Pass `LoopBreakerConfig` from InstanceManager config | Read `self._config.loop_breaker` (or similar) and pass threshold/excluded_tools to `LoopDetector.scan()`. | `daemon/services/instance_lifecycle.py`, `daemon/config.py` |
| 7 | Write integration tests | Test: full flow (detection → repair → re-invoke), GII throttle still works alongside, cleanup on all 5 paths, config disable flag works. | `tests/test_loop_breaker_integration.py` |
| 8 | Update test stubs | Find `_ManagerStub` or similar test stubs that simulate InstanceManager. Add `_loop_breaker_state` + accessor methods to keep stubs in sync. | `tests/` (grep for stubs) |

## Key Files

- `daemon/graph.py:690-702` — `create_agent_node` signature
- `daemon/graph.py:736-991` — `agent_node` inner function
- `daemon/graph.py:871-898` — GII throttle + LLM call (integration point)
- `daemon/graph.py:1263-1277` — `build_instance_graph` signature
- `daemon/graph.py:1361-1373` — Node registration / slot threading
- `daemon/manager.py:2047-2110` — `_cleanup_instance_state` (cleanup path 1)
- `daemon/manager.py:4535-4542` — `cancel_graph_task` done-branch (cleanup path 4)
- `daemon/services/instance_lifecycle.py:1165-1169` — Spawn path instantiation
- `daemon/services/instance_lifecycle.py:1402-1419` — `terminate_instance` (cleanup path 2)
- `daemon/services/instance_lifecycle.py:1838-1843` — `hard_delete_instance` zombie sweep (cleanup path 3)
- `daemon/services/instance_lifecycle.py:1985-1992` — `pause_instance_cascade` (cleanup path 5)
- `daemon/services/instance_lifecycle.py:2470-2474` — Restore path instantiation

## Integration Point: agent_node

The detection + repair block goes **between the GII throttle** (graph.py:871-889) **and the LLM call** (graph.py:891-898):

```python
# ── get_instance_info throttling (EXISTING — keep as-is) ────────────
if throttle_slot is not None:
    last_msg = messages[-1] if messages else None
    if isinstance(last_msg, ToolMessage) and last_msg.name == GII_TOOL_NAME:
        count = throttle_slot.bump(instance_id)
        if count >= 3:
            delay = GII_DELAY_MAP.get(count, GII_MAX_DELAY)
            logger.info(f"[THROTTLE] Instance {instance_short}: gii #{count}, sleeping {delay}s")
            await asyncio.sleep(delay)
    else:
        throttle_slot.reset(instance_id)

# ── General hallucination loop detection + repair (NEW) ──────────────
if loop_breaker_slot is not None and loop_repairer is not None:
    # Get config (threshold, excluded tools)
    lb_config = loop_breaker_config or LoopBreakerConfig()
    
    if lb_config.enabled:
        detection = LoopDetector.scan(
            messages=messages,
            threshold=lb_config.threshold,
            excluded_tools=lb_config.excluded_tools,
        )
        
        if detection is not None:
            # Check repair count — don't repair infinitely
            repair_count = loop_breaker_slot.get_repair_count(instance_id)
            if repair_count < lb_config.max_repairs:  # default 3
                logger.warning(
                    f"[LOOP BREAKER] Instance {instance_short}: detected "
                    f"{detection.repetition_count}x repeated '{detection.tool_name}' "
                    f"calls. Triggering repair (attempt {repair_count + 1}/{lb_config.max_repairs})"
                )
                
                # Execute repair
                repair_context = RepairContext(
                    detection=detection,
                    messages=messages,
                    thread_config=config or {},
                    graph=graph_ref[0] if graph_ref else None,
                    llm_config=llm_config or {},
                    system_prompt=system_prompt,
                    injected_msg=injected_msg,
                    summarization_timeout_seconds=lb_config.summarization_timeout_seconds,
                )
                result = await loop_repairer.repair(repair_context)
                
                if result.success:
                    # Record repair
                    loop_breaker_slot.record_repair(instance_id, result.summary)
                    
                    # Use repaired messages for LLM call
                    messages = result.repaired_messages
                    full_messages = [SystemMessage(content=system_prompt)] + list(messages)
                    logger.info(
                        f"[LOOP BREAKER] Repair complete, re-invoking LLM with "
                        f"{len(full_messages)} messages (repair msg: {result.repair_message_id[:16]}...)"
                    )
                else:
                    logger.error(
                        f"[LOOP BREAKER] Repair failed: {result.error}, "
                        f"continuing with original messages"
                    )
            else:
                logger.warning(
                    f"[LOOP BREAKER] Instance {instance_short}: max repairs "
                    f"({lb_config.max_repairs}) reached, forcing continuation"
                )
        else:
            # No loop detected — reset repair count if it was set
            if loop_breaker_slot.get_repair_count(instance_id) > 0:
                loop_breaker_slot.clear(instance_id)

# ── LLM invocation (EXISTING — now uses potentially-repaired messages) ──
try:
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: current_llm.invoke(full_messages)
    )
```

## Cleanup Paths (All 5)

Each location gets `_loop_breaker_state.pop(instance_id, None)` added **next to** the existing `_gii_throttle.pop`:

### Path 1: `_cleanup_instance_state` (manager.py:2084)

```python
# After line 2084:
self._gii_throttle.pop(instance_id, None)
# NEW:
self._loop_breaker_state.pop(instance_id, None)
```

### Path 2: `terminate_instance` (instance_lifecycle.py:1419)

```python
# After line 1419:
self._manager._gii_throttle.pop(instance_id, None)
# NEW:
self._manager._loop_breaker_state.pop(instance_id, None)
```

### Path 3: `hard_delete_instance` zombie sweep (instance_lifecycle.py:1843)

```python
# In the loop at line 1838-1843:
for iid in tree_ids:
    self._manager._graph_tasks.pop(iid, None)
    self._manager._gii_throttle.pop(iid, None)
    # NEW:
    self._manager._loop_breaker_state.pop(iid, None)
```

### Path 4: `cancel_graph_task` done-branch (manager.py:4541)

```python
# After line 4541:
self._gii_throttle.pop(instance_id, None)
# NEW:
self._loop_breaker_state.pop(instance_id, None)
```

### Path 5: `pause_instance_cascade` (instance_lifecycle.py:1992)

```python
# After line 1992:
self._manager._gii_throttle.pop(node_id, None)
# NEW:
self._manager._loop_breaker_state.pop(node_id, None)
```

## Build Site Updates

### Spawn path (instance_lifecycle.py:1155-1169)

```python
from ..graph import InjectionSlot, ToolThrottleSlot, LoopBreakerSlot, LoopRepairer
graph = build_instance_graph(
    tools=tools,
    checkpointer=self._checkpointer,
    llm_config=llm_config,
    system_prompt=system_prompt,
    retry_config=retry_config,
    compactor=self._compactor,
    graph_config=config,
    user_language=user_language,
    language_check_enabled=self._config.language.check_enabled,
    injection_slot=InjectionSlot(self._manager),
    live_hub=self._manager._live_hub,
    throttle_slot=ToolThrottleSlot(self._manager),
    # NEW:
    loop_breaker_slot=LoopBreakerSlot(self._manager),
    loop_repairer=LoopRepairer(llm_config),
    manager=self._manager,
)
```

### Restore path (instance_lifecycle.py:2460-2474)

```python
# IDENTICAL changes as spawn path
loop_breaker_slot=LoopBreakerSlot(self._manager),
loop_repairer=LoopRepairer(llm_config),
```

## GII Throttle Coexistence

The GII throttle and the loop breaker **coexist** — they serve different purposes:

| Feature | GII Throttle | Loop Breaker |
|---------|-------------|--------------|
| Scope | `get_instance_info` only | ANY tool |
| Action | Sleep delay | Message repair + compaction |
| Mechanism | `asyncio.sleep` | `RemoveMessage` + LLM summary + repair `SystemMessage` |
| When | After tool execution, before LLM call | After tool execution, before LLM call |
| Order | Runs FIRST (graph.py:871-889) | Runs SECOND (new block after 889) |

**Coexistence logic**: The GII throttle checks `messages[-1].name == GII_TOOL_NAME`. If GII triggers, it sleeps. Then the loop breaker scans for ANY repeated tool pattern. If the agent was calling `get_instance_info` repeatedly, the loop breaker would also detect it (since GII calls are just tool calls) and repair the messages. This is **complementary**: the sleep gives the LLM provider a breather, and the repair gives the LLM fresh context.

**Optional future refactor**: Once the loop breaker is stable, the GII throttle could be refactored to use the loop breaker's detection (adding `get_instance_info` to the general detection instead of hardcoding). This is deferred — see decisions.md.

## Test Strategy

### Unit Tests (Phase 1 + 2 already)
- `tests/unit/test_loop_detector.py` — Detection logic
- `tests/unit/test_loop_repairer.py` — Repair logic

### Integration Tests (Phase 3)

`tests/test_loop_breaker_integration.py`:

1. **Full flow test**: Mock messages with 3+ identical tool calls → verify detection → verify repair → verify state updated → verify LLM re-invoked with repaired messages
2. **GII coexistence**: Mock messages with GII calls → verify GII throttle fires AND loop breaker fires → both work
3. **Config disable**: Set `LoopBreakerConfig(enabled=False)` → verify no detection or repair
4. **Max repairs**: Trigger 4 repairs → verify 5th detection is skipped (max_repairs=3)
5. **Cleanup test**: Mock InstanceManager → trigger cleanup paths → verify `_loop_breaker_state` popped
6. **Parallel tool calls**: Mock AIMessage with 3 parallel identical tool calls → verify detected as single loop
7. **Excluded tools**: Add tool to excluded list → verify not detected
8. **Fallback on LLM error**: Mock LLM to raise → verify static repair message used
9. **Injected message re-append**: Mock `injected_msg` → verify re-appended after repair
10. **Fresh UUID**: Verify repair message always has unique ID (no collision)
11. **Summarization timeout**: Mock LLM to hang → verify `asyncio.wait_for` fires after timeout → verify static fallback summary is used instead → verify repair still completes

### Test Stub Updates
- Grep `tests/` for `_ManagerStub` or similar stubs
- Add `_loop_breaker_state: dict = {}` to stub `__init__`
- Add `get_loop_breaker_state`, `record_loop_repair`, `reset_loop_breaker`, `get_loop_repair_count` methods
- Reference: `tests/test_gii_throttle.py` (~970 lines) as the template

## Constraints

- **Both build sites must stay in sync** (spawn + restore paths) — this was flagged as a risk in Phase 2 of user-msg-injection
- **Test stubs must mirror manager attrs** — otherwise tests crash on missing `_loop_breaker_state`
- **GII throttle must NOT be broken** — it runs first, loop breaker runs second
- **Repair count limits** — don't repair infinitely (max_repairs default 3). After max, force continuation with original messages.
- **PostgreSQL + SQLite**: `_loop_breaker_state` is RAM-only (dict on InstanceManager), so no DB schema changes needed. No migration required.
- **No DB persistence needed**: The loop-breaker state is transient (per-session RAM). Unlike `_pending_injections` which persists via checkpoint, the loop counter resets on session restart — which is the correct behavior (a restarted session shouldn't inherit stale loop state).

## Deliverables

- [ ] `loop_breaker_slot` + `loop_repairer` params threaded through `create_agent_node` and `build_instance_graph`
- [ ] Detection + repair block integrated into `agent_node` (between GII throttle and LLM call)
- [ ] `LoopBreakerSlot` + `LoopRepairer` instantiated at both build sites (spawn + restore)
- [ ] Cleanup added to all 5 lifecycle paths
- [ ] Config threaded from `InstanceManager._config`
- [ ] Integration tests (10 scenarios)
- [ ] Test stubs updated
- [ ] GII throttle verified working alongside loop breaker
