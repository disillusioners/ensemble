# Architecture Decisions: General Hallucination Loop Breaker

## D1: Detection Algorithm — Message-Tail Scan vs. Counter-Based

**Decision**: Message-tail scan (walk backwards from `messages[-1]`)

**Rationale**: 
- The existing GII throttle uses a counter (`_gii_throttle[instance_id] += 1`), but this has known gaps:
  - Parallel tool calls bypass it (interleaved ToolMessages reset counter)
  - Counter doesn't survive reactive compaction re-reads
  - Counter must be manually reset on every non-matching message
- Message-tail scan inspects the actual message history, which is the source of truth
- Handles parallel tool calls naturally (signature covers all tool_calls in one AIMessage)
- No state to lose — works even after compaction, pause/resume, or crash recovery

**Trade-off**: Slightly more CPU per agent_node entry (O(n) scan of message tail). Acceptable since we only scan the tail (until first non-tool message, typically <20 messages).

---

## D2: Repair Mechanism — Message Removal + LLM Summary (NOT Sleep)

**Decision**: Remove repetitive messages + LLM summarization + repair SystemMessage

**Rationale**:
- The user's key insight: "even if we inject a message, the LLM's KV cache is stale, and it will likely continue hallucinating"
- Sleep delays (GII throttle approach) only delay — they don't fix the root cause
- Removing repetitive messages + injecting a fresh summary "resets" the LLM's context window
- This forces the LLM provider to compute a fresh KV cache (new context = new cache)
- The LLM summary helps the agent understand WHY it was stuck, not just that it was stuck

**Trade-off**: Adds an LLM call (cost + latency) when a loop is detected. Acceptable since loops are rare and the alternative is infinite recursion (which costs more).

---

## D3: GII Throttle — Coexist (NOT Replace)

**Decision**: Keep the existing GII throttle alongside the new loop breaker

**Rationale**:
- GII throttle serves a specific purpose (polling rate limiting via sleep)
- Loop breaker serves a different purpose (context repair via message manipulation)
- They're complementary: sleep gives the provider a breather, repair gives fresh context
- Removing GII throttle risks regressions in the ~970 lines of tests in `test_gii_throttle.py`
- The loop breaker will ALSO catch GII loops (since GII calls are just tool calls), providing defense-in-depth

**Future refactor** (deferred): Once the loop breaker is stable, GII throttle could be refactored to use the general detection. This is a separate task.

---

## D4: State Storage — RAM-Only Dict on InstanceManager

**Decision**: `_loop_breaker_state: dict[str, dict]` on InstanceManager (RAM-only, no DB)

**Rationale**:
- Follows the exact pattern of `_gii_throttle` and `_pending_injections`
- Loop-breaker state is transient — it should NOT survive session restart
- A restarted session starts fresh (no stale loop state)
- No DB schema changes or migrations needed
- No PostgreSQL/SQLite dual-driver concerns
- Cleanup follows the same 5-path pattern

**Trade-off**: State is lost on daemon restart. This is correct behavior — a fresh start should have fresh loop detection.

---

## D5: Repair Count Limit — Max 3 Repairs Per Session

**Decision**: `max_repairs = 3` (configurable via `LoopBreakerConfig.max_repairs`). After max, force continuation with original messages.

**Rationale**:
- If the LLM keeps looping even after 3 repairs, something is fundamentally wrong
- Continuing to repair infinitely wastes LLM calls
- After max repairs, let the graph continue (will eventually hit `recursion_limit=100` if truly stuck)
- The repair count resets when no loop is detected (agent made progress)
- **Config field**: `LoopBreakerConfig.max_repairs: int = 3` (Phase 1 task 1)

---

## D6: Keep 1 Instance as Evidence (NOT Remove All)

**Decision**: When removing repetitive messages, keep the FIRST instance as evidence

**Rationale**:
- Completely removing all traces of the repetitive calls might confuse the LLM
- Keeping 1 instance shows the agent what it was doing
- The repair message explains WHY those calls were repetitive
- The agent can see: "I called X with Y args, and the system says I was looping"

---

## D7: Excluded Tools Config

**Decision**: Configurable `excluded_tools: list[str]` in LoopBreakerConfig

**Rationale**:
- Some tools are legitimately called repeatedly (e.g., polling a changing resource)
- Default empty list (detect all tools)
- Admins can add tools to the exclude list via config

---

## D8: Scope — Tool-Call Loops Only (NOT Ghost-Promise/Nudge)

**Decision**: Phase 1 detects tool-call loops only. Ghost-promise and nudge cycles are deferred.

**Rationale**:
- Tool-call loops are the most common and damaging hallucination pattern
- Ghost-promise (`should_continue` returns "agent" when content ends with `:`) and nudge cycles (empty response after tool) are different problems:
  - They don't involve repeated tool calls
  - They need different detection (message-content analysis, not tool-signature matching)
  - The repair mechanism (Phase 2) is general enough to be extended later
- Keeping scope focused ensures delivery. Ghost-promise/nudge breaking is a follow-up task.

**Future work**: Add `GhostPromiseDetector` and `NudgeCycleDetector` that feed into the same `LoopRepairer`.

---

## D9: Repair Message Type — SystemMessage (NOT HumanMessage)

**Decision**: Repair message is a `SystemMessage`, not a `HumanMessage`

**Rationale**:
- The repair is a system-level intervention, not a user message
- Using `HumanMessage` could confuse the agent (thinking a user sent it)
- `SystemMessage` is clearly a system directive
- The nudge_node uses `HumanMessage` for a different purpose (prompting continuation)
- Compaction summaries also use `SystemMessage` (compaction.py:894)

---

## D10: LLM Config — Reuse Session LLM Config with clean_llm_config

**Decision**: Reuse the session's `llm_config` for summarization, stripping `model_vision`

**Rationale**:
- Follows the compaction system's pattern (`_call_summarization_llm`, compaction.py:986-995)
- `clean_llm_config()` strips `model_vision` (vision routing irrelevant for text summarization)
- Configurable `summarization_model` override (optional, like compaction's `summarization_model`)
- Avoids introducing a new LLM configuration path

---

## D11: Integration Location — Inside agent_node (NOT should_continue)

**Decision**: Detection + repair runs inside `agent_node`, not in `should_continue`

**Rationale**:
- `should_continue` only routes — it doesn't have access to `graph_ref` or `llm_config`
- `agent_node` has all needed context: messages, graph_ref, llm_config, injected_msg, instance_id
- The repair needs to call `graph.aupdate_state` and re-invoke the LLM — only possible inside `agent_node`
- Same location as the GII throttle (graph.py:871-889) and reactive compaction (graph.py:899-958)
- The `agent → agent` self-edge (ghost promise) has no node transition, so a `should_continue`-based breaker couldn't add delays anyway

---

## D12: Summarization Timeout — 30s with Fallback

**Decision**: The repair LLM summarization call MUST be wrapped in `asyncio.wait_for(timeout=30)`. On timeout, fall back to a static truncation summary. Configurable via `LoopBreakerConfig.summarization_timeout_seconds`.

**Rationale**:
- The repair runs synchronously inside `agent_node` — a hung LLM call freezes the entire agent
- Without a timeout, a slow/unresponsive LLM provider could block indefinitely
- The fallback (static summary) is sufficient to break the loop — it just lacks the contextual nuance of an LLM-generated summary
- The repair still completes: messages are removed, repair message is injected, LLM is re-invoked
- 30s is generous for a simple summarization call but short enough to not freeze agents for minutes

**Trade-off**: LLM-generated summaries are richer than static fallbacks. But freezing the agent is worse than a slightly less informative repair message.
