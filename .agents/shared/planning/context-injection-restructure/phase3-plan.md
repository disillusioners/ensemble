# Phase 3: Inject Context into `agent_node` (Local `full_messages`)

## Objective
Wire `assemble_context_messages()` into `agent_node` — the same local-injection pattern used by the RAM-queue (graph.py:2009-2023) and report injections (graph.py:2147-2161). Context messages are assembled INSIDE `agent_node` (async context) and extended into the LOCAL `full_messages` variable. They never enter graph input, never enter state, never enter checkpoint.

Also: remove all string prepending from `instance_messaging.py` and unify skill injection into the new builder format.

This is the **central integration phase** — it connects Phase 1's builder to the actual message pipeline.

## Coupling
- **Depends on**: Phase 2 (tight — requires mode flag + defense instruction)
- **Coupling type**: tight
- **Shared files with other phases**: `graph.py` (shared with no other phase in v2), `instance_messaging.py` (shared with no other phase in v2)
- **Shared APIs/interfaces**: `create_agent_node()` factory signature grows — needs context assembly dependencies
- **Why this coupling**: This phase is the integration point; Phases 4-5 depend on context flowing through the new path

## Context
- Phase 1 completed: `assemble_context_messages()` exists and returns `list[HumanMessage]`
- Phase 2 completed: mode flag in place; context appenders dormant when `human_messages`; defense instruction added
- **Key architectural correction (per reviewer C1/C2)**: Context is NOT injected via `_build_graph_input()`. It's assembled INSIDE `agent_node` and extended into local `full_messages`.

## ⚠️ Three Wiring Gaps (B1/B2/B3) — Must All Be Fixed

Three specific paths silently drop context messages if not handled. Each is independently fatal.

### B1: Loop-Breaker Repair Path Drops Context

`_maybe_repair_loop` (graph.py:2253) returns rebuilt `(messages, full_messages)` where `full_messages` is reconstructed at line 1344 as `[SystemMessage(system_prompt)] + list(messages)`. This rebuild drops ALL locally-injected messages.

**Existing precedent**: Report messages are re-appended after this rebuild at lines 2284-2287 using object identity check.

**Fix**: Add parallel C3-style re-append for `context_msgs` immediately after the existing report-msg re-append block (after line 2287).

### B2: ContextSlot Cannot Reach Messaging Path Results

The plan's `ContextSlot.set_skill_injection_result()` stores on `self`, but `instance_messaging.py` only holds the compiled graph — no ContextSlot reference. Skill search results computed in the messaging path are unreachable.

**Existing precedent**: `InjectionSlot` solves this via manager-level indirection — `manager._pending_injections` dict (manager.py:747). Messaging path calls `manager.set_injection()`, slot calls `manager.get_injection()`.

**Fix**: Mirror this pattern. Add `manager._context_skill_results: dict[str, tuple[str, list[str]] | None]`. Messaging path writes via `manager.set_context_skill_result(instance_id, result)`. `ContextSlot.assemble()` reads via `manager.get_context_skill_result(instance_id)`.

### B3: Skill Injection Lost on Retry (`is_retry=True`)

Currently `_skill_injection_msg` is initialized to `None` (line 1789) and only set inside `if not is_retry:` (line 1807). On retry, it stays `None`. Today this works because the skill message was checkpointed — retries see it in history. After refactor, skills are ephemeral (never in checkpoint), so retries lose ALL skill context.

**Fix (Option B chosen)**: `assemble_context_messages()` inside `agent_node` always runs skill search when skill injection is enabled — regardless of retry status. The messaging path pre-computes the result on first attempt and stores it in the manager (per B2 fix). On retry, `agent_node` reads the stored result from the manager. If the stored result exists (first attempt ran), it's reused. If not, `assemble_context_messages()` runs the search itself.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `ContextSlot` class with manager-indirection | Encapsulates context assembly. Uses `manager.get_context_skill_result()` (NOT `self._skill_result`). Threaded into `create_agent_node()` via `build_instance_graph()` — same pattern as `InjectionSlot`. | `daemon/graph.py` (near InjectionSlot at line 115) |
| 2 | Add manager-level context skill store (B2 fix) | Add `manager._context_skill_results: dict[str, tuple[str, list[str]] \| None]` at manager.py:747 (parallel to `_pending_injections`). Add `set_context_skill_result(instance_id, result)` and `get_context_skill_result(instance_id)` methods. | `daemon/manager.py:747, 1909+` |
| 3 | Thread `ContextSlot` through graph factory | Add `context_slot` parameter to `build_instance_graph()` and `create_agent_node()`. Pass from spawn (instance_lifecycle.py:1331) and restore (instance_lifecycle.py:2733). | `daemon/graph.py:2684-2795`, `daemon/services/instance_lifecycle.py:1331, 2733` |
| 4 | Assemble context inside `agent_node` | After `full_messages` is built (line 1985) and BEFORE the LLM call. Call `await context_slot.assemble(...)` to get context messages. Inject into LOCAL `full_messages` between SystemMessage and state messages. | `daemon/graph.py:1983-1985` |
| 5 | Extract `user_query` for context matching | The last HumanMessage in `state['messages']` is the user request. Extract its text content for RAG matching (skill search, shared context file matching). | `daemon/graph.py` (inside agent_node) |
| 6 | **B1 fix: Loop-breaker re-append** | After `_maybe_repair_loop` returns (graph.py:2253-2266), add C3-style re-append for `context_msgs` parallel to the report-msg re-append at lines 2284-2287. Use object identity check (same as report msgs). | `daemon/graph.py:2284-2287` (after this block) |
| 7 | **B3 fix: Retry-safe skill search** | `assemble_context_messages()` checks for pre-computed skill result from manager (set by messaging path on first attempt). If found, reuses it. If not found (e.g., retry without first attempt having run), runs skill search itself. This ensures skills survive retry. | `daemon/services/context_messages.py`, `daemon/graph.py` (ContextSlot.assemble) |
| 8 | Handle compaction re-append (C3 analog) | After reactive compaction (graph.py:2326-2366), the retry rebuilds `compact_messages` from checkpoint. Context messages must be re-appended to `compact_messages` before the retry LLM call — same pattern as injected_msgs re-append at lines 2344-2358. | `daemon/graph.py:2326-2366` |
| 9 | Remove `format_project_context()` string prepending | Delete string concat at instance_messaging.py lines 1856 and 1888. Keep `format_project_context()` as deprecated wrapper. | `daemon/services/instance_messaging.py:1842-1897` |
| 10 | Remove `shared_context_kv_block` prepending | Delete string concat at instance_messaging.py lines 1909-2002. KV metadata is now in `build_project_context_message()`. | `daemon/services/instance_messaging.py:1909-2002` |
| 11 | Store skill search result to manager (B2 fix) | In messaging path, after `inject_skills()` runs (line 2071-2104), store result via `manager.set_context_skill_result(instance_id, (injection_text, injected_skill_ids))`. This makes it available to `ContextSlot.assemble()` on both first attempt AND retry. | `daemon/services/instance_messaging.py:2071-2104` |
| 12 | Simplify `_build_graph_input()` | `_build_graph_input()` now returns ONLY `{"messages": [user_message]}` — no skill injection message. The skill result goes to manager, not graph input. | `daemon/services/instance_messaging.py:83-119` |
| 13 | Unify skill format in builder | `_format_injection()` output is wrapped by `build_skills_message()` with `[SYSTEM CONTEXT: Skills]` prefix. `inject_explicit_skill()` (`<meta>` tag) also goes through builder. REPLACE semantics preserved. | `daemon/services/skill_injection_service.py:573-711` |
| 14 | Integration tests | (a) Context appears in LLM input but NOT checkpoint. (b) Context survives loop-breaker repair. (c) Context survives compaction retry. (d) **Skills survive retry** (B3 test). (e) Skills work via `<meta>` tag. | `tests/integration/test_context_in_graph.py` (new) |

## Key Files
- `daemon/graph.py` — MODIFIED: `ContextSlot` class, `agent_node` assembly, **B1 loop-breaker re-append**, compaction re-append, factory wiring
- `daemon/manager.py` — MODIFIED: **B2 context skill store** (`_context_skill_results`, `set_context_skill_result`, `get_context_skill_result`)
- `daemon/services/instance_messaging.py` — MODIFIED: remove string prepending, **B2 store skill result to manager**, simplify `_build_graph_input()`
- `daemon/services/skill_injection_service.py` — MODIFIED: `_format_injection()` output wrapped by builder
- `daemon/services/context_messages.py` — MODIFIED: **B3 retry-safe skill search** in `assemble_context_messages()`
- `daemon/services/instance_lifecycle.py` — MODIFIED: pass `ContextSlot` to `build_instance_graph()`
- `tests/integration/test_context_in_graph.py` — NEW

## ContextSlot Design (B2 fix — manager indirection)

```python
class ContextSlot:
    """Encapsulates context assembly for agent_node.
    
    Mirrors InjectionSlot's manager-indirection pattern:
    - InjectionSlot reads from manager.get_injection() / manager._pending_injections
    - ContextSlot reads from manager.get_context_skill_result() / manager._context_skill_results
    
    The messaging path writes skill search results to the manager (not to this slot),
    so ContextSlot.assemble() can access them without a direct reference.
    """
    
    def __init__(self, manager: Any, agent_meta: Any):
        self._manager = manager
        self._agent_meta = agent_meta
    
    async def assemble(
        self, instance_id: str, user_query: str, project_id: str | None,
        instance_repository: Any, parent_id: str | None = None,
    ) -> list[HumanMessage]:
        """Assemble context messages. Called from agent_node."""
        mode = _resolve_injection_mode(self._agent_meta)
        if mode != "human_messages":
            return []  # legacy mode — context is in system prompt
        
        # B2: Read pre-computed skill result from MANAGER (not self)
        skill_result = None
        getter = getattr(self._manager, "get_context_skill_result", None)
        if getter is not None:
            skill_result = getter(instance_id)
        
        # B3: If no pre-computed result (e.g., retry without first attempt),
        # assemble_context_messages runs skill search itself
        return await assemble_context_messages(
            instance_id=instance_id,
            user_query=user_query,
            project_id=project_id,
            agent_meta=self._agent_meta,
            manager=self._manager,
            instance_repository=instance_repository,
            parent_id=parent_id,
            skill_injection_result=skill_result,  # may be None → builder searches
        )
```

## agent_node Flow (After — with B1/B3 fixes)

```python
async def agent_node(state, config=None):
    messages = state['messages']
    full_messages = [SystemMessage(content=system_prompt)] + list(messages)
    instance_id = config['configurable']['thread_id']
    
    # ── Context injection (NEW) ──────────────────────────────
    context_msgs: list[HumanMessage] = []
    if context_slot is not None:
        user_query = _extract_last_user_text(messages)
        project_id = _resolve_project_id(config)
        context_msgs = await context_slot.assemble(
            instance_id, user_query, project_id, instance_repository, parent_id
        )
        if context_msgs:
            # Insert AFTER SystemMessage, BEFORE state messages
            full_messages = (
                [SystemMessage(content=system_prompt)]
                + context_msgs
                + list(messages)
            )
    
    # ── RAM-queue injections (EXISTING, unchanged) ──────────
    injected_msgs: list[HumanMessage] = []
    if injection_slot is not None:
        # ... existing code at line 2009-2023 ...
        full_messages.extend(injected_msgs)
    
    # ── Report injections (EXISTING, unchanged) ─────────────
    injected_report_msgs: list[HumanMessage] = []
    if report_injection_slot is not None:
        # ... existing code at line 2147-2161 ...
        full_messages.append(report_msg)
    
    # ── Loop-breaker repair (EXISTING + B1 FIX) ─────────────
    messages, full_messages = await _maybe_repair_loop(...)
    
    # C3 re-append for report msgs (EXISTING, line 2284-2287)
    if injected_report_msgs and not any(
        injected_report_msgs[0] is m for m in full_messages
    ):
        full_messages = full_messages + injected_report_msgs
    
    # B1 FIX: C3 re-append for context msgs (NEW)
    if context_msgs and not any(
        context_msgs[0] is m for m in full_messages
    ):
        full_messages = (
            [SystemMessage(content=system_prompt)]
            + context_msgs
            + [m for m in full_messages if not isinstance(m, SystemMessage)]
        )
    
    # ── LLM call ────────────────────────────────────────────
    try:
        response = await loop.run_in_executor(
            None, lambda: current_llm.invoke(full_messages)
        )
    
    # ── Compaction path (EXISTING + context re-append) ──────
    except ContextLengthExceededError:
        # ... compaction runs on checkpoint state ...
        compact_messages = [SystemMessage] + updated_state['messages']
        # Re-append RAM-queue injections (EXISTING)
        for inj in injected_msgs:
            compact_messages.append(inj)
        # Re-append report injections (EXISTING)
        for rmsg in injected_report_msgs:
            compact_messages.append(rmsg)
        # Re-append context messages (NEW — same C3 pattern)
        for ctx in context_msgs:
            compact_messages.append(ctx)
        response = await loop.run_in_executor(
            None, lambda: current_llm.invoke(compact_messages)
        )
    
    # ── Return (checkpoint) ─────────────────────────────────
    # Context messages NOT included — they never enter state.
    if injected_msgs or injected_report_msgs:
        persisted = []
        persisted.extend(injected_msgs)
        persisted.extend(injected_report_msgs)
        persisted.append(response)
        return {'messages': persisted}
    return {'messages': [response]}
```

## B2/B3 Skill Result Threading

```
MESSAGING PATH (instance_messaging.py):
  ┌──────────────────────────────────────────────┐
  │ if not is_retry:                              │
  │   skill_result = inject_skills(message, ...)  │
  │   manager.set_context_skill_result(           │  ← B2: write to manager
  │       instance_id, skill_result               │
  │   )                                           │
  └──────────────────────────────────────────────┘
                     │
                     ▼
AGENT_NODE (graph.py):
  ┌──────────────────────────────────────────────┐
  │ context_slot.assemble(instance_id, ...)       │
  │   skill_result = manager.get_context_skill_   │  ← B2: read from manager
  │       result(instance_id)                     │
  │   if skill_result is None:                    │
  │     # B3: retry without first attempt         │
  │     # assemble_context_messages runs search   │
  │   context_msgs = assemble_context_messages(   │
  │       ..., skill_injection_result=skill_result│
  │   )                                           │
  └──────────────────────────────────────────────┘
```

## Three Re-Append Sites Summary

| Site | Trigger | Existing Pattern | New Addition |
|------|---------|-----------------|--------------|
| Loop-breaker repair (line 2253) | Hallucination loop detected | Report msgs re-appended at 2284-2287 | **B1: context_msgs re-appended** |
| Compaction retry (line 2297) | ContextLengthExceededError | Injected msgs re-appended at 2344-2358 | Context msgs re-appended (same block) |
| Return dict (line 2410) | Normal return | Only response (+ injected/report msgs) | Context msgs EXCLUDED (ephemeral) |

## Constraints
- Context messages must appear AFTER SystemMessage, BEFORE state messages in `full_messages`
- Order: `[SYSTEM CONTEXT: Related Project]` → `[SYSTEM CONTEXT: Shared Context]` → `[SYSTEM CONTEXT: Skills]`
- Context messages must NEVER be in the return dict (not checkpointed)
- **B1**: Loop-breaker repair must re-append context messages (object identity check)
- **B2**: ContextSlot reads skill results from MANAGER, not from `self`
- **B3**: Skill search must survive retry — either via manager-stored result or re-search
- Compaction retry must re-append context messages (C3 analog)
- If `context_injection_mode` is `system_prompt`, `context_slot.assemble()` returns `[]` (no-op)
- `_build_graph_input()` stays sync, returns only `{"messages": [user_message]}`
- **Opencode path is OUT OF SCOPE (per ADR-13)**: Do NOT modify `external_opencode_send_message`'s `related_context_keywords` mechanism or any code that calls it.

## Deliverables
- [ ] `ContextSlot` class created with manager-indirection (B2)
- [ ] Manager-level context skill store added (B2)
- [ ] `agent_node` assembles and injects context into local `full_messages`
- [ ] **B1**: Loop-breaker repair re-appends context messages
- [ ] **B3**: Skill search survives retry (manager-stored result or re-search)
- [ ] Context messages NOT in return dict (not checkpointed)
- [ ] Compaction retry re-appends context messages
- [ ] No string prepending at instance_messaging.py lines 1856, 1888, 1909-2002
- [ ] `_build_graph_input()` returns only `[user_message]`
- [ ] Skill injection uses `[SYSTEM CONTEXT: Skills]` format
- [ ] Integration test: context appears in LLM input but not checkpoint
- [ ] Integration test: **skills survive retry** (B3 test)
- [ ] Integration test: context survives loop-breaker repair (B1 test)
