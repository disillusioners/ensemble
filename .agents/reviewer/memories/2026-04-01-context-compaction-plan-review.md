# Review: Context Compaction Plan

## Review Summary
**Needs Work** — [21 findings: 4 critical, 9 warnings, 8 suggestions]

The plan is well-structured and demonstrates solid understanding of the codebase. However, there is **one blocking architectural flaw** (`aupdate_state` append behavior) and several edge cases that would cause production failures if unaddressed.

## Scope
All planning documents in `.agents/shared/planning/context-compaction/`:
- `plan-overview.md` (120 lines)
- `decisions.md` (68 lines)
- `phase1-plan.md` (214 lines)
- `phase2-plan.md` (242 lines)
- `phase3-plan.md` (223 lines)
- `phase4-plan.md` (235 lines)

Cross-referenced against: `daemon/graph.py`, `daemon/config.py`, `daemon/loader.py`, `daemon/manager.py`

## Sessions Used
- `compaction-arch` — Architecture decisions & LangGraph feasibility analysis
- `compaction-engine` — Implementation correctness & edge case analysis
- `compaction-testing` — Test coverage adequacy analysis

---

## Findings

### 🔴 Critical

#### CRIT-1: `aupdate_state()` APPENDS — does NOT replace messages
**Area:** Phase 3 / Decision 1  
**Source:** ARCH-1 (compaction-arch)

The plan assumes `graph.aupdate_state(config, {"messages": compacted_messages}, as_node="agent")` **replaces** the message history. This is **incorrect**. `MessagesState` uses `add_messages` reducer which is append-only. Calling `aupdate_state()` with a messages dict will **concatenate** `compacted_messages` to the existing list — doubling message history instead of replacing it.

**Evidence:**
- `langgraph/graph/message.py`: `MessagesState` = `messages: Annotated[list[BaseMessage], add_messages]` with append-only reducer
- `phase3-plan.md:97`: `await graph.aupdate_state(config, {"messages": result.compacted_messages}, as_node="agent")` — no `Overwrite` wrapper
- `plan-overview.md:79`: "Replace old messages with summary via `graph.aupdate_state()`"

**Recommendation:** Use `Overwrite` sentinel to bypass the reducer:
```python
from langgraph.types import Overwrite
await graph.aupdate_state(config, {
    "messages": Overwrite(value=result.compacted_messages)
}, as_node="agent")
```
This is the **only** way to truly replace the messages list. Must be reflected in Phase 3 plan.

---

#### CRIT-2: Infinite failure loop when preserved window exceeds threshold
**Area:** Phase 2 / `select_compactable_groups()` + `compact_state()`  
**Source:** COMP-002, COMP-003 (compaction-engine)

`select_compactable_groups()` counts **boundary groups**, not messages. `recent_message_window=20` means 20 groups. With heavy tool usage (AI + 2-3 ToolMessages per group = 2-4 messages per group), that's 40-80 actual messages preserved. With gpt-4 at 8192 tokens × 0.80 threshold = 6553 tokens, 40 verbose tool messages could already consume 6000-8000 tokens.

When this happens: `compactable` is empty → `compact_state()` returns `None` → but tokens remain over threshold → LLM call fails → next message triggers same scenario → **infinite failure loop**.

The `_truncate_fallback()` has the same problem — it preserves `preserved` groups, so if those alone exceed threshold, truncation doesn't help.

**Evidence:**
- `phase2-plan.md:142-147`: `select_compactable_groups` returns `([], groups)` when `len(groups) <= recent_window`
- `phase2-plan.md:196-197`: "If not compactable: return None"
- `phase1-plan.md:51`: Field description says "messages" but implementation uses groups

**Recommendation:**
1. Add "last resort" truncation: when `compactable` is empty but tokens exceed threshold, progressively reduce preserved window (e.g., `recent_window // 2`, then `// 4`)
2. Add minimum preservation guarantee: always keep at least 1 HumanMessage + 1 AIMessage
3. Log a warning when preserved window itself exceeds threshold
4. Consider changing `recent_message_window` to count actual messages instead of groups, iterating from newest until count is reached while respecting group boundaries

---

#### CRIT-3: Missing risk — summarization LLM call can itself overflow tokens
**Area:** plan-overview.md Risks table  
**Source:** ARCH-4 (compaction-arch)

The risks table (plan-overview.md:92-101) does not account for the summarization LLM call's own token usage. The summarization prompt sends ALL old messages to the LLM for summarization. If the compactable messages are very large (which is likely — that's why compaction triggered), the summarization call itself could exceed the LLM's context window, causing a secondary token overflow error.

**Evidence:**
- `phase2-plan.md:217`: `_summarize_groups()` sends all compactable groups to LLM for summarization
- No mention of chunked summarization or max summarization input limits

**Recommendation:** Add to risks table. Mitigation: cap summarization input to a fraction of context window (e.g., 60%), chunk if necessary, or use a larger-context model for summarization.

---

#### CRIT-4: No end-to-end test for graph continuation after compaction with tool calls
**Area:** Phase 4  
**Source:** TEST-6 (compaction-testing)

The integration test (`test_compaction_triggers_after_threshold`) only verifies message count reduction. It does NOT test:
1. After compaction, can `graph.astream()` continue processing correctly?
2. Do tool calls work after compaction resumes from checkpoint?
3. Is the compacted summary message correctly included in subsequent LLM calls?

Without this test, the critical integration point (CRIT-1 fix + graph continuation) is untested.

**Recommendation:** Add `test_astream_after_compaction_with_tools` that compacts mid-session, then sends a message triggering tool calls, and verifies the full pipeline works.

---

### 🟡 Warnings

#### WARN-1: Summary as AIMessage may confuse the LLM
**Area:** Decision 3  
**Source:** ARCH-2 (compaction-arch)

The summary is inserted as an `AIMessage` at position 0 of history. The LLM sees: `SystemPrompt → SummaryAIMessage → recent messages`. The LLM could interpret this as a prior assistant response and "continue" from it rather than treating it as injected memory context.

**Recommendation:** Either (a) use `SystemMessage` with clear marker like `[Conversation Summary]`, or (b) prefix the AIMessage content with `[Earlier conversation summary]:` and add `additional_kwargs` marker. Option (a) is semantically more correct.

---

#### WARN-2: Cascading compaction risk — no loop guard
**Area:** plan-overview.md Risks table  
**Source:** ARCH-4 (compaction-arch)

After compaction, if the result is still above threshold (e.g., summary was verbose, recent window large), the next message will trigger compaction again on already-compacted state. No "recently compacted" flag or minimum interval guard exists.

**Recommendation:** Add `min_compaction_interval` config (e.g., "don't compact again within N messages") or a "compaction_epoch" counter in state metadata.

---

#### WARN-3: Checkpoint history bloat after compaction
**Area:** plan-overview.md Risks table  
**Source:** ARCH-4 (compaction-arch)

Each `aupdate_state()` creates a new checkpoint. After compaction, the checkpointer stores the pre-compaction checkpoint (full history) AND the post-compaction checkpoint. For sessions compacted 10 times, this means 10 full-history checkpoints consuming significant SQLite storage.

**Recommendation:** Document this behavior. Consider adding checkpoint pruning logic or TTL for pre-compaction checkpoints.

---

#### WARN-4: Cost amplification from summarization LLM calls
**Area:** plan-overview.md Risks table  
**Source:** ARCH-4 (compaction-arch)

Each compaction triggers an extra LLM call for summarization. For busy sessions near the threshold, compaction could fire on nearly every message — doubling or tripling API costs. No cost budget, compaction frequency limit, or cost metric is mentioned.

**Recommendation:** Add compaction count metric. Consider `max_compaction_count_per_session` limit. Log estimated cost per compaction call.

---

#### WARN-5: Compaction runs redundantly on retry
**Area:** Phase 3  
**Source:** COMP-005 (compaction-engine)

Compaction runs before every `astream`/`ainvoke`, including retries. On retry, state was already compacted from the first attempt. Re-running wastes an LLM call and could compound issues if compaction partially corrupts state.

**Recommendation:** Skip compaction on `is_retry=True`, or add idempotency check (skip if <5% token reduction).

---

#### WARN-6: Config loading requires explicit wiring
**Area:** Phase 1 / config.py  
**Source:** COMP-004 (compaction-engine)

`load_config()` in `config.py` has hardcoded field extraction (lines 173-185). The plan mentions "add config loading support" but doesn't explicitly note the need for `if "compaction" in processed_config:` block. The Pydantic `env_prefix="COMPACTION_"` inside a parent with `env_prefix=""` works correctly in Pydantic v2.

**Recommendation:** Add explicit task: add `if "compaction" in processed_config: config_dict["compaction"] = processed_config["compaction"]` to `load_config()`.

---

#### WARN-7: No test for re-compaction of already-compacted sessions
**Area:** Phase 4  
**Source:** TEST-1 (compaction-testing)

After first compaction, state starts with a summary AIMessage. If threshold is hit again:
- Does `identify_boundary_groups()` treat the summary as a standalone group?
- Does the summarization prompt handle context that already contains a summary?
- Could the summary be incorrectly merged with a following tool group?

**Recommendation:** Add `test_recompaction_after_first_compaction` — compact once, add more messages, compact again, verify summary is updated correctly.

---

#### WARN-8: No test for sessions with only tool calls
**Area:** Phase 4  
**Source:** TEST-2 (compaction-testing)

All proposed tests assume HumanMessage or text AI responses exist. No test covers edge case where every message is part of a tool group.

**Recommendation:** Add `test_only_tool_messages` to verify boundary detection handles all-tool sequences correctly.

---

#### WARN-9: Integration test creates SessionManager incorrectly
**Area:** Phase 4  
**Source:** TEST-7 (compaction-testing)

The plan does `manager._checkpointer = checkpointer` but doesn't call `await manager.initialize()`. SessionManager's checkpointer is lazy-initialized via `initialize()`. Direct attribute injection may not properly wire the checkpointer into the graph builder.

**Recommendation:** Follow existing test patterns (see `test_session_title_e2e.py`): create SessionManager with Config, use `broadcaster.set_main_loop()`, and let it manage its own persistence.

---

### 🟢 Suggestions

#### SUGG-1: Config field description misleading — groups vs. messages
**Area:** Phase 1 / CompactionConfig  
**Source:** ARCH-5 (compaction-arch)

`recent_message_window` description says "Number of most recent messages" but implementation counts boundary groups. With tool-heavy sessions, 20 groups = 40-100 messages.

**Recommendation:** Either rename to `recent_group_window` with updated description, or change implementation to count actual messages (harder — must respect group boundaries).

---

#### SUGG-2: `estimate_messages_tokens()` design needs spec in Phase 1
**Area:** Phase 1  
**Source:** ARCH-6 (compaction-arch)

Phase 1 lists `estimate_messages_tokens()` as a task but the detailed design is in Phase 2's code. The Phase 1 plan should include the per-message overhead constants and approach for structured content blocks.

**Recommendation:** Move the `estimate_messages_tokens()` design from Phase 2 code comments to Phase 1 plan document.

---

#### SUGG-3: `test_mixed_conversation` assertion is incorrect
**Area:** Phase 4  
**Source:** TEST-8 (compaction-testing)

The test expects 5 groups but the message sequence produces 4:
| Group | Messages | Indices |
|-------|----------|---------|
| 1 | HumanMessage | [0] |
| 2 | AIMessage (no tools) | [1] |
| 3 | AIMessage + ToolMessage | [2, 3] |
| 4 | AIMessage (no tools) | [4] |

**Recommendation:** Fix assertion to `assert len(groups) == 4`.

---

#### SUGG-4: No performance test for large sessions
**Area:** Phase 4  
**Source:** TEST-9 (compaction-testing)

No benchmark for compaction with 100+ messages. Summarization LLM call could take 10+ seconds.

**Recommendation:** Add optional `@pytest.mark.slow` performance test measuring compaction time.

---

#### SUGG-5: Add test for threshold exceeded but nothing compactable
**Area:** Phase 4  
**Source:** TEST-4 (compaction-testing)

When system prompt tokens alone push total over threshold but all messages fit within recent_window, `compact_state()` returns None. The LLM then fails. This edge case should have explicit test coverage.

---

#### SUGG-6: No test for very large single messages
**Area:** Phase 4  
**Source:** TEST-3 (compaction-testing)

A single tool response with 50K+ characters could dominate token usage. `estimate_messages_tokens()` accuracy should be tested at scale.

---

#### SUGG-7: Consider per-session locks for better concurrency
**Area:** Phase 3  
**Source:** COMP-006 (compaction-engine)

Both `send_message()` and `_process_message_with_tracking()` acquire the same process-wide `_processing_lock`. This serializes all sessions, not just same-session access.

**Recommendation:** Consider per-session locks (`self._session_locks: dict[str, asyncio.Lock]`) for better throughput. Current design is safe but limits concurrency.

---

#### SUGG-8: Document the Overwrite API dependency
**Area:** Phase 3  
**Source:** ARCH-4 (compaction-arch)

`Overwrite` class was added in LangGraph 0.3+. If the project ever downgrades, compaction breaks. Add minimum LangGraph version requirement.

---

## Recommendations (Priority Order)

### Must Fix Before Implementation
1. **CRIT-1**: Update Phase 3 to use `Overwrite` wrapper in `aupdate_state()` calls. This is blocking — the plan will corrupt state without it.
2. **CRIT-2**: Redesign the threshold-overflow escape hatch. Add progressive window reduction or message-count-based preservation. The current design deadlocks for gpt-4 sized contexts with tool-heavy sessions.
3. **CRIT-3**: Add summarization input size limit and chunking strategy to Phase 2.

### Should Fix Before Implementation
4. **WARN-1**: Consider `SystemMessage` for summary instead of `AIMessage`.
5. **WARN-2**: Add compaction loop guard (min interval or epoch counter).
6. **WARN-5**: Skip compaction on retry (`is_retry=True`).
7. **WARN-6**: Explicitly note the `load_config()` wiring needed.
8. **CRIT-4**: Add end-to-end test for graph continuation after compaction with tool calls.

### Can Fix During Implementation
9. **WARN-7, WARN-8, SUGG-3, SUGG-5, SUGG-6**: Add missing test scenarios.
10. **SUGG-1**: Update config field description.
11. **WARN-3, WARN-4**: Document checkpoint bloat and cost implications.
