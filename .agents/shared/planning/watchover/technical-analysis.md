# Technical Analysis: Watchover Feature

Date: 2026-08-05T20:19:54Z
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive
Status: Ready for Review

## Question

How should the Watchover feature (a watcher that intercepts each tool call of a watched instance BEFORE the ToolNode executes, evaluates Allow/Deny, and terminates the instance after 3 denials in a turn; activated per-instance via an FE button that pauses → compacts → enables interception → resumes) be architected so it reuses the project's existing explicit-slot threading, deferred-cascade pause/resume, and single-transaction DB patterns, while remaining agent-agnostic, using bifurcated failure handling (judgment=fail-closed, infra=fail-open per LD-2), and crash-safe?

## Context Summary

agents-ensemble runs each agent instance as a compiled LangGraph built by `build_instance_graph()` (`daemon/graph.py:3203-3416`). The graph is a small, explicit topology: `START → agent → (should_continue) → tools → (post_tools_router) → agent`, with optional `language_check`, `nudge`, and `question_pause_node` branches. Middleware-style behavior is implemented NOT through a generic middleware framework but through named "slots" (`InjectionSlot`, `ReportInjectionSlot`, `ToolThrottleSlot`, `LoopBreakerSlot`, `LoopBreakerConfig`, `ContextSlot`) that are threaded into `create_agent_node(...)` via factory closures (`daemon/graph.py:2262-2290`) and resolved from `InstanceManager` at graph build time (`daemon/services/instance_lifecycle.py:956-988`, mirrored on restore at `2556-2586`). The watcher interception must follow this exact pattern (C-5, NFR-19).

Two existing interception patterns are directly reusable as templates:

1. **Post-tools conditional router** — `create_post_tools_router(manager)` (`daemon/graph.py:3056-3097`) returns a closure read by `graph.add_conditional_edges("tools", ..., {...})` (`daemon/graph.py:3394-3401`). It reads `manager.is_question_pause_requested(instance_id)` on every evaluation and routes to `"question_pause_node"` or `"agent"`. This is the closest existing precedent for a router that reads per-instance manager state and branches the graph.

2. **should_continue wrapper** — `create_should_continue(language_check_enabled)` (`daemon/graph.py:2239-2259`) wraps the base `should_continue` to translate `END → "end_candidate"` and route through a new `language_check` node. This demonstrates how to add a new conditional branch off the `agent` node without modifying the base router.

The watcher invocation must be a lightweight single LLM call, NOT a full `spawn_instance` (FR-18, NFR-1). The canonical in-graph LLM-call pattern is `LoopRepairer._summarize_loop` (`daemon/graph.py:1320-1410`): it builds a prompt, constructs a `ThinkingChatOpenAI(**clean_llm_config(llm_config))`, and invokes it via `await asyncio.wait_for(asyncio.to_thread(llm.invoke, [...]), timeout=...)`. The same pattern is used by `ContextCompactor._call_summarization_llm` (`daemon/compaction.py:968-1010`). The watcher must follow this shape.

Pause/resume MUST reuse the deferred-cascade pattern (C-6, NFR-20). `question_pause_node` (`daemon/graph.py:3100-3200`) sets a deferred marker (`manager.set_deferred_question_pause`) and returns `{}`; the graph routes to END; the actual `pause_instance_cascade` runs from the post-graph completion path in `daemon/services/instance_messaging.py` (`976-991` and `3550-3565`) AFTER `_graph_tasks[instance_id]` is popped, wrapped in `asyncio.shield`. This avoids the C2 torn-state bug (a cascade that self-cancels the in-flight graph task via `task.cancel()`, interrupting its DB write). Activation and deactivation must follow the same deferred shape.

Termination MUST reuse `manager.terminate_instance(instance_id)` (`daemon/manager.py:5303-5318`) → `InstanceLifecycleService.terminate_instance` (`daemon/services/instance_lifecycle.py:1138-1602`), which performs the in-memory cleanup, then a single-transaction DB cascade via `_terminate_instance_db_sync` (`daemon/services/instance_lifecycle.py:2716-2960`) wrapped in `asyncio.to_thread`, then post-commit SSE (`stream_status_change(instance_id, "terminated", ...)` at `1399-1408`). The JobItem rows are cancelled with `terminal_reason='aborted'` inside the same transaction (`2931-2948`). No new `AdmissionState` is needed (C-10); a new `terminal_reason='watchover_terminated'` discriminator value is sufficient and is the established extension point (`daemon/repositories/job_queue/models.py:299-305`, `daemon/services/work_status.py:102-114`).

Per-instance state storage is already available without a schema migration: the `instances` table has an `instance_metadata` JSONB column (`daemon/repositories/instance/models.py:64-67`) accessed atomically via `InstanceRepository.set_metadata` / `delete_metadata` (`daemon/repositories/instance/repository.py:782-845`), which use dialect-aware `jsonb_set`/`json_set` single-statement UPDATEs to avoid read-modify-write races. Watchover flags and context go here.

## Architecture

### Current Patterns

- **Explicit slot threading** — `InjectionSlot`, `ReportInjectionSlot`, `ToolThrottleSlot`, `LoopBreakerSlot`, `ContextSlot` (`daemon/graph.py:142-186`, `221-330`, `625-688`); each is a thin handle wrapping `InstanceManager`, threaded into `create_agent_node` and `build_instance_graph` via factory closures (`daemon/graph.py:2262-2290`, `3203-3222`).
- **Conditional post-tools router** — `create_post_tools_router(manager)` (`daemon/graph.py:3056-3097`), wired at `graph.py:3394-3401`.
- **Deferred-cascade pause** — `question_pause_node` sets `manager.set_deferred_question_pause(instance_id)` (`daemon/graph.py:3181-3182`); the post-graph completion path in `instance_messaging.py` peeks with `has_deferred_question_pause`, awaits `asyncio.shield(pause_instance_cascade(...))`, and pops in `finally` (`daemon/services/instance_messaging.py:976-991`, `3550-3565`).
- **Lightweight in-graph LLM call** — `LoopRepairer._summarize_loop` (`daemon/graph.py:1320-1410`) and `ContextCompactor._call_summarization_llm` (`daemon/compaction.py:968-1010`), both using `asyncio.to_thread(llm.invoke, [...])` with `asyncio.wait_for` timeout.
- **Single-transaction DB cascade** — `_terminate_instance_db_sync` (`daemon/services/instance_lifecycle.py:2716-2960`) and `_pause_cascade_db_sync` / `_resume_cascade_db_sync` (`daemon/services/instance_lifecycle.py:3156-3281`, `3545-3742`), all wrapped in `WriteGuardSession` via `asyncio.to_thread`.
- **Atomic instance_metadata write** — `InstanceRepository.set_metadata` (`daemon/repositories/instance/repository.py:782-845`), dialect-aware `jsonb_set`/`json_set`.

### Module Boundaries

```
[FE Watchover button] → [POST /instances/{id}/watchover]
        ↓                              ↓
[WatchoverService] ──pause/resume──→ [InstanceLifecycleService.pause_instance_cascade / resume_instance_cascade / resume_processing_job]
        ↓                              ↓
[ContextCompactor.compact_state] ← [compiled_graph.aget_state(config)]
        ↓
[InstanceRepository.set_metadata] (watchover_enabled, watchover_context, ...)
        ↓
[build_instance_graph(... watchover_slot=WatchoverSlot(manager) ...)]
        ↓
   ┌────────────────────────────────────────────────────────────┐
   │  agent → should_continue → watchover_check → tools → post_tools_router → agent  │
   │                                      ↓ (deny)                               │
   │                                   agent (+denial ToolMessages)               │
   │                                      ↓ (3-strikes)                          │
   │                            watchover_terminate_node                          │
   └────────────────────────────────────────────────────────────┘
        ↓ (deferred marker)
[post-graph completion path in instance_messaging.py] → asyncio.shield(terminate_instance)
```

The watcher LLM call is made from inside `watchover_check` via `WatchoverEvaluator` (a new helper, NOT a spawned instance). The watched agent's graph, the watcher's LLM call, the manager, and the instance repository are the four integration boundaries.

### Architecture Diagram (deep-dive only)

```mermaid
flowchart LR
    START([START]) --> agent[agent_node]
    agent -->|should_continue| sc{tools? / agent? / nudge? / END?}
    sc -->|tools| wc[watchover_check]
    sc -->|agent| agent
    sc -->|nudge| nudge[nudge]
    sc -->|END or end_candidate| lc[language_check]
    wc -->|all allow or mixed allowed subset| tools[ToolNode]
    wc -->|all deny, count below 3| agent
    wc -->|3-strikes| term[watchover_terminate_node]
    tools --> wpr{pending mixed denials?}
    wpr -->|yes| fin[watchover_finalize_denials]  %% ⚠️ ELIMINATED by LD-1 — see architecture-recommendation.md CR-1
    wpr -->|no| ppr{question pause?}
    fin --> ppr
    ppr -->|no| agent
    ppr -->|yes| qpn[question_pause_node]
    qpn --> END([END])
    term --> END
    lc -->|retry| agent
    lc -->|END| ENDL([END])
    nudge --> agent
```

When `watchover_enabled` is OFF for the instance, `watchover_check` short-circuits to `"tools"` (passthrough) and performs no LLM call, so the OFF path adds only one cheap router evaluation per tool-bearing turn (NFR-3, NFR-18).

### A. Interception Architecture (Core Decision)

#### A1. Topology and exact graph wiring

Add five explicit watchover artifacts next to the existing slot/router factories in `daemon/graph.py`:

- `WatchoverSlot(manager)` — thin, mock-friendly handle over per-instance watchover configuration/evaluation, matching `InjectionSlot` and `LoopBreakerSlot` (`daemon/graph.py:142-186`, `625-688`).
- `create_watchover_check_node(watchover_slot, evaluator)` — async node that evaluates the latest `AIMessage.tool_calls` before `ToolNode`.
- `should_end_watchover(state)` — pure router reading only control-plane state written by `watchover_check`.
- `create_watchover_terminate_node(manager)` — writes a deferred termination marker and returns; it MUST NOT call `terminate_instance()` in-graph.
> **⚠️ SUPERSEDED by LD-1 (deny-whole-batch):** the `watchover_finalize_denials` node and mixed-batch message-replacement machinery are eliminated for phase 1. If ANY tool call in a batch is denied, deny the entire batch (inject denial ToolMessages for all calls, route back to agent). See `architecture-recommendation.md` CR-1.
- `create_watchover_finalize_denials_node()` — mixed-batch-only node that restores the original AIMessage and appends denied-call ToolMessages after the allowed subset has executed. *(Historical — eliminated by LD-1; kept for analysis record.)*

The existing `"tools": "tools"` mapping appears twice because language-check-enabled and language-check-disabled graphs are wired separately (`daemon/graph.py:3350-3375`). Change both destinations to `"watchover_check"`. Do NOT change the base `should_continue`; it remains the authority that recognizes an `AIMessage` with tool calls (`daemon/graph.py:2008-2022`).

Proposed wiring (the changed/added edges are marked `NEW`):

```python
# nodes
graph.add_node("agent", create_agent_node(...))
graph.add_node("watchover_check", create_watchover_check_node(watchover_slot, evaluator))  # NEW
graph.add_node("watchover_terminate_node", create_watchover_terminate_node(manager))       # NEW
graph.add_node("watchover_finalize_denials", create_watchover_finalize_denials_node())     # NEW (mixed batch) — ⚠️ SUPERSEDED by LD-1: eliminated for phase 1. See architecture-recommendation.md CR-1.
graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))

# agent conditional edges — both language-check branches
{
    "tools": "watchover_check",   # CHANGED: was "tools"
    "agent": "agent",
    "nudge": "nudge",
    "end_candidate": "language_check",  # enabled branch only
    END: END,                              # disabled branch only
}

# pre-tool gate
graph.add_conditional_edges(
    "watchover_check",
    should_end_watchover,
    {
        "tools": "tools",                                  # Allow/passthrough
        "agent": "agent",                                  # Deny + ToolMessage(s)
        "watchover_terminate_node": "watchover_terminate_node",  # 3 strikes
    },
)

graph.add_edge("watchover_terminate_node", END)

# existing post-tool question router remains authoritative; a tiny wrapper
# gives pending mixed-batch denials first priority
# ⚠️ SUPERSEDED by LD-1 (deny-whole-batch): the watchover_finalize_denials node and
# mixed-batch message-replacement machinery are eliminated for phase 1. If ANY tool
# call in a batch is denied, deny the entire batch. See architecture-recommendation.md CR-1.
graph.add_conditional_edges("tools", create_watchover_post_tools_router(manager), {
    "watchover_finalize_denials": "watchover_finalize_denials",
    "agent": "agent",
    "question_pause_node": "question_pause_node",
})
graph.add_conditional_edges(
    "watchover_finalize_denials",
    create_post_tools_router(manager),
    {"agent": "agent", "question_pause_node": "question_pause_node"},
)
```

All-allowed and all-denied batches use only the three requested outcomes. A mixed batch still returns the `"tools"` outcome for its allowed subset, then takes the private post-tools finalization branch to append the denied results. This topology has no `agent → tools` edge. A topology unit test should inspect the compiled graph and assert that every edge reachable from `agent` on a `"tools"` decision passes through `watchover_check` before `ToolNode` (NFR-12). The language-check and nudge routes remain unchanged (`daemon/graph.py:3350-3375`, `3406`).

#### A2. Gate condition and OFF-path cost

`watchover_check` obtains `instance_id` from `config["configurable"]["thread_id"]`, exactly like `create_post_tools_router` (`daemon/graph.py:3080-3095`). It then calls `watchover_slot.get_config(instance_id)`. The source of truth is `Instance.instance_metadata`; a missing/false `watchover_enabled` means OFF (`daemon/repositories/instance/models.py:64-67`).

To satisfy NFR-3, `get_config` must be an O(1) read from a manager-owned cache hydrated from `instance_metadata` when the graph is built/restored and updated only after an activation/deactivation DB commit. It must NOT issue a synchronous repository read on every tool call; repository reads are sync SQLAlchemy calls that existing async paths explicitly offload via `asyncio.to_thread` (`daemon/services/instance_messaging.py:532-543`). The OFF path returns `{"watchover_route": "tools"}` immediately and makes no watcher LLM call.

If `watchover_enabled=true` but the stored context or watcher id is missing/corrupt, fail closed with a Deny reason (`watchover_config_invalid`), not passthrough. This prevents a partially activated row from silently disabling the control (NFR-5/NFR-12).

#### A3. Allow, Deny, and Terminate state contract

`watchover_check` writes only control-plane keys and `ToolMessage` results:

```python
# Allow
{
  "watchover_route": "tools",
  "watchover_deny_count": current_count,
  "watchover_turn_id": current_turn_id,
}

# Deny (<3)
{
  "watchover_route": "agent",
  "watchover_deny_count": new_count,
  "watchover_turn_id": current_turn_id,
  "messages": [ToolMessage(
      tool_call_id=call_id,
      name=tool_name,
      content=f"Watchover denied {tool_name} (attempt {new_count}/3): {reason}. "
              "Do not retry the same unsafe action; choose a safer alternative or ask the user.",
      additional_kwargs={"watchover_denial": True},
  )],
}

# Third strike
{
  "watchover_route": "watchover_terminate_node",
  "watchover_deny_count": 3,
  "watchover_turn_id": current_turn_id,
  "watchover_pending_termination": True,
  "messages": [final denial ToolMessage],
}
```

`should_end_watchover` is intentionally dumb: read `watchover_route`; map malformed/missing values to `"agent"` (fail closed when the node actually ran). It must never inspect tool arguments or call the LLM. On Deny, every denied call gets a `ToolMessage` with its original `tool_call_id`; this maintains the tool-call protocol and gives the watched agent a correction opportunity (FR-9). A dedicated `watchover_event` SSE should carry tool name, reason, counter, and turn id for the FE; relying only on GET `/messages` is insufficient because raw `ToolMessage` entries are omitted by the current message serialization path (`daemon/services/instance_messaging.py:643-648`).

The agent node should eagerly clear `watchover_deny_count` and `watchover_turn_id` when its LLM response has no `tool_calls`, by adding those keys to the same return dict that currently persists the response (`daemon/graph.py:2897-2929`). This implements the requirements' turn boundary without putting mutable state logic inside `should_continue`. The explicit `watchover_turn_id` comparison remains a crash/retry safety net: if a new work id reaches the gate before an eager reset, the gate resets the count before evaluating the first call of the new turn.

#### A4. Parallel tool calls (Gap #8)

Evaluate every call independently, preserving input order. The denial counter increments **per denied tool call**, not per AIMessage batch. Therefore, a batch containing three denied calls can reach the 3-strike threshold in one LLM response. Watcher evaluations may run concurrently with `asyncio.gather`, but all decisions must complete before any call is handed to `ToolNode`; a bounded watcher semaphore prevents one large batch from exhausting the default thread pool.

A mixed batch (some Allow, some Deny) is the one place where the simple three-route topology needs careful message handling. `ToolNode` executes every tool call in the latest `AIMessage`, so it cannot receive the original mixed batch. Recommended behavior:

> **⚠️ SUPERSEDED by LD-1 (deny-whole-batch):** the `watchover_finalize_denials` node and mixed-batch message-replacement machinery are eliminated for phase 1. If ANY tool call in a batch is denied, deny the entire batch (inject denial ToolMessages for all calls, route back to agent). See `architecture-recommendation.md` CR-1. The analysis below is retained as a historical record.

1. `watchover_check` evaluates all calls.
2. If all Allow: route to `tools` unchanged.
3. If all Deny and total count remains below 3: emit one denial `ToolMessage` per call and route to `agent`.
4. If mixed and count remains below 3: replace the latest `AIMessage` (same message id) with a copy whose `tool_calls` contains only allowed calls; checkpoint the original full call list + denied-result descriptors in private state; route to `tools`.
5. Extend the existing post-tools router with a first branch for `watchover_pending_denials`: after allowed calls execute, a small `watchover_finalize_denials` node restores the original `AIMessage` (same id) and appends denial `ToolMessage`s, then delegates to the existing question-pause router. This preserves a complete transcript: the restored AIMessage declares all original calls, allowed ToolMessages carry real results, denied ToolMessages carry rejection results.
6. If the cumulative count reaches 3 anywhere in the batch: terminate and execute **none** of the batch's allowed calls. Termination takes precedence over partial side effects.

The post-tools extension is needed to satisfy AC-EC.9 literally (each call independently evaluated and each Allow/Deny applied). The cheaper alternative — deny the whole batch when any call is denied — is safer and simpler but violates the acceptance criterion because an allowed call would not execute. The message-replacement/finalization path must have checkpoint/restart tests at each node boundary.

> **⚠️ SUPERSEDED by LD-1 (deny-whole-batch):** The leader ACCEPTED the cheaper alternative (deny-whole-batch) and re-scoped AC-EC.9 to: "each call independently evaluated; a denied call blocks its batch." The message-replacement/finalization path is eliminated for phase 1. See `architecture-recommendation.md` CR-1.

#### A5. Why not the alternatives?

- **Do not place watcher evaluation inside `should_continue`.** Routers are synchronous/pure and select a next-node name; the watcher needs an async LLM call and state/message updates. `create_should_continue` only translates a route string (`daemon/graph.py:2239-2259`), which is appropriate for language checking but not evaluation.
- **Do not wrap `ToolNode` directly.** This couples policy to tool execution, makes partial-batch semantics depend on LangChain internals, and weakens NFR-12 because a future call site could instantiate/use an unwrapped ToolNode.
- **Do not modify each tool implementation.** It would be agent/tool-specific, easy to bypass, and impossible to prove complete.
- **Do not introduce generic middleware.** The graph uses explicit named slots and closures; C-5 explicitly forbids a new middleware framework.

## Integration Points

| # | Integration | Type | Contract | Auth | Failure Mode | File:Line |
|---|-------------|------|----------|------|--------------|-----------|
| 1 | Watchover toggle API | sync REST | `POST /instances/{id}/watchover {enabled: bool, requirement?: str}` | session ownership (FR-27) | 404 unknown instance; 503 write-paused; 409 wrong state | `daemon/routers/instances.py` (new endpoint near `526-608`) |
| 2 | Pause/resume cascade | async manager call | `pause_instance_cascade(iid)` / `resume_instance_cascade(iid)` / `resume_processing_job(iid, message, silent)` | manager-internal | C2 torn-state if not deferred; guarded by `asyncio.shield` | `daemon/manager.py:5348-5380`, `daemon/services/instance_lifecycle.py:1785-2026` |
| 3 | Compaction for watchover_context | async call | `ContextCompactor.compact_state(CompactionContext)` → `CompactionResult.replacement_messages` / `.compacted_at` | none | returns `None` when not needed; falls back to raw recent messages | `daemon/compaction.py:596-781`, `daemon/services/instance_messaging.py:690-769` |
| 4 | instance_metadata JSONB | atomic SQL | `set_metadata(iid, key, value)` / `delete_metadata(iid, key)` | manager-internal | read-modify-write race if plain `update(instance_metadata=...)` used (rejected by `update()` at `repository.py:709-713`) | `daemon/repositories/instance/repository.py:782-845` |
| 5 | Watcher LLM call | async, in-graph | `await asyncio.wait_for(asyncio.to_thread(llm.invoke, [...]), timeout=...)` | none | **bifurcated (LD-2): judgment error → fail-closed Deny; infra error (timeout/5xx) → fail-open Allow + degraded SSE** (NFR-4) | `daemon/graph.py:1320-1410` (LoopRepairer pattern) |
| 6 | Watcher agent prompt | filesystem + registry | `load_and_cache_prompt("watcher", Path(meta.path), cache, ...)` → `(system_prompt, tokens)` | none | cache miss reloads from disk; registry `discover()` auto-registers | `daemon/loader.py:603-712`, `daemon/registry.py:235-345` |
| 7 | Termination | async manager call | `manager.terminate_instance(iid)` → `_terminate_instance_db_sync` (single txn) → post-commit SSE | manager-internal | C2 torn-state if called in-graph; MUST be deferred | `daemon/manager.py:5303-5318`, `daemon/services/instance_lifecycle.py:1138-1602` |
| 8 | SSE denial/termination event | async fire-and-forget | `live_hub.stream_message(iid, {...}, event_type="watchover_event")` | none | dropped silently when no SSE client connected | `daemon/services/live_event_hub.py:150-196` |
| 9 | Watchover state in graph | LangGraph state keys | `watchover_deny_count: int`, `watchover_turn_id: str \| None`, `watchover_route: str \| None`, pending mixed-batch state | none | auto-checkpointed; reset at turn boundary | `daemon/graph.py:1992-2005` (SessionState) |
| 10 | Watchover toggle endpoint authorization | REST guard | session-ownership check (FR-27) | session owner only | 403 on cross-session toggle | `daemon/routers/instances.py` (new; see Open Questions) |

### Integration Details

**Integration 3: Compaction for watchover_context.** During activation, the service fetches the live checkpoint via `compiled_graph.aget_state({"configurable": {"thread_id": instance_id}})` (same call as `_maybe_compact_context` at `daemon/services/instance_messaging.py:702`), builds a `CompactionContext` (`daemon/compaction.py:213-231`) from the messages + system-prompt token count + model name, and calls `compactor.compact_state(context)`. The resulting summary `SystemMessage` (shape `"[Conversation Summary]\nTimestamp: ...\n<summary>"`, `daemon/compaction.py:891-894`) is extracted as text and combined with the user requirement to form `watchover_context`. If compaction returns `None` (not needed) or the instance has very short history, fall back to the raw recent messages (AC-EC.7). The activation path must NOT mutate the checkpoint — it reads state and discards the `CompactionResult.replacement_messages` (it does not call `graph.aupdate_state`). This keeps activation side-effect-free apart from the `set_metadata` writes.

**Integration 5: Watcher LLM call.** The watcher prompt is a list of `BaseMessage`: `[SystemMessage(watcher_system_prompt), SystemMessage(watchover_context), *mirrored_messages, HumanMessage(tool_call_description)]`. The mirrored messages are a truncated tail of the watched instance's `state['messages']` (last ~10 messages, mirroring `LoopRepairer._build_excerpt` at `daemon/graph.py:1358`). The tool call is rendered as a JSON description (`name`, `args`). The watcher is invoked exactly like `LoopRepairer._summarize_loop`: `ThinkingChatOpenAI(**clean_llm_config(watcher_llm_config))` then `await asyncio.wait_for(asyncio.to_thread(llm.invoke, [...]), timeout=watcher_timeout)`. The response is parsed for `ALLOW`/`DENY` + reason. On `asyncio.TimeoutError` or provider/network `Exception` (infrastructure errors), the evaluator returns `Decision(deny=False, reason="watcher_infra_error")` and emits a degraded-safety SSE — fail-OPEN per LD-2 (+0 count). On malformed/unparseable response (judgment error), the evaluator returns `Decision(deny=True, reason="watcher_invalid_response")` — fail-CLOSED per LD-2 (+1 count).

**Integration 6: Watcher agent prompt.** A new `agents/watcher/` directory with `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md` (FR-17, C-4, NFR-22) is auto-registered by `AgentRegistry.discover()` at daemon start. The watcher is NEVER spawned as an instance. Instead, at graph build time (or lazily on first interception), the service resolves the watcher's system prompt via `load_and_cache_prompt("watcher", Path(meta.path), prompt_cache, mcp_tool_names=[], version_tag=None)` (`daemon/loader.py:603-712`) — the same function used for every other agent. The returned `system_prompt` is fed to the watcher LLM call as the first `SystemMessage`. This satisfies FR-17 (the watcher is a real agent definition following the convention) and FR-18 (it is invoked via a lightweight LLM call, not `spawn_instance`). Use the existing top-level `llm_model` for model selection; add a typed nested `watchover` config only for timeout/count/mirroring settings.

### B. Watcher Invocation Architecture

#### B1. Lightweight single-call evaluator

Use a dedicated `WatchoverEvaluator` helper, analogous to `LoopRepairer`, with no graph of its own and no tools. For each proposed tool call it builds:

1. `SystemMessage(watcher_system_prompt)` — composed from `agents/watcher/`.
2. `SystemMessage("[WATCHOVER POLICY CONTEXT — immutable control-plane data]\n" + watchover_context)`.
3. A bounded, role-labelled mirror of recent watched-instance messages. All mirrored content is explicitly framed as **untrusted observational data**, not instructions, following the same principle used by `_frame_injected_report` (`daemon/graph.py:188-218`).
4. `HumanMessage` containing canonical JSON for the candidate call: `{"tool_call_id": ..., "name": ..., "arguments": ...}` and a strict instruction to return JSON `{"decision": "allow"|"deny", "reason": "..."}`.

Invoke the unbound model with the LoopRepairer pattern:

```python
response = await asyncio.wait_for(
    asyncio.to_thread(watcher_llm.invoke, watcher_messages),
    timeout=watcher_timeout_seconds,
)
```

This is the established non-blocking + hard-timeout pattern (`daemon/graph.py:1372-1409`). Use `temperature=0`, a small output-token limit, and strict parsing. Unknown decision text, malformed JSON, missing reason on Deny, or unsupported content types all become Deny (`watcher_invalid_response`).

Do not reuse the graph's `llm_standard` object directly. Despite its name, the current builder binds tools to both the standard and primary LLM (`daemon/graph.py:2962-2971`). Construct a fresh **unbound** `ThinkingChatOpenAI` from the resolved watcher config so the watcher can only return text/JSON and cannot propose executable tool calls.

`asyncio.wait_for` stops the graph from waiting after the deadline, but it cannot kill a Python thread already executing `llm.invoke`; therefore the provider `request_timeout` should be set at or below the outer timeout and watcher calls should use a bounded semaphore. Late responses are ignored. This is an operational caveat of the canonical pattern, not a reason to use a full agent instance.

#### B2. Real watcher agent definition, raw LLM invocation (FR-17/FR-18)

Create `agents/watcher/` with `meta.json`, `soul.md`, `rule.md`, `workflow.md`, and `tools_note.md`. Follow `docs/agent-prompt-writing-guide.md` (critical note and NFR-22). The definition supplies identity, decision policy, response schema, and safety rules; it does not need tool permissions or team members because it never runs a ReAct loop.

Resolve the prompt through the normal registry/loader path:

```python
registry = get_registry()
watcher_meta = (
    registry.get_version(watcher_agent_id, watcher_version_tag)
    or registry.get_resolved(watcher_agent_id)
)
watcher_system_prompt, _ = load_and_cache_prompt(
    watcher_meta.id,
    watcher_meta.path,
    manager.prompt_cache,
    mcp_tool_names=[],
    version_tag=watcher_meta.version_tag,
)
```

Versioned lookup MUST use `get_version()` with fallback to `get_resolved()`, matching the project's critical-note convention and registry semantics (`daemon/registry.py:783-820`). `load_and_cache_prompt` composes the agent's prompt files and caches by agent/version (`daemon/loader.py:603-712`). Do not call `spawn_instance`, create a Task/JobItem, bind tools, inject skills, or enter the job queue. The watcher identity is real, but the execution mechanism is a raw, single model call.

Use the existing top-level `llm_model` field for model selection. A nested `watchover` config can carry `timeout_seconds`, `max_denials_per_turn`, `mirror_message_count`, and the (normally fixed) `failure_mode`. Because `AgentMetadata` currently has no typed `watchover` field (`daemon/registry.py:235-345`), add a `WatchoverAgentConfig` field to the registry model; otherwise extra metadata can be silently ignored and runtime defaults will win.

#### B3. Bifurcated failure handling (LD-2)

Behavior is **bifurcated** per LD-2 — infrastructure errors fail-open; judgment errors fail-closed:

| Failure class | Decision | Reason code | Counter effect |
|---------------|----------|-------------|----------------|
| Outer timeout (infra) | **Allow** | `watcher_timeout` | +0 (no count) |
| Provider/API exception (infra) | **Allow** | `watcher_infra_error` | +0 (no count) |
| Malformed response (judgment) | **Deny** | `watcher_invalid_response` | +1 |
| Prompt/policy missing while enabled (judgment) | **Deny** | `watchover_config_invalid` | +1 |
| Unsupported/malformed tool call (judgment) | **Deny** | `tool_call_invalid` | +1 |

Infrastructure fail-open events MUST emit a `watchover_event{status: "degraded", reason: "watcher_infra_error"}` SSE so the FE shows a degraded-safety warning. Three judgment-error denials in the same turn terminate exactly like three policy denials (NFR-4, AC-NFR-4.3). Rationale: uniform fail-closed creates a self-DoS cascade (provider outage → mass termination of all watched instances). Log structured fields only: `instance_id`, `turn_id`, `tool_name`, `decision`, `reason_code`, `deny_count`, `duration_ms`, `watcher_model`; do not log raw secret-bearing arguments by default (FR-29/NFR-24).

A configurable `fail_open` mode should not be shipped in phase 1 even though NFR-4 calls behavior configurable: it undermines the core safety claim and makes NFR-12 misleading. If configurability is mandatory, restrict it to an operator-only daemon config, never a watched-agent-visible setting, and display a prominent degraded-safety state in FE.

#### B4. Model selection (Gap #3)

Recommended resolution order:

1. `agents/watcher/meta.json` explicit `llm_model` (existing `AgentMetadata.llm_model` field at `daemon/registry.py:253`).
2. Watched instance's already-resolved model from the graph's `llm_config["model"]` (`daemon/graph.py:3290-3308`).
3. Global daemon default model.

Do not perform weighted `llm_models` selection per tool call; that field is designed to select once at instance creation and freeze the result (`daemon/registry.py:254-263`). If watcher `llm_models` is supported, resolve once when the watcher evaluator is built and cache the selected model. Validate against `config.llm.allowed_models` using the same path used by instance lifecycle model resolution (`daemon/services/instance_lifecycle.py:865-924`).

#### B5. Security invariants

- **NFR-10 — immutable watcher prompt/context:** The watcher system prompt is loaded from `agents/watcher/`, which is not exposed through watched-agent tools. `watchover_context` is written only by the authenticated toggle/compaction service. No dynamic-skill, inner-soul, DB, or filesystem tool receives a watchover mutation capability. Even if a watched DevOps agent can access broad filesystem/DB tools, its mutation attempt is itself a proposed tool call and is intercepted before execution.
- **Prompt injection defense:** Watched messages and tool arguments are untrusted data. Frame them as such; never concatenate them into the watcher system instructions without delimiters. Do not include the watched agent's system prompt as a peer `SystemMessage` because that could conflict with watcher policy.
- **NFR-11 — counter isolation:** `watchover_deny_count` and `watchover_turn_id` are private `SessionState` keys, never serialized into the watched agent's LLM prompt or exposed by tools. The agent only receives denial `ToolMessage`s.
- **NFR-12 — unbypassable:** The compiled graph has no `agent → tools` edge. Every `AIMessage.tool_calls` path passes through `watchover_check`. Add a static topology test and an integration test that replaces ToolNode with a spy and proves Deny never calls it.
- **Authorization:** The toggle endpoint is a control-plane endpoint only; no agent tool exposes it. FR-27 still requires a concrete session-ownership model (see Open Questions).

### C. State & Persistence Architecture

#### C1. instance_metadata JSONB schema

No watchover table or column is required. Persist these top-level keys in `Instance.instance_metadata` (`daemon/repositories/instance/models.py:64-67`):

```json
{
  "watchover_enabled": true,
  "watchover_context": "[Operator Requirement]...\n[Conversation Summary]...",
  "watchover_watcher_agent_id": "watcher",
  "watchover_active_at": "2026-08-05T20:19:54Z"
}
```

Recommended internal control-plane keys (not exposed to the watched agent):

```json
{
  "watchover_transition": {
    "operation": "enable|disable",
    "previous_enabled": false,
    "started_at": "..."
  },
  "watchover_pending_termination": {
    "reason": "watchover_terminated",
    "deny_count": 3,
    "turn_id": "...",
    "created_at": "..."
  }
}
```

`watchover_enabled` absent means false (FR-1). Cap `watchover_context` to a configured token/byte limit and keep the user's requirement in a separate immutable prefix when rebuilding summaries.

Use `InstanceRepository.set_metadata`/`delete_metadata`, not `update(instance_metadata=...)`; the latter is explicitly rejected because replacing the JSON blob can clobber concurrent writers (`daemon/repositories/instance/repository.py:682-713`). Because activation changes four related keys, add a `set_metadata_many(instance_id, mapping, delete_keys=...)` repository helper that updates all keys in one transaction/single SQL statement. Four independent `set_metadata` calls are individually atomic but are not atomic as a group and can expose a torn configuration.

#### C2. LangGraph state keys and turn reset

Extend `SessionState` (`daemon/graph.py:1992-2005`) with:

```python
watchover_deny_count: int = 0
watchover_turn_id: str | None = None
watchover_route: str | None = None
watchover_pending_termination: bool = False
watchover_pending_denials: list[dict] = []   # mixed-batch only
watchover_original_tool_calls: list[dict] = []  # mixed-batch only
```

These keys checkpoint automatically with the rest of `SessionState`; no DB schema migration is needed. `watchover_turn_id` must be a stable work/turn id, ideally the current Task `work_id`, not just `instance_id`. Today the graph config carries only `configurable.thread_id=instance_id` in both `ainvoke` and `astream` paths (`daemon/services/instance_messaging.py:906-915`, `2069-2075`). Thread the authoritative `work_id` as `configurable.turn_id` from message/task processing. On each gate:

- if `state.watchover_turn_id != config.turn_id`, set count to 0 before evaluating;
- increment once per Deny;
- when `agent_node` returns an LLM response with no tool calls, eagerly set count 0 and turn id `None` in its existing return dict (`daemon/graph.py:2897-2929`);
- deactivation also resets these keys while paused (AC-EC.5).

This provides both semantic reset at the no-tool turn boundary (FR-10) and recovery from stale checkpoint state if a crash occurs before the eager reset.

#### C3. SuspensionReason.WATCHOVER_SETUP — correction and recommendation

The research finding that `SuspensionReason` is a PostgreSQL enum requiring `ALTER TYPE` is **not accurate for the current code**. `SuspensionReason` is a Python `str, Enum` (`daemon/repositories/task/models.py:55-60`), while `Task.suspension_reason` is a nullable string column (`daemon/repositories/task/models.py:135-140`). The migration adds it as `TEXT` (`daemon/migrations/versions/20260801_000001_task_turn_handles.sql:35-45`), and the PostgreSQL ensure path adds `VARCHAR` (`daemon/manager.py:3756-3765`). There is no PostgreSQL enum type to alter.

`WATCHOVER_SETUP = "watchover_setup"` is also **not strictly required** for phase 1. `pause_instance_cascade` already suspends running turns with `SuspensionReason.PAUSED_EXTERNAL` (`daemon/services/instance_lifecycle.py:3236-3243`), and `resume_processing_job` selects paused/cancellable turns without needing a setup-specific reason (`daemon/manager.py:5504-5542`). Recommended MVP: reuse `PAUSED_EXTERNAL` and track setup state in `instance_metadata.watchover_transition`.

If operators require reason-specific telemetry, add the Python enum member and thread an optional `suspension_reason` parameter through `pause_instance_cascade` → `_pause_cascade_db_sync` → `SuspendTurn`. No SQL migration or `ALTER TYPE` is needed; tests are needed to verify `_VALID_SUSPENSION_REASONS` (derived from the Python enum in `daemon/services/turn_transitions.py:140`) accepts it.

#### C4. Crash recovery (FR-26/NFR-6)

On daemon restart:

1. Instance restore resolves metadata and rebuilds the graph (`daemon/services/instance_lifecycle.py:2310-2589`). The new restore wiring must pass `WatchoverSlot(manager)` exactly as spawn wiring does.
2. `WatchoverSlot` hydrates its cache from `instance_metadata.watchover_enabled/context/watcher_agent_id`. Because the gate is always present in every graph, no graph recompilation is required when the flag is ON.
3. LangGraph checkpointer restores `watchover_deny_count`, `watchover_turn_id`, and mixed-batch pending state with `SessionState`.
4. Startup recovery scans `watchover_transition`:
   - enable transition, `watchover_enabled=false` → rollback/clear transition and leave OFF (or retry context build if the product explicitly chooses resume-on-start);
   - enable transition, `watchover_enabled=true` + complete context → clear transition and resume with watchover ON;
   - disable transition, `watchover_enabled=true` → rollback to ON;
   - disable transition, `watchover_enabled=false` → clear transition and resume OFF.
5. Startup recovery scans `watchover_pending_termination` before admitting new work and calls the normal terminate cascade. A RAM-only deferred marker (like `_deferred_question_pause`) is insufficient for the crash window between graph END and the post-graph callback; the persistent metadata intent closes that gap.

### D. Activation/Deactivation Lifecycle Architecture

#### D1. Activation sequence

Expose a high-level `manager.activate_watchover(instance_id, requirement, watcher_agent_id="watcher")` implemented by a new `WatchoverService`; the router must not orchestrate low-level writes itself.

Exact manager/service sequence:

1. Validate instance exists and is non-terminal (`await manager.get_instance(instance_id)`).
2. Acquire a **per-instance** watchover transition lock (not global) and atomically set `watchover_transition={operation:"enable", previous_enabled:false, ...}`.
3. Request a deferred graph-boundary pause. If a graph is active, a pre-/post-tool router routes to a setup pause node, which sets a deferred marker; the post-graph completion path performs `await asyncio.shield(manager.pause_instance_cascade(instance_id))` using the existing pattern (`daemon/services/instance_messaging.py:976-991`, `3550-3565`). If no graph is active, call `await manager.pause_instance_cascade(instance_id)` directly.
4. Await a new `manager.wait_for_instance_quiescent(instance_id, timeout=...)` barrier. Existing pause cancels and pops the graph task but does not await its unwind (`daemon/services/instance_lifecycle.py:1864-1886`), so pause completion alone is not a quiescence guarantee.
5. Fetch the graph and checkpoint: `graph = await manager.get_instance(instance_id)`; `state = await graph.aget_state({"configurable":{"thread_id": instance_id}})`.
6. Build `CompactionContext` from `state.values['messages']`, system-prompt tokens, watched model, compaction config, and LLM config (`daemon/compaction.py:213-231`; existing construction at `daemon/services/instance_messaging.py:730-745`). Call `compactor.compact_state(context)` and extract summary; if `None`, serialize a bounded raw recent tail (AC-EC.7). Do not apply replacement messages to the watched graph.
7. Build immutable `watchover_context = operator requirement + summary/raw tail`.
8. Atomically persist the four public watchover fields in one metadata transaction, then update the manager cache. The DB commit happens before the cache flip, so an enabled cache never points to incomplete DB context.
9. Clear `watchover_transition`.
10. Resume the tree: `resume_result = await manager.resume_instance_cascade(instance_id)` (`daemon/manager.py:5365-5380`). For every `rid` in `resumed_ids`, call `await manager.resume_processing_job(rid, message="Watchover enabled" if rid == target else "resume", silent=(rid != target))`, mirroring the existing router fan-out (`daemon/routers/instances.py:584-600`). If the target's paused turn should not see a control-plane message, use `silent=True` for the target too; the persistent gate does not depend on an injected message.
11. Emit `watchover_event{status:"enabled"}` after the metadata commit and before/after resume as product UX dictates (FR-24).

Note: `pause_instance_cascade` and `resume_instance_cascade` are tree-scoped: they find the tree root and traverse all nodes (`daemon/services/instance_lifecycle.py:1809-1819`, `2052-2062`). The pause window therefore affects siblings/children operationally even though only the selected target's watchover metadata changes. This is a real side effect of reusing the cascade and should be documented in FE.

#### D2. Deactivation sequence

`manager.deactivate_watchover(instance_id)` is symmetric:

1. Acquire the target's transition lock; persist `watchover_transition={operation:"disable", previous_enabled:true}`.
2. Deferred pause → `await asyncio.shield(manager.pause_instance_cascade(instance_id))` → await quiescence.
3. Atomically set `watchover_enabled=false` and delete/clear `watchover_context`, `watchover_watcher_agent_id`, `watchover_active_at`; reset watchover LangGraph state while paused.
4. Update the manager cache only after the DB commit; clear transition marker.
5. `await manager.resume_instance_cascade(instance_id)` and fan out `resume_processing_job` exactly as activation does.
6. Emit `watchover_event{status:"disabled"}`.

If any context/write step fails, keep the instance paused and preserve the prior complete ON/OFF configuration; do not resume in a half-transition state.

#### D3. Atomicity and mid-flight race (NFR-5/NFR-15)

A DB transaction cannot span pause, an external LLM compaction call, and resume. Atomicity is therefore a state-machine property, not a single SQL transaction:

`OFF → ENABLING (paused) → ON → RUNNING` and `ON → DISABLING (paused) → OFF → RUNNING`.

`watchover_transition` is the durable journal. The instance remains PAUSED during `ENABLING`/`DISABLING`; no work is admitted. The four final metadata fields are written atomically as one unit. Crash recovery rolls forward/back based on the journal + final flag.

#### D4. In-flight tool calls (FR-28 / AC-EC.6)

The current `pause_instance_cascade` cancels the active graph task immediately and does not await its unwind (`daemon/services/instance_lifecycle.py:1864-1886`). Cancellation cannot reliably stop a synchronous tool already running in a worker thread, and arbitrary tool side effects are not exactly-once. Therefore the current method by itself does **not** prove NFR-15.

Recommended cutoff semantics:

- A tool call that entered `ToolNode` before the activation request is a **pre-activation call**. Let it finish and checkpoint its result; then pause at the post-tools boundary (the same boundary used by `create_post_tools_router`).
- A tool call proposed after the activation request but before the pause commit is held by a pre-tool setup gate and does not enter ToolNode.
- Once PAUSED, Task claim gates already prevent new processing; calls are preserved in checkpoint and resumed after the metadata flip.
- If the in-flight tool does not complete within the activation timeout, fail activation and keep the instance paused. Do not claim it was safely intercepted.

This requires a deferred "pause at graph boundary" request rather than only hard-cancelling the graph task. Without that addition, AC-EC.6 can only guarantee "the old call may complete" and cannot guarantee no duplicate execution if cancellation occurs after side effects but before ToolMessage checkpointing. Exactly-once arbitrary tools would require a tool-execution idempotency ledger, which is outside this feature.

### E. Termination Architecture (3 Strikes)

#### E1. Option A vs Option B

| Criterion | Option A: direct `await manager.terminate_instance()` inside node | Option B: deferred marker + post-graph cascade |
|-----------|---------------------------------------------------------------|------------------------------------------------|
| Simplicity | Fewer moving parts | Requires marker + two post-graph call sites |
| Self-cancel risk | High — terminate pops/cancels `_graph_tasks[instance_id]` (`daemon/services/instance_lifecycle.py:1254-1286`) while the current node is that task | Low — graph routes END first; cascade occurs outside the node |
| DB torn-state risk | High — same class of bug documented for direct pause (`daemon/graph.py:3035-3053`) | Low — follows the proven C2 deferred-cascade pattern |
| Crash window | Direct call starts immediately, but may be interrupted by self-cancel | Persistent pending marker survives until startup recovery consumes it |
| Maintainability | Violates C-6 and existing critical-note pattern | Matches `question_pause_node` + post-graph callback |

**Recommendation: Option B.** `watchover_terminate_node` records `manager.set_deferred_watchover_termination(instance_id, reason, count, turn_id)` and persists `instance_metadata.watchover_pending_termination`, emits the final denial/termination-pending event, and returns. It routes to END. Both `ainvoke` and `astream` post-graph `finally` paths must peek the marker, call `await asyncio.shield(manager.terminate_instance(instance_id, terminal_reason="watchover_terminated"))`, and pop/clear the marker in `finally`, outside any `_graph_tasks` identity guard. Watchover termination takes precedence over deferred question pause; clear any stale question-pause marker instead of pausing a terminating instance.

#### E2. terminal_reason and AdmissionState

No new AdmissionState is needed: JobItems move from `QUEUED|ACTIVE → DONE`, and `terminal_reason` distinguishes why (`daemon/repositories/job_queue/models.py:40-48`, `299-305`). Extend:

- `InstanceManager.terminate_instance(instance_id, terminal_reason="aborted")`;
- `InstanceLifecycleService.terminate_instance(..., terminal_reason=...)`;
- `_terminate_instance_db_sync(..., terminal_reason=...)` so the existing hard-coded `"aborted"` write at `daemon/services/instance_lifecycle.py:2931-2948` uses `"watchover_terminated"` for the watched root.

Do not pass the watchover reason into recursive child termination by default; children terminated only because their parent terminated should retain `"aborted"`, while the watched root gets `"watchover_terminated"` (`terminate_instance` recursively calls children first at `daemon/services/instance_lifecycle.py:1186-1224`). Add `"watchover_terminated": "cancelled"` to `_STATUS_CANONICAL_MAP`; otherwise `canonicalize_status()` returns the unknown string verbatim and the unified work API leaks a non-canonical status (`daemon/services/work_status.py:102-156`).

The research statement that manager termination hard-deletes the instance is incorrect in current code. `manager.terminate_instance` delegates to the soft terminate lifecycle (`daemon/manager.py:5303-5318`); `hard_delete_instance` is a separate destructive API (`daemon/manager.py:5320-5346`). Watchover must use soft termination, preserving the instance row/audit context.

#### E3. SSE/FE surfacing (FR-23)

Add a dedicated event payload:

```json
{
  "event_type": "watchover_event",
  "status": "terminated",
  "instance_id": "...",
  "terminal_reason": "watchover_terminated",
  "deny_count": 3,
  "turn_id": "...",
  "reason": "..."
}
```

Also extend `stream_status_change` with optional `terminal_reason`; the existing payload already emits `status="terminated"` post-commit (`daemon/services/instance_lifecycle.py:1399-1408`; event shape at `daemon/services/live_event_hub.py:220-253`). The FE can update instance status using the standard event and render the richer watchover chat message using `watchover_event`.

For initial load/reconnect, add an explicit `watchover_enabled: bool` field to backend `InstanceInfo` responses and frontend `InstanceInfo`; current instance endpoints selectively map metadata such as `model_override` but do not expose arbitrary metadata (`daemon/routers/instances.py:350-372`, `407-430`; `frontend/src/app/models/index.ts:4-32`). DB/API state—not localStorage—must be authoritative; localStorage may remember only presentation preferences. Activation/deactivation responses should return the committed enabled state and transition outcome so the button cannot display ON before the DB commit.

Important existing ordering issue: `terminate_instance` currently calls `live_hub.cleanup_instance(instance_id)` before its post-commit `stream_status_change` (`daemon/services/instance_lifecycle.py:1289-1290` vs `1399-1408`), and `cleanup_instance` removes all queues (`daemon/services/live_event_hub.py:432-440`). That can drop the termination event. For FR-23 reliability, reorder cleanup after post-commit status/watchover events, or route termination through a persistent/global notification surface. Emit after DB commit so FE never sees a terminal event while DB still says running.

### F. Edge Case Analysis

#### F1. Compaction during active watchover (FR-25/NFR-7)

There are two compaction paths that must notify WatchoverService:

1. pre-turn `_maybe_compact_context` in `instance_messaging.py` (`690-769`);
2. reactive context-overflow compaction inside `agent_node` (`daemon/graph.py:2782-2818`).

After either path successfully applies `CompactionResult.replacement_messages`, call `watchover_service.refresh_context_after_compaction(instance_id, result, current_state)` if the instance is watched. Reuse the summary already present in the replacement messages; do not make a second summary LLM call. Preserve the original operator requirement verbatim and replace only the conversation-summary portion. Atomically write the refreshed `watchover_context`, then update the slot cache. If refresh fails, retain the prior complete context, log the failure, and keep interception enabled/fail-closed; NFR-7 requires refresh by the next tool call, so the next gate may retry once before evaluation.

#### F2. Watchover + LoopBreaker (Gap #15)

Loop detection runs inside `agent_node` before each watched-agent LLM call (`daemon/graph.py:2691-2717`). A denial sends a `ToolMessage` back to `agent`, so repeated denied calls can look like a tool loop. The watcher counter must **not reset** when LoopRepairer rewrites messages: repair updates only `messages` via `graph.aupdate_state` (`daemon/graph.py:1123-1160`), while watchover counter keys remain in SessionState.

Recommended precedence:

- mark denial ToolMessages with `additional_kwargs.watchover_denial=true`;
- teach `LoopDetector.scan` to exclude call/result pairs whose result has that marker, so loop repair does not remove policy evidence or nudge the agent around the 3-strike policy;
- watcher denial/termination wins over loop repair; the third denied call terminates before another agent-node loop-repair pass;
- ordinary allowed tool loops remain under LoopBreaker control.

Pause currently clears manager `_loop_breaker_state` (`daemon/services/instance_lifecycle.py:1874-1883`), but it must not clear checkpointed watchover deny state unless deactivation explicitly resets it.

#### F3. Child instances / cascade (Gap #13)

Recommend **independent per-instance watchover; no automatic inheritance in phase 1**. This matches FR-2/FR-16 and avoids applying a DevOps-specific context to an explorer/developer child with different responsibilities. Activation/deactivation still pauses/resumes the whole tree because the existing lifecycle methods are tree-scoped, but metadata is changed only for the selected instance.

Trade-off: a watched parent could delegate an unsafe operation to an unwatched child. Phase-1 mitigation is to treat spawn/send/delegation tools like every other candidate tool call; the watcher can deny delegation whose content violates the policy. For stronger transitive safety, add a future `watchover_inherit_to_children` policy that snapshots parent policy into new child metadata at spawn time, with an independent counter per child. Do not silently infer inheritance from `parent_id` in the gate; that creates hidden coupling and breaks per-instance FE state.

Termination is already a cascade: terminating a watched parent terminates descendants first (`daemon/services/instance_lifecycle.py:1186-1224`). Only the watched root should receive `terminal_reason="watchover_terminated"`; descendants use `"aborted"` unless independently watched and independently triggered.

#### F4. Concurrent instances (NFR-13/NFR-14)

Isolation guarantees:

- enabled/context fields are stored on each instance row;
- denial counter/turn id are keyed by LangGraph `thread_id == instance_id` and checkpointed separately;
- manager cache and transition lock are keyed by instance id, mirroring `_question_pause_requested` and `_deferred_question_pause` (`daemon/manager.py:722-739`);
- watcher LLM calls share only a bounded semaphore/model client, never mutable decision state;
- no global activation lock is held, so 10 independent activations may proceed concurrently; DB writes remain row-scoped.

One caveat is tree-scoped pause/resume: concurrent toggles on two instances in the same tree can race/cascade over each other. Serialize transitions per **tree root** for lifecycle operations while keeping policy state per instance. Instances in different trees remain fully concurrent.

#### F5. Additional failure edges

- **Fresh/empty history:** compaction returns `None`; context = operator requirement + bounded raw tail (possibly empty). Activation may still succeed (AC-EC.7).
- **Watcher definition missing:** reject activation (preferred) so a user cannot enable a broken control; if it disappears after activation, runtime fails closed.
- **Malformed parallel call IDs:** deny malformed calls and do not execute the batch; protocol-safe ToolMessage creation requires unique call ids.
- **Deactivation with count > 0:** pause, atomically disable, clear graph-state counter/pending batch state, then resume (AC-EC.5).
- **No active SSE connection:** event is dropped by LiveEventHub (`daemon/services/live_event_hub.py:175-196`); DB state and ToolMessages remain the recovery source, and FE must query instance watchover state on reconnect.
- **Multiple daemon processes:** metadata is the authority. If more than one process can own the same instance graph, cache invalidation needs a revision/event; otherwise an in-memory cache can be stale. ExecutionGate ownership should normally keep one active graph owner.

## Trade-offs

### G. Trade-offs & Recommendations

#### Decision Summary

| Decision | Recommendation | Complexity | Scalability | Maintainability | Risk | Cost |
|----------|----------------|------------|-------------|-----------------|------|------|
| Pre-tool interception | Explicit `watchover_check` node + `WatchoverSlot`; no `agent → tools` bypass | Medium | Per-instance | High (matches graph patterns) | Low after topology tests | One cheap node when OFF; watcher calls when ON |
| Parallel calls | Evaluate per call; execute allowed subset; inject denial results; termination cancels whole batch | Medium-high | Linear in candidate calls; bounded parallelism | Medium | Medium (message protocol/restart complexity) | One watcher LLM request per candidate call |
| Watcher execution | Real `agents/watcher/` prompt, raw unbound LLM, no spawn/job | Low-medium | Stateless + bounded semaphore | High | Low | Variable LLM token/roundtrip cost; no new infra |
| Watcher failure | Fail closed (Deny), timeout/error counts toward 3 strikes | Low | Constant-time fallback | High | Lowest safety risk; higher false-positive risk | Bounded by timeout |
| Model | watcher `llm_model` → watched model → global default | Low | Cache once per graph/evaluator | High | Medium if fallback model is weak | Configurable model cost |
| Persistent policy state | Existing instance_metadata JSONB + atomic multi-key patch | Medium | Row-scoped | High | Low; no schema migration | JSONB storage only |
| Per-turn count | LangGraph state + stable Task work_id + eager no-tool reset | Medium | Per-thread checkpoint | High | Low after retry/resume tests | Negligible |
| Setup suspension | Reuse `PAUSED_EXTERNAL`; durable transition journal; deferred quiescent pause | Medium-high | Tree-scoped lifecycle may serialize same-tree toggles | Medium | Low only with boundary/quiescence barrier | One compaction call per toggle |
| Three-strike termination | Deferred persistent marker → normal soft terminate with `terminal_reason` | Medium | Existing cascade | High | Lowest torn-state risk | No new infra |
| Children | Independent by default; no implicit inheritance | Low | Per-instance | High | Medium delegation-bypass risk | No added watcher calls on children |
| Loop breaker | Denial evidence excluded from loop repair; counter never reset by repair | Low-medium | Per-instance | Medium-high | Low | Negligible |

### Alternatives Considered

1. **Option A: New `watchover_check` routing node between `agent` and `tools`** — insert a new node + conditional edges mirroring `create_post_tools_router`. The `agent`'s `should_continue` already returns `"tools"` when the LLM made tool calls; we re-route that `"tools"` destination to `watchover_check`, which then routes to `{tools, agent, watchover_terminate_node}`.
2. **Option B: Wrap `should_continue` (language-check style)** — wrap `should_continue` so that when it would return `"tools"`, it instead returns `"watchover_check"`. This is the `create_should_continue` pattern (`daemon/graph.py:2239-2259`).
3. **Option C: Wrap/replace `ToolNode` directly** — subclass `ToolNode` to intercept each tool call before execution.
4. **Option D: Generic middleware framework** — introduce a pluggable middleware layer that wraps every node.

### Comparison

| Criterion | Option A (new node) | Option B (wrap should_continue) | Option C (wrap ToolNode) | Option D (middleware) |
|-----------|---------------------|---------------------------------|--------------------------|-----------------------|
| Performance (OFF path) | One extra router eval per tool-bearing turn; no LLM call | Same as A | Same (router still runs) | Potentially higher (framework overhead) | 
| Performance (ON path) | One watcher LLM call per candidate tool call; clean separation | Same LLM cost; routing logic mixed into should_continue | Same LLM cost; ToolNode subclass couples evaluation to execution | Same LLM cost; framework dispatch overhead |
| Complexity | Medium — explicit pre-tool node/router plus deny-whole-batch (LD-1; mixed-batch finalization eliminated) | Medium — wrapper composes with `create_should_continue` (language-check) and must preserve all branches (`tools`/`agent`/`nudge`/`END`/`end_candidate`) | Medium-high — ToolNode internals (parallel execution, error handling) must be reproduced or preserved | High — new framework, new abstractions, touches every node |
| Maintainability | High — follows C-5/NFR-19 explicitly; one new slot class `WatchoverSlot` | Medium — should_continue accumulates concerns (language check + watchover) | Low — coupling evaluation to execution breaks the single-responsibility of ToolNode | Low — generic middleware is explicitly rejected by C-1/C-5 |
| Team skills | High — codebase already has `create_post_tools_router` and `create_question_pause_node` as templates | High — `create_should_continue` is an existing pattern | Medium — requires LangChain ToolNode internals knowledge | Low — no existing middleware framework in the project |
| Time-to-implement | Medium — new node, router, slot, evaluator, SSE, endpoint, FE button | Medium-low — less new wiring, but more careful composition with language check | Medium-high — ToolNode subclassing is fragile across LangChain versions | High — framework design + migration |
| Cost (infra / usage) | No new infra; variable watcher LLM cost per candidate call | Same as A | Same as A | Same LLM usage plus framework maintenance cost |
| NFR-12 (unbypassable) | Strong — `agent → watchover_check → tools` is the ONLY path to ToolNode; no bypass | Strong — same topology | Weaker — a future code path could call ToolNode directly | Weaker — middleware can be misconfigured/skipped |
| NFR-19 (reuses slot pattern) | Strong — `WatchoverSlot(manager)` mirrors existing slots | Medium — wraps router, not a slot | Weak — not a slot | Weak — new framework |

### Recommendation

**Pick: Option A — new `watchover_check` routing node between `agent` and `tools`.**

**Reasoning:** Option A is the closest structural match to the existing `create_post_tools_router` + `question_pause_node` pattern, which the codebase already uses for the analogous "intercept after tools and branch" need. It keeps the watchover concern in its own node and its own slot (`WatchoverSlot`), satisfying C-5 and NFR-19 (explicit slot threading, no new middleware). It makes NFR-12 (unbypassable) trivially true: the graph topology `agent → watchover_check → tools` is the only path to `ToolNode`, so a graph-topology test can assert no bypass. Option B is viable but mixes watchover routing into `should_continue`, which already composes with the language-check wrapper; the composition is correct but harder to reason about and makes the "is watchover active?" gate live inside a router that runs on every turn even when watchover is OFF. Option C couples evaluation to execution and is fragile across LangChain versions. Option D is explicitly rejected by C-1 and C-5.

The graph implementation remains agent-agnostic: it serializes generic tool name/arguments/id and never branches on `agent_id`. DevOps-first behavior belongs in activation availability, tests, and watcher policy/context—not in the interception node—so AC-EC.8 remains achievable for non-DevOps agents.

**Assumptions:** LangGraph's `add_conditional_edges` supports a node whose router returns one of `{tools, agent, watchover_terminate_node}` (it does — this is how `post_tools_router` returns `{agent, question_pause_node}`). The `watchover_check` node can return state updates (`{'messages': [...]}`) that persist via the `add_messages` reducer (it can — `question_pause_node` returns `{}` and `agent_node` returns `{'messages': [...]}`). The watcher LLM call is short enough (≤ ~10s with timeout) to not violate NFR-1.

**Reversibility:** High. The new node, router, and slot are additive. Removing watchover reverts to the unconditional `agent → tools` edge. No existing node's contract changes.

## Scalability

### Growth Assumptions

- Watched instances: 1–10 concurrent per daemon (DevOps-first per C-3; watchover is opt-in per instance).
- Tool calls per watched turn: 1–20 (typical DevOps workflow).
- Watcher LLM calls: equal to individual candidate tool calls from watched instances; a batch of three tool calls produces three independent evaluations (possibly concurrent behind a bounded semaphore).
- watchover_context size: bounded by compaction summary (~2–4 KB) + user requirement (~1 KB).

### Load Profile Projection

These are planning assumptions, not measured current capacity:

| Scenario | Concurrent watched instances | Candidate tool calls / instance / second | Watcher request rate | Expected constraint |
|----------|------------------------------|------------------------------------------|----------------------|---------------------|
| DevOps-first typical | 1–5 | 0.05–0.2 | 0.05–1 RPS | User-visible latency, not throughput |
| Phase-1 target | 10 | 0.2 | 2 RPS | Watcher-model p95 + provider quota |
| Stress | 50 | 0.5 | 25 RPS | Thread-pool/semaphore saturation and provider rate limits |

Cost and throughput scale linearly: `watcher_requests = Σ(candidate_tool_calls)`. For a batch, elapsed gate time is approximately the maximum watcher-call latency when calls are evaluated concurrently, while token/API cost is the sum of all calls. Capacity headroom is currently unknown; implementation must instrument request count, duration, timeout count, denial rate, and queue wait before setting a production concurrency limit.

### Current Bottlenecks

| # | Bottleneck | Threshold | File:Line | Impact |
|---|------------|-----------|-----------|--------|
| 1 | Watcher LLM latency/cost added for every candidate tool call of a watched instance | ≤ 2× LLM roundtrip + 1s overhead target (NFR-1) | `daemon/graph.py:1320-1410` (pattern) | User-visible latency; batches fan out one evaluation per call, mitigated by bounded concurrency + strict timeout |
| 2 | Per-instance `_watchover_state` in-memory dict growth | one entry per watched instance; cleared on terminate | `daemon/manager.py` (new, mirroring `_question_pause_requested` at `728`) | Bounded by concurrent watched instances; cleanup on terminate (mirrors `2642`) |
| 3 | Compaction during activation | ≤ 30s typical, ≤ 60s worst (NFR-2) | `daemon/compaction.py:596-781` | Activation latency dominated by compaction; acceptable per NFR-2 |
| 4 | Watcher prompt cache miss on first interception | one-time disk read per daemon | `daemon/loader.py:688-712` | Negligible after first hit (PromptCache keyed by agent_id) |

### Scaling Characteristics

- **Vertical vs horizontal:** Policy state is DB-backed, but the OFF-path optimization uses a process-local cache. Horizontal safety therefore requires single active graph ownership per instance (the normal ExecutionGate model) or explicit cache invalidation/revision propagation when another process toggles the row; JSONB persistence alone does not invalidate another process's cache.
- **Stateless vs stateful:** The denial counter is stateful per turn (LangGraph state, auto-checkpointed). The watchover_enabled flag is stateful per instance (DB). The watcher prompt is stateless (cached).
- **Sync vs async:** Watcher LLM call is async via `asyncio.to_thread` (non-blocking). Pause/resume cascades are async with `asyncio.shield`. SSE is fire-and-forget.
- **Scaling cliffs:** If watchover is enabled on a high-throughput instance with many tool calls per turn, the per-tool-call watcher LLM cost becomes the dominant cost. The `watchover.max_denials_per_turn` cap (default 3) bounds the worst case to 3 denials + 1 termination. No architecture change is needed up to ~10 concurrent watched instances; beyond that, a cheaper watcher model (Gap #3) or a rule-based pre-filter (deferred per Out of Scope) would be needed.

## Technical Debt

### Items Affecting This Analysis

| # | Debt Item | Impact on Recommendation | Severity | File:Line |
|---|-----------|--------------------------|----------|-----------|
| 1 | The two current agent conditional-edge maps route `"tools"` directly to ToolNode | Both maps must be changed and tested; missing either is an NFR-12 bypass | High | `daemon/graph.py:3350-3375` |
| 2 | Pause cancels/pops the graph task but does not await tool/graph quiescence | Existing pause alone cannot prove NFR-15/FR-28; add deferred boundary pause + quiescence barrier | High | `daemon/services/instance_lifecycle.py:1864-1886` |
| 3 | Termination helper hard-codes `terminal_reason='aborted'` | Thread an explicit reason through manager/lifecycle/DB helper; preserve `aborted` for descendant cascade | High | `daemon/services/instance_lifecycle.py:2918-2948` |
| 4 | Work-status canonicalization does not know `watchover_terminated` | Without a mapping, unified work status leaks the unknown reason instead of canonical `cancelled` | High | `daemon/services/work_status.py:102-156` |
| 5 | Terminate cleanup removes LiveEventHub connections before post-commit status SSE | FR-23 termination event may be dropped; reorder cleanup or use a persistent/global channel | High | `daemon/services/instance_lifecycle.py:1289-1290`, `1399-1408`; `daemon/services/live_event_hub.py:432-440` |
| 6 | `compact_state` is threshold-driven and returns `None` for short histories; there is no public "summarize snapshot" method | Activation needs a raw-tail fallback or a small public summarization adapter; do not assume `compact()` exists | Medium | `daemon/compaction.py:596-653` |
| 7 | `set_metadata` writes one JSON key at a time | Four independent calls can expose partial watchover config; add an atomic multi-key patch helper | Medium | `daemon/repositories/instance/repository.py:782-845` |
| 8 | Deferred question marker is RAM-only | Copying it exactly for termination leaves a crash window; persist termination intent in metadata | Medium | `daemon/manager.py:730-739`, `2388-2461` |
| 9 | No existing session-ownership authorization on instance endpoints | FR-27 cannot be implemented as "reuse existing ownership check" until ownership is defined | High | `daemon/routers/instances.py:526-608` |
| 10 | Spawn and restore construct graphs in separate call sites | `WatchoverSlot` must be threaded through both or restart silently bypasses watchover | Medium | `daemon/services/instance_lifecycle.py:956-988`, `2556-2586` |
| 11 | ~~Mixed parallel batches require filtered AIMessage + post-tools denial finalization~~ — **ELIMINATED by LD-1 (deny-whole-batch).** No message replacement, no finalization node, no checkpoint/restart surface. | ~~Highest implementation/test complexity~~ → eliminated | ~~Medium~~ → N/A | `daemon/graph.py:3338-3405` |
| 12 | Instance API/frontend model do not expose watchover state | FE cannot reliably restore toggle state from DB without explicit schema fields; localStorage would become stale | Medium | `daemon/routers/instances.py:350-430`; `frontend/src/app/models/index.ts:4-32` |

### Items NOT Affecting This Analysis

- **No PostgreSQL enum migration is needed for `WATCHOVER_SETUP`.** `suspension_reason` is TEXT/VARCHAR, not a native enum (`daemon/migrations/versions/20260801_000001_task_turn_handles.sql:35-45`; `daemon/manager.py:3756-3765`).
- **No new watchover column/table is needed.** Existing JSONB metadata is sufficient (`daemon/repositories/instance/models.py:64-67`).
- Turn-Reconciler Migration Phase 4b/4c deferred call-site migration (critical note) does not block watchover because phase 1 can reuse the existing pause/resume transitions.
- OpenCode's separate SQLite registry is unrelated.

### Recommended Paydown

1. **Before implementation:** define owner authorization and the exact operator-requirement source (Open Questions 1–2).
2. **Before declaring NFR-15 met:** implement and race-test deferred boundary pause + quiescence; document that already-started arbitrary tools are not exactly-once without idempotency support.
3. **Before termination E2E:** parameterize terminal reason, add `watchover_terminated → cancelled` canonicalization, and fix SSE cleanup ordering.
4. **Alongside persistence work:** add atomic multi-key metadata patch + durable transition/termination intent.
5. **Alongside graph work:** wire spawn and restore, add topology/no-bypass tests, and add mixed-batch checkpoint/restart tests.
6. **Before production rollout:** benchmark OFF p95 regression (≤5%), watcher p95 latency, timeout thread leakage, and provider rate-limit behavior.

## Open Questions

1. **Gap #1 (watchover_context source):** ✅ **RESOLVED** — `watchover_context = user-provided requirement (at activation time) + compaction summary of current instance state`. Confirmed by leader decision. Phase 3 T3.4 implements this combination.
2. **FR-27 authorization:** ✅ **RESOLVED** — **Phase 1: descoped to "manager-internal only"** (option c). No cross-session authorization in phase 1; the project has no instance-ownership primitive and building one is out of scope. Full session-owner 403 rejection deferred to phase 2 (TD-9). See `phase3-plan.md` T3.7 descope note.
3. **Gap #13 (child instances):** ✅ **RESOLVED (AD-7):** child instances do NOT inherit watchover — independent per-instance activation in phase 1.
4. **Gap #16 (sensitive reads):** ✅ **RESOLVED (Decision #8, phase1 T1.2):** the watcher blocks critical-path reads (e.g., `cat /etc/shadow`, secrets). Implemented in `agents/watcher/soul.md`.
5. **Watcher model default (Gap #3):** ✅ **RESOLVED (phase1 T1.1):** fallback chain = watcher `watchover.llm_model` → watched instance's resolved model → global default. Configurable via `agents/watcher/meta.json`.

## References

- `daemon/graph.py:3056-3097` — `create_post_tools_router` (closest existing interception pattern)
- `daemon/graph.py:3100-3200` — `create_question_pause_node` (deferred-cascade pattern)
- `daemon/graph.py:2239-2259` — `create_should_continue` (wrapper alternative)
- `daemon/graph.py:3203-3416` — `build_instance_graph` (wiring site)
- `daemon/graph.py:1320-1410` — `LoopRepairer._summarize_loop` (lightweight LLM call pattern)
- `daemon/graph.py:1992-2005` — `SessionState` (state schema extension point)
- `daemon/graph.py:2897-2929` — `agent_node` return + watchover reset insertion point
- `daemon/compaction.py:596-781` — `ContextCompactor.compact_state` (context build)
- `daemon/compaction.py:891-894` — summary `SystemMessage` shape
- `daemon/compaction.py:968-1010` — `_call_summarization_llm` (LLM call pattern)
- `daemon/manager.py:5303-5318` — `terminate_instance`
- `daemon/manager.py:5348-5380` — `pause_instance_cascade` / `resume_instance_cascade`
- `daemon/manager.py:5382-5575` — `resume_processing_job`
- `daemon/manager.py:2316-2461` — deferred-question-pause marker API (template for deferred terminate)
- `daemon/manager.py:2628-2652` — `_cleanup_instance_state` (cleanup template)
- `daemon/services/instance_lifecycle.py:1138-1602` — `terminate_instance` (single-transaction cascade)
- `daemon/services/instance_lifecycle.py:1785-2026` — `pause_instance_cascade` / `resume_instance_cascade`
- `daemon/services/instance_lifecycle.py:2716-2960` — `_terminate_instance_db_sync`
- `daemon/services/instance_lifecycle.py:3156-3281` — `_pause_cascade_db_sync`
- `daemon/services/instance_lifecycle.py:3545-3742` — `_resume_cascade_db_sync`
- `daemon/services/instance_lifecycle.py:956-988` — graph build call site (spawn)
- `daemon/services/instance_lifecycle.py:2556-2586` — graph build call site (restore)
- `daemon/services/instance_messaging.py:690-769` — `_maybe_compact_context` (compaction call template)
- `daemon/services/instance_messaging.py:976-991`, `3550-3565` — deferred-cascade post-graph completion path
- `daemon/repositories/instance/models.py:64-67` — `instance_metadata` JSONB column
- `daemon/repositories/instance/repository.py:782-845` — `set_metadata` / `delete_metadata`
- `daemon/repositories/task/models.py:55-60` — `SuspensionReason` enum
- `daemon/repositories/job_queue/models.py:40-137` — `AdmissionState` + `terminal_reason`
- `daemon/services/work_status.py:66-156` — `_STATUS_CANONICAL_MAP` + `canonicalize_status`
- `daemon/services/live_event_hub.py:150-253` — SSE streaming API
- `daemon/loader.py:603-712` — `load_and_cache_prompt`
- `daemon/registry.py:235-345` — `AgentMetadata` (extension point for `watchover.*` config)
- `daemon/routers/instances.py:526-608` — pause/resume endpoint pattern (toggle endpoint template)
- `frontend/src/app/models/index.ts:4-32`, `147-163` — Instance/SSE model extension points
- `daemon/migrations/versions/20260801_000001_task_turn_handles.sql:35-45` — suspension_reason is TEXT, not PostgreSQL enum
- `.agents/shared/planning/watchover/requirements.md` — full requirements + acceptance criteria
- Critical notes: C2 DB Torn State Fix (deferred pause pattern), Report-Lane Decoupling (deferred cascade), Phase D enqueued_at column bug (`_ensure_postgres_columns` for new columns)
gres_columns` for new columns)
