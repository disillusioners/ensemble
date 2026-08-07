# Phase 2: Watcher Invocation & Decision Logic

## Objective

Replace the Phase 1 `create_watchover_check_node()` stub with the real watcher
logic: assemble the watcher prompt (system + watchover_context + recent messages
+ tool call), make a lightweight LLM call (LoopRepairer pattern), parse the
Allow/Deny verdict, inject a ToolMessage on Deny so the instance sees why it was
blocked, and trigger the deferred 3-strikes termination when the denial count
reaches 3.

## Files to Modify

| # | Path | What Changes |
|---|------|--------------|
| M2.1 | `daemon/graph.py` (`create_watchover_check_node`, from Phase 1 T1.7) | Replace stub with full implementation: prompt assembly, LLM call, verdict parsing, denial-counter increment, ToolMessage injection, routing decision. |
| M2.2 | `daemon/graph.py` (near `LoopRepairer`, `:1024-1174`) | Add `WatchoverEvaluator` helper class (or functions) encapsulating the LLM call + timeout + verdict parsing. **Reuse callout:** mirrors `LoopRepairer.repair()` (`graph.py:1024-1174`) — `asyncio.to_thread` for the sync LLM call, `asyncio.wait_for` timeout guard, return original on error. |
| M2.3 | `daemon/graph.py` (`create_watchover_check_node` routing) | On Deny: increment `watchover_denial_count` in state; if count ≥ 3, route to `watchover_terminate_node`; else inject `ToolMessage` with denial reason and route back to `agent`. |
| M2.4 | `daemon/graph.py` (`should_end_watchover` router) | Finalize the router to read the node's return value/state mutation and select `{tools, agent, watchover_terminate_node}`. |

## Files to Create

| # | Path | Purpose |
|---|------|---------|
| C2.1 | `test/test_watchover_decision.py` | Unit tests for the watcher decision logic: Allow path, Deny path, fail-closed on LLM error, 3-strike termination trigger, ToolMessage injection content. |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| T2.1 | Implement `WatchoverEvaluator` helper in `graph.py`: `__init__(llm_config, watcher_model, timeout)` + `async evaluate(context, messages, tool_call) -> WatcherVerdict`. Verdict is a dataclass `{verdict: "allow"\|"deny", reason: str}`. **Reuses `LoopRepairer.repair()` pattern (`graph.py:1024-1174`): `asyncio.to_thread` + `asyncio.wait_for`.** | Phase 1 (T1.7 node exists) | Helper constructs the LLM call from config; returns a verdict; times out gracefully via `asyncio.wait_for`. Unit test with a mock LLM. |
| T2.2 | Implement watcher prompt assembly inside `WatchoverEvaluator`: system prompt = `agents/watcher/soul.md` content; user message = JSON `{context: <watchover_context>, tool_call: {name, args}, recent_messages: [...]}`. The `watchover_context` is read from `instance_metadata` (set in Phase 3). | T2.1 | Prompt assembly produces the correct message structure; unit test verifies the serialized prompt contains context + tool call + watcher soul. |
| T2.3 | Implement **bifurcated failure handling** (AD-6, per LD-2): TWO error classes. **Judgment errors** (malformed response, unparseable verdict, empty reason on Deny, tool-call validation, config invalid) → fail-closed → Deny + increment counter +1. **Infrastructure errors** (LLM timeout, provider 5xx, network exception) → fail-OPEN → Allow tool call + log + emit `watchover_event{status: "degraded", reason: "watcher_infra_error"}` SSE (no counter increment). Rationale: a provider outage must not mass-terminate all watched instances (self-DoS prevention). | T2.1 | Unit test: mock LLM timeout → verdict is Allow (infra); mock LLM raises ConnectionError → Allow (infra); mock LLM returns malformed JSON → Deny (judgment) + count; mock LLM returns empty verdict → Deny (judgment) + count; config missing → Deny (judgment) + count. |
| T2.4 | Wire `WatchoverEvaluator` into `create_watchover_check_node()`: extract the pending tool call from state (the last AIMessage's `tool_calls`), call `evaluator.evaluate(...)`, and return the verdict. | T2.1, T2.2, T2.3 | Node calls evaluator with the correct tool call; verdict is returned; integration test with a mock evaluator. |
| T2.5 | Implement Deny handling in the node: increment `watchover_denial_count` in the returned state update; inject a `ToolMessage` with `content` = the denial reason and `tool_call_id` matching the denied call (so the LLM sees a tool response). Route back to `agent`. | T2.4 | Unit test: Deny verdict → `watchover_denial_count` increments; a `ToolMessage` with the correct `tool_call_id` and denial reason is in the state update. |
| T2.6 | Implement 3-strike termination: when `watchover_denial_count` reaches 3 (after increment), route to `watchover_terminate_node` (from Phase 1) instead of back to `agent`. The terminate node sets `_deferred_watchover_terminate` and routes to END (Phase 1 T1.7). **Reuses `question_pause_node` deferred-marker pattern (`graph.py:3142-3200`).** | T2.5, Phase 1 (T1.7 terminate node) | Unit test: 3rd Deny → routes to `watchover_terminate_node`; deferred marker is set; graph routes to END. |
| T2.6b | **Persist termination intent (TD-8).** BEFORE setting the RAM-only `_deferred_watchover_terminate` marker in T2.6, write the termination intent to `instance_metadata.watchover_pending_termination` via `set_metadata`. This closes the crash window between graph END and the post-graph callback. The startup recovery path (Phase 5 T5.1) checks this flag on instance restore — if present, trigger the termination cascade. The RAM marker remains for the normal path; the DB marker is the crash-safety net. | T2.6 | Unit test: set persistent marker → simulate crash (skip post-graph completion) → restore instance → marker detected → termination cascade runs. |
| T2.7 | **Thread `terminal_reason` through the terminate cascade (TD-3, TD-4).** `instance_lifecycle.py:2935` currently hard-codes `terminal_reason='aborted'`. Thread an explicit `terminal_reason` parameter through `manager.terminate_instance(instance_id, terminal_reason=...)` → `InstanceLifecycleService.terminate_instance(..., terminal_reason=...)` → `_terminate_instance_db_sync(..., terminal_reason=...)`. The watched root gets `terminal_reason="watchover_terminated"`; descendants terminated as part of the cascade keep `"aborted"`. Add `"watchover_terminated": "cancelled"` to `_STATUS_CANONICAL_MAP` in `work_status.py:102-156` (otherwise `canonicalize_status()` leaks the unknown reason). | Phase 1 (T1.7 terminate node) | Unit test: watchover 3-strike termination → DB JobItem rows show `terminal_reason="watchover_terminated"`; descendants show `"aborted"`; unified work API returns canonical `"cancelled"`. |
| T2.8 | **SSE cleanup ordering fix (CR-4, TD-5).** In `daemon/services/instance_lifecycle.py` terminate cascade: reorder `cleanup_instance(instance_id)` (currently at `:1289-1290`) to run AFTER the post-commit `stream_status_change` (`:1399-1408`) and `watchover_event` emission. Without this fix, `cleanup_instance` drops the SSE connection before the termination event is delivered — watchover terminations would be silently lost (violates FR-23). | T2.6 | Unit test: trigger watchover termination → SSE termination event IS received (connection not dropped early). |
| T2.9 | Write `test/test_watchover_decision.py`: tests for Allow (tool executes), Deny (ToolMessage injected, counter increments), **bifurcated failure** (timeout → Allow/infra; malformed → Deny/judgment+count), 3-strike (3rd judgment-Deny → terminate), deny-whole-batch (batch with one denied → all denied), and verdict parsing (valid JSON, malformed JSON, timeout). | T2.1-T2.8 | All tests pass; cover both happy path and error paths. |

## Coupling

- **Tight with: Phase 1** — depends on the `create_watchover_check_node()` stub (T1.7), the `watchover_denial_count` state key (T1.4), the routing destinations (T1.7), and the deferred terminate node (T1.7).
- **Loose with: Phase 3** — Phase 3 sets `watchover_context` in `instance_metadata`; this phase reads it. If Phase 3 is not done, the evaluator can be tested with a manually-set context.

## Reuse Callouts

| Pattern | Source | Reused For |
|---------|--------|------------|
| `LoopRepairer.repair()` — sync LLM via `asyncio.to_thread` + `asyncio.wait_for` | `graph.py:1024-1174` | `WatchoverEvaluator.evaluate()` — lightweight LLM call in a graph node |
| `question_pause_node` deferred marker → END | `graph.py:3142-3200` | 3-strike termination: node sets marker, routes to END, cascade runs post-graph |
| ToolMessage injection (LangGraph standard) | LangGraph ToolNode behavior | Deny response: inject a `ToolMessage` so the agent sees a "tool result" explaining the denial |

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| P2-R1 | LLM call adds significant latency to every watched tool call. | High | T2.1: `asyncio.wait_for` timeout (configurable, default 5s). Use a cheap model via `agents/watcher/meta.json` watcher_model config. Phase 5 adds latency profiling. |
| P2-R2 | ~~Fail-closed on all errors~~ — RESOLVED by LD-2 bifurcated handling. Infrastructure errors (timeout, 5xx, network) now fail-OPEN (allow + log + degraded SSE, no count). Only judgment errors fail-closed. This prevents self-DoS cascade during provider outages. | Low | AD-6 updated to bifurcated per LD-2. Document the two error classes in `agents/watcher/rule.md`. |
| P2-R3 | ToolMessage injection confuses the agent — the agent may not understand why its tool call returned a denial message instead of a result. | Medium | T2.5: craft the ToolMessage content clearly ("BLOCKED by watchover: <reason>. Do not retry this action."). Test that the agent adjusts behavior after seeing it. |
| P2-R4 | Parallel tool calls (LLM emits multiple `tool_calls` in one AIMessage) — handled by **deny-whole-batch** (LD-1 ACCEPTED). Evaluate ALL calls in the batch independently; if ANY call is denied, deny the ENTIRE batch: inject one denial ToolMessage per denied call + a "deferred — batch contained denied call" ToolMessage for allowed-but-not-executed calls; route back to `agent`. No `watchover_finalize_denials` node, no message replacement, no post-tools router extension. This eliminates the checkpoint/restart inconsistency surface entirely. | Low (simplified by LD-1) | T2.4: evaluate all calls; on any Deny, inject denials for all and route to agent. Phase 5 T5.5 documents semantics. |

## Exit Criterion

- `WatchoverEvaluator` makes a lightweight LLM call and returns a structured verdict.
- Allow verdict → tool executes normally.
- Deny verdict → ToolMessage injected, counter increments, agent re-runs.
- 3rd Deny → deferred termination marker set, graph routes to END.
- Bifurcated failure (LD-2): judgment errors (malformed/unparseable) → Deny + count; infrastructure errors (timeout/5xx/network) → Allow + log + degraded SSE, no count.
- `test/test_watchover_decision.py` passes (all paths covered).
