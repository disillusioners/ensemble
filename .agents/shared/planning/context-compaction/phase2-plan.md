# Phase 2: Compaction Engine

## Objective

Build the core compaction logic module: boundary detection (preserving tool call/message pairs), progressive window reduction (CRIT-2 fix), chunked summarization (CRIT-3 fix), and state replacement using `RemoveMessage` sentinels (CRIT-1 fix). The summary uses `SystemMessage` with `[Conversation Summary]` marker (WARN-1).

## Context

- **Previous phase**: Phase 1 — Configuration & Token Estimation (completed)
- **Key files created**: `daemon/compaction.py`, `daemon/loader.py` enhanced with `estimate_messages_tokens()`
- **Key decisions**:
  - `ContextCompactor` is a standalone class (not tied to manager.py) so it can be unit tested independently
  - Boundary detection uses message pair grouping rather than naive index-based slicing
  - Summary is a `SystemMessage` (not `AIMessage`) with `[Conversation Summary]` marker (WARN-1)
  - State replacement uses `RemoveMessage` sentinels, NOT raw message replacement (CRIT-1)
  - Progressive window reduction prevents infinite loops on small-context models (CRIT-2)
  - Chunked summarization prevents overflow of the summarization LLM call (CRIT-3)
  - Summarization uses the same LLM as the session (or override) with a carefully crafted prompt

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Design `CompactionContext` dataclass** | Container for all inputs needed for compaction: messages, system_prompt_tokens, model name, config, LLM config, last_compacted_at | `daemon/compaction.py` |
| 2 | **Implement `identify_boundary_groups()`** | Group messages into atomic units — each group is either a single message or an AIMessage with tool_calls + its ToolMessages. Groups must NEVER be split. | `daemon/compaction.py` |
| 3 | **Implement `select_compactable_groups()` with progressive reduction** | Given boundary groups and recent_window, split into compactable vs. preserved. Uses configurable threshold (REV-WARN-1). If preserved alone exceeds threshold, progressively reduce the preserved window. Minimum is `min_recent_window`. | `daemon/compaction.py` |
| 4 | **Implement `emergency_truncate()` (REV-CRIT-2)** | Last-resort truncation when progressive reduction still leaves tokens over threshold. Three-pass strategy: truncate tool responses, truncate human messages, progressive oldest-first truncation. | `daemon/compaction.py` |
| 5 | **Implement `_truncate_batch_to_fit()` (REV-CRIT-2)** | Truncate individual messages within a summarization batch to fit within token limit. Truncates tool responses first, then drops oldest groups. | `daemon/compaction.py` |
| 6 | **Implement `_merge_summaries()` (REV-CRIT-2)** | Merge multiple partial summaries from chunked summarization. 2-3 summaries: single merge call. 4+: hierarchical pair-wise merge. Size check with second-pass condensation. | `daemon/compaction.py` |
| 7 | **Implement chunked summarization** | If compactable messages exceed `summarization_chunk_threshold` fraction of context window, split into batches. Summarize each batch separately, then merge summaries into a final summary. Uses `_truncate_batch_to_fit()` and `_merge_summaries()`. | `daemon/compaction.py` |
| 8 | **Implement `build_summarization_prompt()`** | Create a prompt that asks the LLM to summarize message groups into a coherent summary. Preserve: key decisions, important facts, tool actions/outcomes, user requests. | `daemon/compaction.py` |
| 9 | **Implement `_summarize_single_batch()` async method** | Take a batch of message groups, call LLM with summarization prompt, return `SystemMessage` with `[Conversation Summary]\n<summary>` content. Handle errors gracefully. | `daemon/compaction.py` |
| 10 | **Implement `_build_replacement_messages()`** | Build the complete replacement message list: `RemoveMessage` sentinels for messages to remove, plus the summary `SystemMessage`, plus preserved messages. | `daemon/compaction.py` |
| 11 | **Implement `compact_state()` async method** | Main entry point: dedup check, eligibility check, threshold check, calls boundary/summarization pipeline, applies `emergency_truncate()` if compactable is empty but tokens over threshold (REV-CRIT-2). Returns `CompactionResult` with replacement messages OR `None`. | `daemon/compaction.py` |
| 12 | **Implement `_truncate_fallback()` method** | If summarization fails (LLM error, timeout), truncate oldest messages while preserving minimum recent window. Uses `RemoveMessage` pattern. | `daemon/compaction.py` |
| 13 | **Implement `_is_recently_compacted()` dedup check** | Check if the last compaction was recent enough to skip re-compaction. Prevents re-compacting on every subsequent message. | `daemon/compaction.py` |

## Key Files

- `daemon/compaction.py` — **NEW FILE** — Full `ContextCompactor` class with all sub-methods

## Detailed Design

### CompactionContext Dataclass

```python
from dataclasses import dataclass
from typing import Optional
from langchain_core.messages import BaseMessage

@dataclass
class CompactionContext:
    """Container for all inputs needed for context compaction."""
    messages: list[BaseMessage]       # Current conversation history from LangGraph state
    system_prompt_tokens: int        # Pre-calculated token count of system prompt
    model_name: str                  # Model identifier for context window lookup
    config: CompactionConfig         # Compaction configuration
    llm_config: dict                 # LLM config dict for summarization calls
    last_compacted_at: Optional[str] # ISO timestamp of last compaction (from state metadata)
```

### CompactionResult Dataclass

```python
@dataclass
class CompactionResult:
    """Result of a compaction operation."""
    # The replacement messages: RemoveMessage sentinels + summary + preserved messages
    # This is the complete new state["messages"] list to pass to aupdate_state
    replacement_messages: list[BaseMessage]
    
    # Metrics
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    messages_before: int
    messages_after: int
    
    # How compaction was performed
    compaction_type: str  # "summarization" | "chunked_summarization" | "truncation" | "none"
    
    # Error info if summarization failed
    summarization_error: Optional[str] = None
    
    # Timestamp for dedup marker
    compacted_at: Optional[str] = None
```

### Boundary Groups Concept

A **boundary group** is the atomic unit of compaction. A group is one of:

| Type | Description | Example |
|------|-------------|---------|
| **Human group** | A single `HumanMessage` | `HumanMessage(content="fix the bug")` |
| **AI-only group** | An `AIMessage` with no tool calls | `AIMessage(content="I'll fix it...")` |
| **Tool group** | One `AIMessage` + one or more `ToolMessages` | `AIMessage(tool_calls=[...])` + `ToolMessage(content="result")` |

**Rule**: Groups are NEVER split. If a tool call + its response are in the "compactable" window, BOTH are summarized or NEITHER is.

```
[Human] → [AI (no tools)] → [AI + Tool1 + Tool1_result] → [AI + Tool2 + Tool2_result] → [AI (recent)]
          ↑                          ↑                                                          ↑
       compactable              compactable                                                preserved
```

### `identify_boundary_groups()` Implementation

```python
@dataclass
class MessageGroup:
    """Represents an atomic message group that cannot be split."""
    start_idx: int
    end_idx: int
    messages: list[BaseMessage]
    group_type: str  # "single" | "tool_sequence"

def identify_boundary_groups(messages: list[BaseMessage]) -> list[MessageGroup]:
    """Group messages into atomic boundary groups that cannot be split."""
    groups = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        msg_type = getattr(msg, 'type', 'unknown')
        
        if msg_type == 'tool':
            # Orphan tool message — shouldn't happen in well-formed input, but handle it
            groups.append(MessageGroup(i, i, [msg], "single"))
            i += 1
            continue
        
        if msg_type == 'ai' and hasattr(msg, 'tool_calls') and msg.tool_calls:
            # AI message with tool calls — find all corresponding ToolMessages
            tool_call_ids = {
                tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
                for tc in msg.tool_calls
            }
            
            group_msgs = [msg]
            j = i + 1
            while j < len(messages) and hasattr(messages[j], 'tool_call_id'):
                tc_id = getattr(messages[j], 'tool_call_id', '')
                if tc_id in tool_call_ids:
                    group_msgs.append(messages[j])
                    j += 1
                else:
                    break  # ToolMessage for a different tool call
            
            groups.append(MessageGroup(i, j - 1, group_msgs, "tool_sequence"))
            i = j
        else:
            # Single message (Human, AI without tools, System)
            groups.append(MessageGroup(i, i, [msg], "single"))
            i += 1
    
    return groups
```

### Progressive Window Reduction (CRIT-2 Fix)

**Problem**: `recent_message_window=10` counts groups, not messages. With tool-heavy sessions (2-4 msgs per group), that's 20-40 messages. On gpt-4 (8K context), this alone could exceed the threshold with no compactable messages remaining → infinite loop.

**Solution**: Progressive reduction. If compactable is empty but tokens still exceed threshold, shrink the preserved window by 1 group, try again. Minimum is `min_recent_window`.

```python
def select_compactable_groups(
    groups: list[MessageGroup],
    recent_window: int,
    min_window: int,
    context_window: int,
    system_prompt_tokens: int,
    estimate_fn,
    config_threshold: float = 0.80,  # REV-WARN-1: configurable, was hardcoded 0.80
) -> tuple[list[MessageGroup], list[MessageGroup], int]:
    """Split groups into compactable (old) and preserved (recent).
    
    Uses progressive window reduction to ensure total tokens fit within threshold.
    
    Returns:
        Tuple of (compactable_groups, preserved_groups, actual_window_used)
    """
    window = recent_window
    
    while window >= min_window:
        if len(groups) <= window:
            return [], groups, window
        
        preserved = groups[-window:]
        compactable = groups[:-window]
        
        # Check if preserved + system prompt alone exceeds threshold
        preserved_tokens = estimate_fn(
            [msg for g in preserved for msg in g.messages]
        )
        total = preserved_tokens + system_prompt_tokens
        threshold = context_window * config_threshold  # Configurable threshold (REV-WARN-1)
        
        if total <= threshold:
            return compactable, preserved, window
        
        # Too many tokens even with just preserved window
        # Reduce window and try again
        window -= 1
    
    # Emergency: even min_window exceeds threshold
    # Keep only min_window groups
    preserved = groups[-min_window:]
    compactable = groups[:-min_window]
    return compactable, preserved, min_window
```

### Emergency Truncation (REV-CRIT-2 Fix)

**Problem**: When `len(groups) <= min_recent_window` AND all groups together exceed the threshold, `select_compactable_groups()` returns `([], groups, min_window)` — there's nothing to summarize, and the LLM call will still fail.

**Solution**: After progressive window reduction fails (compactable is empty but tokens still over threshold), apply `emergency_truncate()` directly on the preserved groups' messages. This truncates individual messages (especially tool responses) to fit within the context window.

```python
def emergency_truncate(
    messages: list[BaseMessage],
    max_tokens: int,
    tokenizer,
    max_tool_response_chars: int = 2000,
    max_human_message_chars: int = 4000,
) -> list[BaseMessage]:
    """Last-resort truncation of individual messages to fit within token limit.
    
    Applied when progressive window reduction still leaves tokens over threshold.
    Truncates individual message content (especially tool responses) rather than
    removing entire messages.
    
    Strategy:
    1. First pass: truncate all tool responses to max_tool_response_chars
    2. Check if under limit — if yes, return
    3. Second pass: truncate human messages to max_human_message_chars  
    4. Check if under limit — if yes, return
    5. Third pass: progressively truncate ALL messages from oldest until under limit
    
    Args:
        messages: Messages to truncate (preserved groups only).
        max_tokens: Target maximum token count.
        tokenizer: Token estimation function.
        max_tool_response_chars: Max chars for tool responses (default 2000).
        max_human_message_chars: Max chars for human messages (default 4000).
    
    Returns:
        List of messages with truncated content. Same length as input.
    """
    import copy
    
    truncated = [copy.deepcopy(m) for m in messages]
    
    # Pass 1: Truncate tool responses
    for msg in truncated:
        if getattr(msg, 'type', '') == 'tool':
            content = msg.content or ''
            if len(content) > max_tool_response_chars:
                msg.content = content[:max_tool_response_chars] + "\n[...truncated]"
    
    if estimate_messages_tokens(truncated) <= max_tokens:
        return truncated
    
    # Pass 2: Truncate human messages
    for msg in truncated:
        if getattr(msg, 'type', '') == 'human':
            content = msg.content or ''
            if len(content) > max_human_message_chars:
                msg.content = content[:max_human_message_chars] + "\n[...truncated]"
    
    if estimate_messages_tokens(truncated) <= max_tokens:
        return truncated
    
    # Pass 3: Progressive truncation from oldest
    # Reduce all message content by 50% from oldest until under limit
    for i in range(len(truncated)):
        content = truncated[i].content or ''
        if isinstance(content, str) and len(content) > 500:
            truncated[i].content = content[:len(content) // 2] + "\n[...truncated]"
        if estimate_messages_tokens(truncated) <= max_tokens:
            break
    
    return truncated
```

**Integration in `compact_state()`**: After `select_compactable_groups()` returns empty compactable list, check if tokens still exceed threshold and apply emergency truncation to the preserved messages directly.

### `_truncate_batch_to_fit()` (REV-CRIT-2 — Part of Chunked Summarization)

**Problem**: Within chunked summarization, a single batch of ~20 groups could still exceed the summarization input limit (especially with verbose tool responses).

**Solution**: Truncate individual messages within the batch to fit.

```python
def _truncate_batch_to_fit(
    batch_groups: list[MessageGroup],
    max_tokens: int,
    tokenizer_fn,
    max_tool_response_chars: int = 2000,
) -> list[MessageGroup]:
    """Truncate a batch of message groups to fit within token limit.
    
    Strategy:
    1. First, truncate all tool responses to max_tool_response_chars
    2. If still over, drop oldest groups from the batch until under limit
    3. Always keep at least 1 group
    
    Args:
        batch_groups: Groups to potentially truncate.
        max_tokens: Maximum tokens allowed for this batch.
        tokenizer_fn: Token estimation function.
        max_tool_response_chars: Max chars for tool responses.
    
    Returns:
        Batch of groups that fits within max_tokens.
    """
    import copy
    
    # Step 1: Truncate tool responses in-place
    truncated_groups = []
    for group in batch_groups:
        new_group = copy.deepcopy(group)
        for msg in new_group.messages:
            if getattr(msg, 'type', '') == 'tool':
                content = msg.content or ''
                if len(content) > max_tool_response_chars:
                    msg.content = content[:max_tool_response_chars] + "\n[...truncated]"
        truncated_groups.append(new_group)
    
    # Check if truncation was enough
    all_msgs = [m for g in truncated_groups for m in g.messages]
    if tokenizer_fn(all_msgs) <= max_tokens:
        return truncated_groups
    
    # Step 2: Drop oldest groups until under limit (keep at least 1)
    while len(truncated_groups) > 1:
        all_msgs = [m for g in truncated_groups for m in g.messages]
        if tokenizer_fn(all_msgs) <= max_tokens:
            break
        truncated_groups = truncated_groups[1:]  # Drop oldest
    
    return truncated_groups
```

### `_merge_summaries()` (REV-CRIT-2 — Part of Chunked Summarization)

**Problem**: Multiple partial summaries from chunked summarization need to be combined into one coherent summary.

```python
async def _merge_summaries(
    self,
    partial_summaries: list[SystemMessage],
    context: CompactionContext,
) -> SystemMessage:
    """Merge multiple partial summaries into a final comprehensive summary.
    
    Strategy:
    - 2-3 summaries: concatenate with merge prompt, single LLM call
    - 4+ summaries: hierarchical merge (merge pairs, then merge the merges)
    - Size check: if merged result exceeds threshold, do second-pass condensation
    
    Args:
        partial_summaries: List of SystemMessage summaries to merge.
        context: Compaction context for LLM configuration.
    
    Returns:
        Single merged SystemMessage summary.
    """
    if len(partial_summaries) == 1:
        return partial_summaries[0]
    
    if len(partial_summaries) <= 3:
        # Simple merge: concatenate and ask LLM to combine
        combined = "\n\n---\n\n".join(
            f"Part {i+1}:\n{s.content}" 
            for i, s in enumerate(partial_summaries)
        )
        
        merge_prompt = (
            "Combine these conversation summaries into a single coherent summary. "
            "Preserve all key decisions, important facts, tool actions and their outcomes, "
            "and user requests. Remove redundancy but keep all unique information:\n\n"
            + combined
        )
        
        merged_content = await self._call_summarization_llm(merge_prompt, context)
        timestamp = datetime.now(timezone.utc).isoformat()
        return SystemMessage(
            content=f"[Conversation Summary]\nTimestamp: {timestamp}\n{merged_content}",
            id=f"compaction-merge-{uuid.uuid4()}",
        )
    
    # 4+ summaries: hierarchical merge
    # Merge pairs, then merge the resulting summaries
    while len(partial_summaries) > 3:
        next_round = []
        for i in range(0, len(partial_summaries), 2):
            pair = partial_summaries[i:i+2]
            if len(pair) == 2:
                merged = await self._merge_summaries(pair, context)
                next_round.append(merged)
            else:
                next_round.append(pair[0])
        partial_summaries = next_round
    
    # Final merge of remaining 2-3 summaries
    final = await self._merge_summaries(partial_summaries, context)
    
    # Size check: if final summary is too long, condense
    final_tokens = estimate_messages_tokens([final])
    context_window = get_model_context_limit(context.model_name, context.config)
    max_summary_tokens = context_window * 0.10  # Summary should be max 10% of context
    
    if final_tokens > max_summary_tokens:
        condense_prompt = (
            "Condense this conversation summary to be more concise while keeping "
            "all key information. Focus on decisions, facts, and outcomes:\n\n"
            + final.content
        )
        condensed = await self._call_summarization_llm(condense_prompt, context)
        timestamp = datetime.now(timezone.utc).isoformat()
        return SystemMessage(
            content=f"[Conversation Summary]\nTimestamp: {timestamp}\n{condensed}",
            id=f"compaction-condense-{uuid.uuid4()}",
        )
    
    return final
```

### Chunked Summarization (CRIT-3 Fix)

**Problem**: If compactable messages are very large (that's WHY compaction triggered), sending all of them to the summarization LLM could itself exceed the context window.

**Solution**: If compactable messages exceed `summarization_chunk_threshold` fraction of context window, split into batches. Summarize each batch separately, then merge into a final summary.

```python
async def _summarize_chunked(
    self,
    compactable_groups: list[MessageGroup],
    context: CompactionContext,
) -> list[SystemMessage]:
    """Summarize compactable groups in batches if they're too large.
    
    If the compactable messages exceed summarization_chunk_threshold of the
    context window, we split into batches and summarize each batch separately.
    The partial summaries are then merged into a final comprehensive summary.
    """
    from daemon.loader import estimate_messages_tokens
    
    compactable_messages = [msg for g in compactable_groups for msg in g.messages]
    compactable_tokens = estimate_messages_tokens(compactable_messages)
    context_window = get_model_context_limit(context.model_name, context.config)
    
    threshold_tokens = context_window * context.config.summarization_chunk_threshold
    
    if compactable_tokens <= threshold_tokens:
        # Fits in one call — use single summarization
        summary = await self._summarize_single_batch(compactable_groups, context)
        return [summary]
    
    # Too large — chunk into batches (~20 groups per batch)
    batch_size = 20
    batches = []
    
    for i in range(0, len(compactable_groups), batch_size):
        batch_groups = compactable_groups[i:i + batch_size]
        batch_msgs = [msg for g in batch_groups for msg in g.messages]
        batch_tokens = estimate_messages_tokens(batch_msgs)
        
        if batch_tokens > threshold_tokens:
            # Even a single batch is too large — truncate to fit
            batch = self._truncate_batch_to_fit(batch_groups, threshold_tokens, context)
            batches.append(batch)
        else:
            batches.append(batch_groups)
    
    # Summarize each batch
    partial_summaries = []
    for batch in batches:
        partial = await self._summarize_single_batch(batch, context)
        partial_summaries.append(partial)
    
    # Merge partial summaries if multiple batches
    if len(partial_summaries) == 1:
        return partial_summaries
    
    return [await self._merge_summaries(partial_summaries, context)]
```

### Summary Message Format (WARN-1)

Use `SystemMessage` instead of `AIMessage` to clearly separate the summary from conversational messages:

```python
from langchain_core.messages import SystemMessage
from datetime import datetime, timezone

timestamp = datetime.now(timezone.utc).isoformat()
summary_content = f"""[Conversation Summary]
Timestamp: {timestamp}
<LLM-generated summary text>
"""

summary = SystemMessage(content=summary_content, id=f"compaction-{uuid.uuid4()}")
```

The `[Conversation Summary]` marker makes it unambiguous that this is not a conversational response.

### State Replacement via RemoveMessage (CRIT-1 Fix)

**Critical**: `MessagesState` uses `add_messages` reducer which is append-only. Passing a raw list to `aupdate_state` will CONCATENATE, not replace. This was verified experimentally.

**Correct approach**: Use `RemoveMessage` sentinels to delete old messages by ID, then add the summary.

```python
from langchain_core.messages import RemoveMessage, SystemMessage

def _build_replacement_messages(
    compactable_groups: list[MessageGroup],
    preserved_groups: list[MessageGroup],
    summary: SystemMessage,
) -> list[BaseMessage]:
    """Build the complete replacement message list for aupdate_state.
    
    Returns: RemoveMessage sentinels for old messages + summary + preserved messages.
    This is the complete new state["messages"] list.
    """
    replacement = []
    
    # Step 1: Remove all compactable messages by ID
    for group in compactable_groups:
        for msg in group.messages:
            if msg.id:
                replacement.append(RemoveMessage(id=msg.id))
    
    # Step 2: Add summary message
    replacement.append(summary)
    
    # Step 3: Add all preserved messages (in original order)
    for group in preserved_groups:
        replacement.extend(group.messages)
    
    return replacement
```

This was **verified experimentally** — `RemoveMessage` correctly deletes messages by ID from the checkpointed state. The graph continues working correctly after compaction.

### Main Entry Point: `compact_state()`

```python
async def compact_state(
    self,
    context: CompactionContext
) -> CompactionResult | None:
    """Check if compaction needed and perform it.
    
    Returns None if no compaction was performed (tokens below threshold,
    or recently compacted — dedup check via last_compacted_at).
    """
    # 1. Dedup check: skip if recently compacted (WARN-2)
    if context.last_compacted_at:
        if self._is_recently_compacted(context.last_compacted_at):
            return None
    
    # 2. Eligibility checks
    if len(context.messages) < context.config.min_messages_before_compaction:
        return None
    
    # 3. Calculate total tokens
    history_tokens = estimate_messages_tokens(context.messages)
    total_tokens = history_tokens + context.system_prompt_tokens
    
    # 4. Get context window and check threshold
    context_window = get_model_context_limit(context.model_name, context.config)
    if total_tokens <= context_window * context.config.threshold:
        return None
    
    logger.info(f"Compaction triggered: {total_tokens} tokens "
                f"(threshold: {int(context_window * context.config.threshold)})")
    
    # 5. Identify boundary groups
    groups = identify_boundary_groups(context.messages)
    
    # 6. Select compactable vs preserved (with progressive reduction)
    compactable, preserved, actual_window = select_compactable_groups(
        groups,
        context.config.recent_message_window,
        context.config.min_recent_window,
        context_window,
        context.system_prompt_tokens,
        estimate_messages_tokens,
    )
    
    if not compactable:
        # REV-CRIT-2: Even after progressive reduction, nothing to summarize.
        # Check if tokens still exceed threshold and apply emergency truncation.
        preserved_msgs = [msg for g in preserved for msg in g.messages]
        preserved_tokens = estimate_messages_tokens(preserved_msgs) + context.system_prompt_tokens
        
        if preserved_tokens <= context_window * context.config.threshold:
            return None  # Under threshold, no action needed
        
        # Emergency truncation on preserved messages
        logger.warning(
            f"Emergency truncation: {preserved_tokens} tokens exceed threshold "
            f"with only {len(preserved)} preserved groups"
        )
        truncated_msgs = emergency_truncate(
            preserved_msgs,
            max_tokens=int(context_window * context.config.target_ratio),
            tokenizer_fn=estimate_messages_tokens,
        )
        # Build replacement: remove ALL original messages, add truncated versions
        timestamp = datetime.now(timezone.utc).isoformat()
        replacement = []
        for group in groups:  # ALL groups (compactable + preserved)
            for msg in group.messages:
                if msg.id:
                    replacement.append(RemoveMessage(id=msg.id))
        replacement.extend(truncated_msgs)
        
        non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
        tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens
        
        return CompactionResult(
            replacement_messages=replacement,
            tokens_before=total_tokens,
            tokens_after=tokens_after,
            tokens_saved=total_tokens - tokens_after,
            messages_before=len(context.messages),
            messages_after=len(non_removal),
            compaction_type="emergency_truncation",
            compacted_at=timestamp,
        )
    
    # 7. Try summarization (chunked if needed), with truncation fallback
    timestamp = datetime.now(timezone.utc).isoformat()
    
    try:
        summaries = await self._summarize_chunked(compactable, context)
        
        if len(summaries) == 1:
            summary = summaries[0]
        else:
            summary = await self._merge_summaries(summaries, context)
        
        replacement = _build_replacement_messages(compactable, preserved, summary)
        compaction_type = "chunked_summarization" if len(summaries) > 1 else "summarization"
        
    except Exception as e:
        logger.warning(f"Summarization failed, falling back to truncation: {e}")
        replacement, compaction_type = self._truncate_fallback(
            compactable, preserved, context
        )
    
    # 8. Calculate metrics
    non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
    tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens
    messages_after = len(non_removal)
    
    return CompactionResult(
        replacement_messages=replacement,
        tokens_before=total_tokens,
        tokens_after=tokens_after,
        tokens_saved=total_tokens - tokens_after,
        messages_before=len(context.messages),
        messages_after=messages_after,
        compaction_type=compaction_type,
        compacted_at=timestamp,
    )
```

## Constraints

- `identify_boundary_groups()` must correctly handle ALL message patterns that appear in the graph
- Progressive window reduction must terminate at `min_window` (hard floor)
- **Emergency truncation must ensure termination** — even if all messages exceed threshold, individual message truncation guarantees tokens fit (REV-CRIT-2)
- Chunked summarization must not overflow even on worst case (50+ tool messages in one batch)
- `_truncate_batch_to_fit()` must always keep at least 1 group in each batch (REV-CRIT-2)
- `_merge_summaries()` must handle 2-N partial summaries with hierarchical merge for 4+ (REV-CRIT-2)
- Truncation fallback must still preserve the minimum recent window
- All async LLM calls must use `asyncio.to_thread()` (consistent with rest of codebase)
- Compaction dedup must be reliable — prevent re-compaction within the same minute
- `RemoveMessage` pattern must be used for ALL state replacement — never raw message lists
- **Threshold must be configurable** — no hardcoded `0.80` (REV-WARN-1)

## Deliverables

- [ ] `CompactionContext` and `CompactionResult` dataclasses in `daemon/compaction.py`
- [ ] `MessageGroup` dataclass and `identify_boundary_groups()` function
- [ ] `select_compactable_groups()` with progressive window reduction (configurable threshold — REV-WARN-1)
- [ ] `emergency_truncate()` last-resort individual message truncation (REV-CRIT-2)
- [ ] `_truncate_batch_to_fit()` batch truncation for summarization (REV-CRIT-2)
- [ ] `_merge_summaries()` hierarchical partial summary merging (REV-CRIT-2)
- [ ] `_summarize_chunked()` with batch splitting and merge
- [ ] `_build_replacement_messages()` using `RemoveMessage` pattern
- [ ] `_truncate_fallback()` using `RemoveMessage` pattern
- [ ] `_is_recently_compacted()` dedup check
- [ ] `compact_state()` main entry point with emergency truncation integration
- [ ] Comprehensive unit tests for all functions (boundary detection, progressive reduction, emergency truncation, merge, RemoveMessage pattern)
