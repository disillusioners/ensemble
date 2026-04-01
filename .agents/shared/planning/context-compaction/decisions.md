# Architecture Decisions: Context Compaction

## Revision History

| Version | Date | Changes |
|---------|------|---------|
| v1 | 2026-04-01 | Initial plan |
| v2 | 2026-04-01 | Critical fixes from review: CRIT-1 (RemoveMessage), CRIT-2 (progressive reduction), CRIT-3 (chunked summarization), CRIT-4 (continuation test), WARN-1 through WARN-6 |
| v3 | 2026-04-01 | Emergency restoration + review fixes: REV-CRIT-1 (SessionState schema), REV-CRIT-2 (emergency truncation + merge + batch truncate), REV-WARN-1 (configurable threshold), REV-WARN-4 (spawn_session) |

---

## Decision 1: Pre-Invocation Compaction vs. Graph Node

**Chosen**: Pre-invocation compaction in `manager.py`
**Alternative**: Add a `compactor` node to the LangGraph graph

**Rationale**:
- Adding a node would complicate the graph's simple `START → agent → tools → agent → END` structure
- A compactor node would run on every turn (latency) even when no compaction is needed
- Pre-invocation check is more natural: "check before you send to LLM"
- `graph.aupdate_state()` already creates checkpoints, so crash recovery is automatic
- Less invasive to the existing codebase

---

## Decision 2: Boundary Groups vs. Index-Based Slicing

**Chosen**: Group messages into atomic boundary groups
**Alternative**: Simple index-based slicing (keep messages[N:])

**Rationale**:
- Tool calls and their responses MUST stay together — LangGraph enforces this
- If you send an AIMessage with tool_calls but the ToolMessage is missing, the graph breaks
- Boundary groups ensure we never split a tool call from its response
- Slightly more complex but much safer

---

## Decision 3: RemoveMessage Sentinels for State Replacement (CRIT-1 FIX)

**Chosen**: Use `RemoveMessage` sentinels + new messages via `aupdate_state()`
**Alternative (WRONG)**: Pass compacted message list directly to `aupdate_state()`

**Problem with alternative**: `MessagesState` uses the `add_messages` reducer which is append-only. Passing `{"messages": compacted_list}` to `aupdate_state()` CONCATENATES the compacted list onto the existing history — doubling it.

**Correct pattern**:
```python
# For each message to remove:
removals = [RemoveMessage(id=m.id) for m in messages_to_remove]
# New message to add:
summary = SystemMessage(content="[Conversation Summary]\n...", id="compaction-<uuid>")
# Single aupdate_state call:
await graph.aupdate_state(config, {"messages": removals + [summary]}, as_node="agent")
```

**Verified experimentally**:
1. Created a graph with 4 messages
2. Sent `RemoveMessage` for first 2 + new summary via `aupdate_state()`
3. Result: 3 messages (2 preserved + 1 summary) — correct!
4. Sent new message after compaction — graph continued working correctly
5. Checkpoint was preserved — crash recovery works

---

## Decision 4: SystemMessage for Summary (WARN-1 FIX)

**Chosen**: Insert summary as `SystemMessage` with `[Conversation Summary]` marker
**Alternative (WRONG)**: Use `AIMessage` for summary

**Problem with AIMessage**: The LLM might interpret a previous "assistant message" as its own prior response and try to continue it, or confuse it with conversational context. `AIMessage` is semantically "the assistant said this" — but the summary is NOT what the assistant said.

**Why SystemMessage**:
- `SystemMessage` is clearly "context provided to the model" — not conversation
- The `[Conversation Summary]` marker makes it unambiguous
- The agent node in `graph.py` already prepends a `SystemMessage` (the system prompt), so adding another is natural
- LangGraph handles multiple `SystemMessage` objects correctly

---

## Decision 5: Progressive Window Reduction (CRIT-2 FIX)

**Chosen**: Shrink preserved window when it alone exceeds token threshold
**Alternative (WRONG)**: Fixed window size regardless of token count

**Problem**: `recent_message_window=10` counts boundary GROUPS, not individual messages. With tool-heavy sessions (2-4 messages per group), 10 groups = 20-40 messages. On gpt-4 (8K context), 40 tool-heavy messages could easily exceed the threshold. With no compactable messages remaining, compaction returns `None` → tokens still over limit → LLM call fails → retry → compaction returns `None` again → infinite failure loop.

**Solution**: After selecting compactable vs preserved, check if preserved + system prompt tokens alone exceed threshold. If so, shrink window by 1 group and try again. Minimum is `min_recent_window` (configurable, default 3).

**Configuration impact**: Added `min_recent_window` to `CompactionConfig` (default: 3 groups).

---

## Decision 6: Chunked Summarization (CRIT-3 FIX)

**Chosen**: If compactable messages exceed 60% of context window, summarize in batches then merge
**Alternative (WRONG)**: Send all compactable messages to summarization LLM in one call

**Problem**: Compaction is triggered BECAUSE the total messages are large. Sending all old messages to the summarization LLM could itself exceed the context window — the very problem we're trying to solve.

**Solution**: Check if compactable messages exceed `summarization_chunk_threshold` (default: 0.60) of context window. If so:
1. Split compactable groups into batches of ~20 groups
2. Summarize each batch separately
3. If multiple partial summaries, merge them into a final summary

This adds 2-3 extra LLM calls for large sessions but prevents the summarization itself from failing.

**Configuration impact**: Added `summarization_chunk_threshold` to `CompactionConfig`.

---

## Decision 7: Token Estimation Method

**Chosen**: tiktoken `cl100k_base` (existing `estimate_tokens()` in `loader.py`)
**Alternative**: Use model-specific tokenizers or API-based counting

**Rationale**:
- `estimate_tokens()` already exists and is tested
- tiktoken is fast and runs locally (no API call overhead)
- Token counts are always approximate — exact matching isn't necessary for threshold detection
- The 80% threshold provides enough buffer for estimation errors

---

## Decision 8: Model-Aware Context Limits with Override

**Chosen**: Model-aware context limits with global override
**Alternative**: Single global context window size

**Rationale**:
- Different models have vastly different context windows (8K to 200K tokens)
- A single global value would either be too small for large-context models or too large for small ones
- The registry approach with fuzzy model name matching is pragmatic
- Override option for edge cases or custom models

---

## Decision 9: Compaction Dedup via Metadata (WARN-2 FIX)

**Chosen**: Store `compacted_at` timestamp in state metadata; skip if recent
**Alternative**: No dedup — compact on every message

**Problem without dedup**: After compaction, the next message triggers compaction check again. If the threshold is still exceeded (e.g., summary + recent window still large), it would re-compact every single message, wasting LLM calls.

**Solution**: After successful compaction, store ISO timestamp in state metadata. On next check, if `last_compacted_at` is within a short window (e.g., 60 seconds), skip compaction. After the window expires, re-check normally.

---

## Decision 10: Skip Compaction on Retry (WARN-5 FIX)

**Chosen**: Skip compaction when `is_retry=True`
**Alternative**: Always run compaction

**Rationale**: On retry, the graph resumes from the checkpoint. If compaction ran on the first attempt, the checkpoint already contains the compacted state. Running compaction again on retry would be:
- Redundant (wasting an LLM call)
- Potentially harmful (re-compacting already-compacted state)
- Already handled by the dedup guard, but explicit skip is clearer and cheaper

The `is_retry` flag is already available in `_process_message_with_tracking()`.

---

## Decision 11: Config Loading Wiring (WARN-6)

**Chosen**: Explicit `if "compaction" in processed_config:` in `load_config()`
**Alternative**: Auto-discovery of config sections

**Rationale**: All existing config sections (llm, daemon, limits, persistence, agents, queue) use the explicit pattern. The new `compaction` section follows the same pattern exactly. No special handling needed.

---

## v3 Revision Decisions

### Decision 12: Custom `SessionState` Schema (REV-CRIT-1)

**Chosen**: Extend `MessagesState` with `compacted_at: Optional[str] = None`
**Alternative (BROKEN)**: Store `compacted_at` via `aupdate_state({"compacted_at": ...})` on `MessagesState`

**Problem**: `MessagesState` only has a `messages` channel. When you call `aupdate_state({"compacted_at": "..."})`, LangGraph silently drops keys not in the schema. The dedup mechanism was completely non-functional — `compacted_at` was never persisted.

**Fix**: Create `SessionState(MessagesState)` with `compacted_at` as a proper field. Now `aupdate_state({"compacted_at": "..."})` persists to the checkpoint, and `state.values["compacted_at"]` returns the value.

**Impact**: Phase 3 (graph.py must use `SessionState`), Phase 4 (tests verify dedup via state values).

---

### Decision 13: Emergency Truncation (REV-CRIT-2)

**Chosen**: Three-pass individual message truncation when progressive window reduction fails
**Alternative**: Return None and let the LLM call fail

**Problem**: When `len(groups) <= min_recent_window` and total tokens still exceed threshold, `select_compactable_groups()` returns `([], groups, min_window)`. With empty compactable, `compact_state()` returned `None`. The LLM call would then fail with context overflow → retry → same result → infinite failure loop.

**Fix**: After `select_compactable_groups()` returns empty compactable, check if tokens still exceed threshold. If so, apply `emergency_truncate()` which:
1. Truncates tool responses to `max_tool_response_chars` (default 2000)
2. Truncates human messages to `max_human_message_chars` (default 4000)
3. Progressively truncates all messages from oldest until under limit

This guarantees the LLM call can succeed, even if with degraded context.

**Also adds**:
- `_truncate_batch_to_fit()`: Prevents individual summarization batches from overflowing
- `_merge_summaries()`: Hierarchical merge for 4+ partial summaries with size-check condensation

**Impact**: Phase 2 (new methods), Phase 4 (new tests for emergency truncation, chunked merge).

---

### Decision 14: Configurable Compaction Threshold (REV-WARN-1)

**Chosen**: Replace hardcoded `0.80` with `config_threshold` parameter
**Alternative**: Keep hardcoded threshold

**Problem**: The threshold check in `select_compactable_groups()` used `context_window * 0.80` while the config already had `compaction.threshold` as a configurable value. Users who set a different threshold would see no effect on the preserved window check.

**Fix**: Add `config_threshold` parameter to `select_compactable_groups()`, defaulting to `0.80` but typically passed from `context.config.threshold`.

**Impact**: Phase 2 (function signature change).

---

### Decision 15: Correct Session Creation Method (REV-WARN-4)

**Chosen**: Use `spawn_session()` in all test code
**Alternative (WRONG)**: `create_session()` which doesn't exist

**Problem**: Phase 4 test code referenced `manager.create_session()` which is not a real method. The actual method is `SessionManager.spawn_session()`.

**Fix**: Replace all `create_session` with `spawn_session` in test code.

**Impact**: Phase 4 (test code).
