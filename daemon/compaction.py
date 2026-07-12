"""Context compaction engine for managing token limits.

This module provides a complete context window management system that automatically
compacts conversation history when it approaches the model's context limit. The system
uses LLM-based summarization to preserve important context while reducing token usage.

Key components:
- MODEL_CONTEXT_LIMITS: Registry of model context window sizes
- get_model_context_limit(): Lookup function with fuzzy matching
- estimate_tokens(): Token counting via tiktoken (imported from loader)
- CompactionContext: Container for all inputs needed for context compaction
- CompactionResult: Result of a compaction operation
- MessageGroup: Represents an atomic message group that cannot be split
- ContextCompactor: Main compaction engine with summarization and truncation strategies

Compaction Strategies:
1. Summarization: LLM-based summarization of old message groups
2. Chunked Summarization: For large histories, summarizes in batches then merges
3. Truncation: Fallback when summarization fails
4. Emergency Truncation: When even preserved groups exceed threshold
"""


import asyncio
import copy
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from .config import CompactionConfig
from .graph import clean_llm_config
from .loader import estimate_messages_tokens

logger = logging.getLogger(__name__)


def _extract_text_from_content(content: str | list) -> str:
    """Extract text from message content, handling multimodal lists.

    Args:
        content: Message content, either a string or a multimodal list
                 (e.g., [{'type': 'text', 'text': '...'}, {'type': 'image_url', ...}]).

    Returns:
        Extracted text string. For multimodal content, joins all text blocks.
        Skips image_url blocks entirely.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                # Skip image_url and other non-text blocks
        return "".join(text_parts)

    return str(content) if content is not None else ""


def _is_injected_message(msg: BaseMessage) -> bool:
    """Phase 1 / C3: detect a user-injected message by ``additional_kwargs``.

    Mirrors the ``language_check_reminder`` skip pattern at graph.py:493.
    An injected message was deliberately placed into the conversation by
    the user via the injection slot (Phase 1 / C2) and MUST survive any
    compaction pass — both proactive (this module) and reactive
    (graph.py:641-684). Summarizing it would erase user intent.

    Args:
        msg: Candidate ``BaseMessage`` (typically ``HumanMessage``).

    Returns:
        ``True`` when the message is flagged as injected, ``False`` otherwise.
    """
    additional_kwargs = getattr(msg, "additional_kwargs", None)
    if not additional_kwargs:
        return False
    return bool(additional_kwargs.get("injected_message"))


def _partition_injected_messages(
    messages: list[BaseMessage],
) -> tuple[list[BaseMessage], list[BaseMessage]]:
    """Split a message list into ``(non_injected, injected)`` order.

    The returned ``non_injected`` list preserves the relative order of
    the non-injected messages from the input. The ``injected`` list
    preserves the original order of injected messages so they can be
    re-inserted at the end of the replacement list in their original
    sequence. Order of injected messages relative to each other matters
    less than their overall chronological position (preserved here).

    Args:
        messages: Source message list (in conversation order).

    Returns:
        Tuple ``(non_injected, injected)``.
    """
    non_injected: list[BaseMessage] = []
    injected: list[BaseMessage] = []
    for msg in messages:
        if _is_injected_message(msg):
            injected.append(msg)
        else:
            non_injected.append(msg)
    return non_injected, injected


# Context window sizes for known models (in tokens)
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    # OpenAI models
    "gpt-4": 8192,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.5": 128000,
    "gpt-3.5-turbo": 16385,
    "o1": 200000,
    "o1-mini": 128000,
    "o3-mini": 200000,
    
    # Anthropic models (via OpenAI-compatible API)
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3.5-sonnet": 200000,
    "claude-3.5-haiku": 200000,
    "claude-4": 200000,
    
    # Open-source models
    "llama-3": 8192,
    "llama-3.1": 128000,
    "mistral": 32000,
    "mixtral": 32000,
    "deepseek": 128000,
    "qwen": 32768,
}

DEFAULT_CONTEXT_LIMIT = 180000


def get_model_context_limit(model_name: str, config: object | None = None) -> int:
    """Get the context window limit for a given model name.

    Resolution order (first match wins):
    1. ``config.context_window_overrides`` — substring match against the model
       name; longest key wins. Lets operators cap distinct models (e.g. a
       smaller vision model) without touching the registry.
    2. ``MODEL_CONTEXT_LIMITS`` registry — fuzzy substring match (case-insensitive).
    3. ``config.context_window_default`` — used when neither overrides nor the
       registry match. Set to 0 to fall through to the hard-coded fallback.
    4. ``DEFAULT_CONTEXT_LIMIT`` — last-resort fallback.

    Args:
        model_name: Model identifier string (e.g., "gpt-4o", "claude-3.5-sonnet").
            Whitespace is stripped and matching is case-insensitive.
        config: Optional config object exposing ``context_window_overrides``
            (dict[str, int]) and ``context_window_default`` (int). Both are
            optional; missing attributes are treated as empty/zero.

    Returns:
        Context window size in tokens.
    """
    # Normalize once so override and registry matching see the same string.
    normalized = model_name.strip().lower()

    # Per-model overrides take priority (longest key first for specificity)
    if config is not None:
        overrides = getattr(config, "context_window_overrides", None) or {}
        if overrides and normalized:
            for key in sorted(overrides.keys(), key=len, reverse=True):
                if not key:
                    continue
                if key.lower() in normalized:
                    return int(overrides[key])

    # Direct match first
    if normalized in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[normalized]

    # Fuzzy match: check if any registry key is contained in the model name
    # Check longer keys first to get more specific matches
    for key in sorted(MODEL_CONTEXT_LIMITS.keys(), key=len, reverse=True):
        if key in normalized:
            return MODEL_CONTEXT_LIMITS[key]

    # Operator-supplied fallback when registry has no entry
    if config is not None:
        default = getattr(config, "context_window_default", 0)
        if default and default > 0:
            return int(default)

    return DEFAULT_CONTEXT_LIMIT


# =============================================================================
# Phase 2: Compaction Engine
# =============================================================================

@dataclass
class CompactionContext:
    """Container for all inputs needed for context compaction.
    
    Attributes:
        messages: List of conversation messages to potentially compact.
        system_prompt_tokens: Token count of the system prompt (excluded from compaction).
        model_name: Model identifier for context window lookup.
        config: Compaction configuration settings.
        llm_config: LLM configuration for summarization calls.
        last_compacted_at: ISO timestamp of last compaction (if any).
    """
    messages: list[BaseMessage]
    system_prompt_tokens: int
    model_name: str
    config: CompactionConfig
    llm_config: dict
    last_compacted_at: str | None = None


@dataclass
class CompactionResult:
    """Result of a compaction operation.
    
    Attributes:
        replacement_messages: List containing RemoveMessage for deleted items
            and the new summary/retained messages.
        tokens_before: Total tokens before compaction.
        tokens_after: Total tokens after compaction (including system prompt).
        tokens_saved: Net tokens saved by compaction.
        messages_before: Number of messages before compaction.
        messages_after: Number of messages after compaction.
        compaction_type: Strategy used ("summarization", "chunked_summarization",
            "truncation", "emergency_truncation").
        summarization_error: Error message if summarization failed.
        compacted_at: ISO timestamp when compaction occurred.
    """
    replacement_messages: list[BaseMessage]
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    messages_before: int
    messages_after: int
    compaction_type: str  # "summarization" | "chunked_summarization" | "truncation" | "emergency_truncation"
    summarization_error: str | None = None
    compacted_at: str | None = None


@dataclass
class MessageGroup:
    """Represents an atomic message group that cannot be split during compaction.
    
    A MessageGroup is either a single message or an AI message followed by
    its related ToolMessages. Groups ensure tool call sequences remain atomic.
    
    Attributes:
        start_idx: Starting index in the original messages list.
        end_idx: Ending index (inclusive) in the original messages list.
        messages: The actual message objects in this group.
        group_type: Either "single" or "tool_sequence".
    """
    start_idx: int
    end_idx: int
    messages: list[BaseMessage]
    group_type: str  # "single" | "tool_sequence"


def identify_boundary_groups(messages: list[BaseMessage]) -> list[MessageGroup]:
    """Group messages into atomic units that cannot be split during compaction.
    
    Messages are grouped as follows:
    - Orphan tool messages become single-message groups
    - AI messages with tool_calls form groups with their corresponding ToolMessages
    - All other messages become single-message groups
    
    Args:
        messages: List of conversation messages to group.
        
    Returns:
        List of MessageGroup objects in chronological order.
    """
    groups: list[MessageGroup] = []
    i = 0
    
    while i < len(messages):
        msg = messages[i]
        msg_type = getattr(msg, "type", "unknown")
        
        if msg_type == "tool":
            # Orphan tool message - single group
            groups.append(MessageGroup(
                start_idx=i,
                end_idx=i,
                messages=[msg],
                group_type="single"
            ))
            i += 1
            continue
        
        if msg_type == "ai" and hasattr(msg, "tool_calls") and msg.tool_calls:
            # AI message with tool calls - collect matching tool responses
            tool_call_ids = set()
            for tc in msg.tool_calls:
                if isinstance(tc, dict):
                    tc_id = tc.get("id", "")
                else:
                    tc_id = getattr(tc, "id", "")
                if tc_id:
                    tool_call_ids.add(tc_id)
            
            # W2: If no valid tool_call_ids, treat as single message
            if not tool_call_ids:
                groups.append(MessageGroup(
                    start_idx=i,
                    end_idx=i,
                    messages=[msg],
                    group_type="single"
                ))
                i += 1
                continue
            
            # Collect following ToolMessages whose tool_call_id matches
            group_messages = [msg]
            group_end = i
            for j in range(i + 1, len(messages)):
                next_msg = messages[j]
                # W1: Use explicit None check to handle empty string tool_call_id
                if hasattr(next_msg, "tool_call_id") and getattr(next_msg, 'tool_call_id', None) is not None:
                    if next_msg.tool_call_id in tool_call_ids:
                        group_messages.append(next_msg)
                        group_end = j
                    else:
                        # Tool message not related to this AI - stop
                        break
                else:
                    # Non-tool message - stop
                    break
            
            groups.append(MessageGroup(
                start_idx=i,
                end_idx=group_end,
                messages=group_messages,
                group_type="tool_sequence"
            ))
            i = group_end + 1
            continue
        
        # Default: single message group
        groups.append(MessageGroup(
            start_idx=i,
            end_idx=i,
            messages=[msg],
            group_type="single"
        ))
        i += 1
    
    return groups


def select_compactable_groups(
    groups: list[MessageGroup],
    recent_window: int,
    min_window: int,
    context_window: int,
    system_prompt_tokens: int,
    estimate_fn: callable,
    config_threshold: float = 0.80
) -> tuple[list[MessageGroup], list[MessageGroup], int]:
    """Select which groups to compact vs preserve using progressive window reduction.
    
    This function iteratively reduces the preserved window size until the total
    token count falls below the threshold, ensuring recent messages are kept intact.
    
    Args:
        groups: All message groups from identify_boundary_groups.
        recent_window: Desired number of recent groups to preserve.
        min_window: Hard minimum number of groups to preserve.
        context_window: Model's context window size in tokens.
        system_prompt_tokens: Token count of system prompt (excluded from compaction).
        estimate_fn: Function to estimate tokens for a message list.
        config_threshold: Fraction of context window that triggers compaction.
        
    Returns:
        Tuple of (compactable_groups, preserved_groups, actual_window_size).
    """
    window = recent_window
    
    while window >= min_window:
        if len(groups) <= window:
            return [], groups, window
        
        preserved = groups[-window:]
        compactable = groups[:-window]
        preserved_tokens = estimate_fn([msg for g in preserved for msg in g.messages])
        total = preserved_tokens + system_prompt_tokens
        threshold = context_window * config_threshold
        
        if total <= threshold:
            return compactable, preserved, window
        
        window -= 1
    
    # Fallback: use minimum window
    preserved = groups[-min_window:]
    compactable = groups[:-min_window] if len(groups) > min_window else []
    return compactable, preserved, min_window


def emergency_truncate(
    messages: list[BaseMessage],
    max_tokens: int,
    estimate_fn: callable,
    max_tool_response_chars: int = 2000,
    max_human_message_chars: int = 4000
) -> list[BaseMessage]:
    """Emergency truncation with 4-pass approach to fit within token limit.
    
    Pass 0: Convert all multimodal content to clean strings
    Pass 1: Truncate tool responses to max_tool_response_chars
    Pass 2: Truncate human messages to max_human_message_chars
    Pass 3: Progressive halving of content > 500 chars until under limit
    
    Args:
        messages: Messages to truncate.
        max_tokens: Target maximum tokens.
        estimate_fn: Function to estimate tokens.
        max_tool_response_chars: Max characters for tool responses.
        max_human_message_chars: Max characters for human messages.
        
    Returns:
        Truncated list of messages (deep copied).
    """
    # Pass 0: Deep copy and convert all multimodal content to clean strings
    truncated = copy.deepcopy(messages)
    for msg in truncated:
        if isinstance(msg.content, list):
            msg.content = _extract_text_from_content(msg.content)
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 1: Truncate tool responses
    for msg in truncated:
        if getattr(msg, "type", "") == "tool":
            content = _extract_text_from_content(msg.content)
            if len(content) > max_tool_response_chars:
                msg.content = content[:max_tool_response_chars] + "\n[...truncated]"
            else:
                msg.content = content  # Ensure string
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 2: Truncate human messages
    for msg in truncated:
        if getattr(msg, "type", "") == "human":
            content = _extract_text_from_content(msg.content)
            if len(content) > max_human_message_chars:
                msg.content = content[:max_human_message_chars] + "\n[...truncated]"
            else:
                msg.content = content  # Ensure string
    
    if estimate_fn(truncated) <= max_tokens:
        return truncated
    
    # Pass 3: Progressive halving of large content
    for msg in truncated:
        content = _extract_text_from_content(msg.content)
        if len(content) > 500:
            while len(content) > 500 and estimate_fn(truncated) > max_tokens:
                half_len = len(content) // 2
                # Find a good break point (end of sentence or line)
                break_point = content.rfind('. ', 0, half_len)
                if break_point == -1:
                    break_point = content.rfind('\n', 0, half_len)
                if break_point == -1:
                    break_point = half_len
                content = content[:break_point + 1] + "\n[...truncated]"
                msg.content = content
            
            if estimate_fn(truncated) <= max_tokens:
                return truncated
    
    # C1: After Pass 3, if still over limit, drop oldest messages as last resort
    while len(truncated) > 1 and estimate_fn(truncated) > max_tokens:
        truncated.pop(0)
    
    return truncated


def _truncate_batch_to_fit(
    batch_groups: list[MessageGroup],
    max_tokens: int,
    tokenizer_fn: callable,
    max_tool_response_chars: int = 2000
) -> list[MessageGroup]:
    """Truncate a batch of groups to fit within token limit.
    
    First converts all multimodal content to strings, then truncates tool responses,
    then drops oldest groups if still over limit.
    
    Args:
        batch_groups: Groups to truncate.
        max_tokens: Target maximum tokens.
        tokenizer_fn: Function to estimate tokens.
        max_tool_response_chars: Max characters for tool responses.
        
    Returns:
        Truncated list of groups (deep copied).
    """
    # Deep copy groups and convert all multimodal content to strings
    truncated_groups = []
    for group in batch_groups:
        group_copy = MessageGroup(
            start_idx=group.start_idx,
            end_idx=group.end_idx,
            messages=copy.deepcopy(group.messages),
            group_type=group.group_type
        )
        
        # Convert all multimodal content to clean strings first
        for msg in group_copy.messages:
            if isinstance(msg.content, list):
                msg.content = _extract_text_from_content(msg.content)
        
        # Truncate tool responses if over limit
        for msg in group_copy.messages:
            if getattr(msg, "type", "") == "tool":
                if len(msg.content) > max_tool_response_chars:
                    msg.content = msg.content[:max_tool_response_chars] + "\n[...truncated]"
        
        truncated_groups.append(group_copy)
    
    # If still over limit, drop oldest groups (keep at least 1)
    while len(truncated_groups) > 1 and tokenizer_fn(
        [msg for g in truncated_groups for msg in g.messages]
    ) > max_tokens:
        truncated_groups.pop(0)
    
    # W3: If single remaining group still exceeds max_tokens, truncate its messages
    # At this point, all content is already converted to strings
    if len(truncated_groups) == 1 and tokenizer_fn(
        [msg for g in truncated_groups for msg in g.messages]
    ) > max_tokens:
        for msg in truncated_groups[0].messages:
            content = getattr(msg, "content", "") or ""
            if len(content) > max_tool_response_chars:
                msg.content = content[:max_tool_response_chars] + "\n[...truncated]"

    return truncated_groups


class ContextCompactor:
    """Main compaction engine that handles context window management.
    
    This class orchestrates the compaction process, including:
    - Eligibility checking (dedup, minimum messages)
    - Token calculation and threshold detection
    - Message grouping and selection
    - LLM-based summarization with chunking
    - Fallback truncation strategies
    
    Usage:
        compactor = ContextCompactor(config, llm_config)
        result = await compactor.compact_state(context)
        if result:
            # Apply result.replacement_messages to LangGraph state
    """
    
    def __init__(self, config: CompactionConfig, llm_config: dict):
        """Initialize the compactor with configuration.
        
        Args:
            config: CompactionConfig with threshold, window, and model settings.
            llm_config: LLM configuration dict for summarization calls.
        """
        self.config = config
        self.llm_config = llm_config
        self.llm_config_with_headers = {
            **llm_config,
            "default_headers": {"x-proxy-app": "ensemble"},
        }
    
    async def compact_state(self, context: CompactionContext) -> CompactionResult | None:
        """Compact conversation history if it exceeds context window threshold.

        Args:
            context: CompactionContext with messages and configuration.

        Returns:
            CompactionResult if compaction occurred, None if not needed.
        """
        # 1. Deduplication: skip if recently compacted
        if context.last_compacted_at and self._is_recently_compacted(context.last_compacted_at):
            logger.debug("Skipping compaction: recently compacted")
            return None

        # C3 / Phase 1: Partition injected messages out of the candidate
        # list. These messages are user-injected HumanMessages that MUST
        # survive compaction — they are deliberate user intent, not
        # summarizable history. We filter once up-front and re-attach
        # them to the result below.
        regular_messages, injected_messages = _partition_injected_messages(
            context.messages
        )

        # If every message is an injection, there is nothing to compact
        # (the injected messages will be left in place by the unchanged
        # conversation state). Bail early.
        if not regular_messages:
            logger.debug(
                "Skipping compaction: every message carries "
                "injected_message flag (n=%d)",
                len(context.messages),
            )
            return None

        # 2. Eligibility: minimum messages check (against the non-injected
        # subset so an injection-heavy conversation doesn't get spuriously
        # compacted away).
        if len(regular_messages) < context.config.min_messages_before_compaction:
            logger.debug(
                f"Skipping compaction: {len(regular_messages)} non-injected messages "
                f"(minimum: {context.config.min_messages_before_compaction}, "
                f"injected={len(injected_messages)})"
            )
            return None

        # 3. Token calculation (regular messages only)
        history_tokens = estimate_messages_tokens(regular_messages)
        total_tokens = history_tokens + context.system_prompt_tokens

        # 4. Context window and threshold check
        context_window = get_model_context_limit(context.model_name, context.config)
        if total_tokens <= context_window * context.config.threshold:
            logger.debug(
                f"Skipping compaction: {total_tokens} tokens "
                f"<= threshold {int(context_window * context.config.threshold)}"
            )
            return None

        logger.info(
            f"Compaction triggered: {total_tokens} tokens "
            f"(threshold: {int(context_window * context.config.threshold)}, "
            f"regular={len(regular_messages)}, injected={len(injected_messages)})"
        )

        # 5. Boundary groups (regular messages only)
        groups = identify_boundary_groups(regular_messages)

        # 6. Select compactable vs preserved
        compactable, preserved, actual_window = select_compactable_groups(
            groups,
            context.config.recent_message_window,
            context.config.min_recent_window,
            context_window,
            context.system_prompt_tokens,
            estimate_messages_tokens,
            config_threshold=context.config.threshold,
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        if not compactable:
            # Emergency path: even preserved groups exceed threshold
            preserved_msgs = [msg for g in preserved for msg in g.messages]
            preserved_tokens = estimate_messages_tokens(preserved_msgs) + context.system_prompt_tokens

            if preserved_tokens <= context_window * context.config.threshold:
                return None

            logger.warning(
                f"Emergency truncation: {preserved_tokens} tokens exceed threshold "
                f"with only {len(preserved)} preserved groups"
            )

            truncated_msgs = emergency_truncate(
                preserved_msgs,
                max_tokens=int(context_window * context.config.target_ratio),
                estimate_fn=estimate_messages_tokens,
            )

            # W6: Assign new IDs to truncated messages to avoid conflict with RemoveMessage
            for truncated_msg in truncated_msgs:
                if hasattr(truncated_msg, 'id') and truncated_msg.id:
                    truncated_msg.id = f"truncated-{uuid.uuid4()}"

            replacement = []
            for group in groups:
                for msg in group.messages:
                    if msg.id:
                        replacement.append(RemoveMessage(id=msg.id))
            replacement.extend(truncated_msgs)
            # C3: re-attach injected messages verbatim at the end so
            # they survive emergency truncation. They were never in
            # ``regular_messages`` so no RemoveMessage applies.
            replacement.extend(injected_messages)

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

        # 7. Summarization path
        try:
            summaries = await self._summarize_chunked(compactable, context)

            # C2: _summarize_chunked always returns a single-element list
            summary = summaries[0]

            replacement = self._build_replacement_messages(compactable, preserved, summary)
            # C3: re-attach injected messages at the end of the
            # replacement list. They are appended AFTER the preserved
            # tail and the summary so their chronological position is
            # honored (the add_messages reducer handles interleaving
            # correctly by ``id``).
            replacement.extend(injected_messages)
            compaction_type = "chunked_summarization" if len(summaries) > 1 else "summarization"

        except Exception as e:
            logger.warning(f"Summarization failed, falling back to truncation: {e}")
            replacement, compaction_type = self._truncate_fallback(compactable, preserved, context)
            # C3: same re-attach on the truncation fallback path
            replacement.extend(injected_messages)

            # Include error info in result
            non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
            tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens
            return CompactionResult(
                replacement_messages=replacement,
                tokens_before=total_tokens,
                tokens_after=tokens_after,
                tokens_saved=total_tokens - tokens_after,
                messages_before=len(context.messages),
                messages_after=len(non_removal),
                compaction_type=compaction_type,
                summarization_error=str(e),
                compacted_at=timestamp,
            )

        # 8. Build result
        non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
        tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens

        logger.info(
            f"Compaction complete: {total_tokens} -> {tokens_after} tokens "
            f"(saved {total_tokens - tokens_after}), type={compaction_type}, "
            f"injected_preserved={len(injected_messages)}"
        )

        return CompactionResult(
            replacement_messages=replacement,
            tokens_before=total_tokens,
            tokens_after=tokens_after,
            tokens_saved=total_tokens - tokens_after,
            messages_before=len(context.messages),
            messages_after=len(non_removal),
            compaction_type=compaction_type,
            compacted_at=timestamp,
        )
    
    async def _summarize_chunked(
        self,
        compactable_groups: list[MessageGroup],
        context: CompactionContext
    ) -> list[SystemMessage]:
        """Summarize compactable groups, chunking if necessary.
        
        Args:
            compactable_groups: Groups to summarize.
            context: Compaction context with configuration.
            
        Returns:
            List of summary SystemMessages.
        """
        compactable_messages = [msg for g in compactable_groups for msg in g.messages]
        compactable_tokens = estimate_messages_tokens(compactable_messages)
        context_window = get_model_context_limit(context.model_name, context.config)
        threshold_tokens = context_window * context.config.summarization_chunk_threshold
        
        # Single batch if small enough
        if compactable_tokens <= threshold_tokens:
            summary = await self._summarize_single_batch(compactable_groups, context)
            return [summary]
        
        # Chunk into batches of 20 groups
        batch_size = 20
        batches: list[list[MessageGroup]] = []
        for i in range(0, len(compactable_groups), batch_size):
            batch_groups = compactable_groups[i:i + batch_size]
            batch_msgs = [msg for g in batch_groups for msg in g.messages]
            batch_tokens = estimate_messages_tokens(batch_msgs)
            
            # Truncate batch if still too large
            if batch_tokens > threshold_tokens:
                batch_groups = _truncate_batch_to_fit(
                    batch_groups,
                    int(threshold_tokens),
                    estimate_messages_tokens
                )
            batches.append(batch_groups)
        
        # Summarize each batch
        partial_summaries: list[SystemMessage] = []
        for batch in batches:
            partial = await self._summarize_single_batch(batch, context)
            partial_summaries.append(partial)
        
        # Merge if multiple summaries
        if len(partial_summaries) == 1:
            return partial_summaries
        
        return [await self._merge_summaries(partial_summaries, context)]
    
    async def _summarize_single_batch(
        self,
        batch_groups: list[MessageGroup],
        context: CompactionContext
    ) -> SystemMessage:
        """Summarize a single batch of message groups.
        
        Args:
            batch_groups: Groups to summarize.
            context: Compaction context.
            
        Returns:
            Summary as SystemMessage.
        """
        # Format messages into readable conversation text
        conversation_parts: list[str] = []
        for group in batch_groups:
            for msg in group.messages:
                msg_type = getattr(msg, "type", "unknown")
                content = _extract_text_from_content(msg.content)
                if msg_type == "human":
                    conversation_parts.append(f"User: {content}")
                elif msg_type == "ai":
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        tool_names = []
                        for tc in msg.tool_calls:
                            if isinstance(tc, dict):
                                name = tc.get("name", "?")
                            else:
                                name = getattr(tc, "name", "?")
                            tool_names.append(name)
                        content += f" [Called tools: {', '.join(tool_names)}]"
                    conversation_parts.append(f"Assistant: {content}")
                elif msg_type == "tool":
                    tool_name = getattr(msg, "name", "unknown")
                    conversation_parts.append(f"Tool ({tool_name}): {content}")
                else:
                    conversation_parts.append(f"{msg_type}: {content}")
        
        conversation_text = "\n".join(conversation_parts)
        
        prompt = (
            "Summarize the following conversation segment. Preserve:\n"
            "- Key decisions made\n"
            "- Important facts and context\n"
            "- Tool actions and their outcomes\n"
            "- User requests and their status\n\n"
            "Be concise but comprehensive. Focus on information that would be needed "
            "to continue the conversation effectively.\n\n"
            f"Conversation:\n{conversation_text}"
        )
        
        summary_text = await self._call_summarization_llm(prompt, context)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        return SystemMessage(
            content=f"[Conversation Summary]\nTimestamp: {timestamp}\n{summary_text}",
            id=f"compaction-{uuid.uuid4()}",
        )
    
    async def _merge_summaries(
        self,
        partial_summaries: list[SystemMessage],
        context: CompactionContext
    ) -> SystemMessage:
        """Merge multiple summaries into one.
        
        Uses pairwise hierarchical merging for 4+ summaries,
        direct merge for 2-3 summaries.
        
        Args:
            partial_summaries: List of summary SystemMessages to merge.
            context: Compaction context.
            
        Returns:
            Merged summary as SystemMessage.
        """
        if len(partial_summaries) == 1:
            return partial_summaries[0]
        
        # Direct merge for 2-3 summaries
        if len(partial_summaries) <= 3:
            combined = "\n\n---\n\n".join(
                f"Part {i+1}:\n{s.content}" for i, s in enumerate(partial_summaries)
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
        
        # Hierarchical pairwise merge for 4+ summaries
        while len(partial_summaries) > 3:
            next_round: list[SystemMessage] = []
            for i in range(0, len(partial_summaries), 2):
                pair = partial_summaries[i:i + 2]
                if len(pair) == 2:
                    merged = await self._merge_summaries(pair, context)
                    next_round.append(merged)
                else:
                    next_round.append(pair[0])
            partial_summaries = next_round
        
        final = await self._merge_summaries(partial_summaries, context)
        
        # Size check: condense if too large
        final_tokens = estimate_messages_tokens([final])
        context_window = get_model_context_limit(context.model_name, context.config)
        max_summary_tokens = context_window * 0.10
        
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
    
    async def _call_summarization_llm(
        self,
        prompt: str,
        context: CompactionContext
    ) -> str:
        """Call LLM for summarization.
        
        Args:
            prompt: Summarization prompt.
            context: Compaction context with model info.
            
        Returns:
            LLM response content as string.
        """
        from .graph import ThinkingChatOpenAI
        
        # Use summarization model override if set, otherwise use session model
        if context.config.summarization_model:
            llm_config = {
                **self.llm_config_with_headers,
                "model": context.config.summarization_model,
            }
        else:
            llm_config = self.llm_config_with_headers

        # Strip model_vision — compaction summarization is text-only, vision model is irrelevant
        llm_config = clean_llm_config(llm_config)

        llm = ThinkingChatOpenAI(**llm_config)
        
        response = await asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(
                    content="You are a helpful assistant that summarizes conversations "
                    "concisely while preserving all important details."
                ),
                HumanMessage(content=prompt),
            ],
        )
        
        content = response.content
        return _extract_text_from_content(content)
    
    @staticmethod
    def _build_replacement_messages(
        compactable_groups: list[MessageGroup],
        preserved_groups: list[MessageGroup],
        summary: SystemMessage
    ) -> list[BaseMessage]:
        """Build replacement message list with RemoveMessage for old content.
        
        Args:
            compactable_groups: Groups being summarized.
            preserved_groups: Groups being kept intact.
            summary: Summary SystemMessage to insert.
            
        Returns:
            List with RemoveMessage for old content and new summary + preserved.
        """
        replacement: list[BaseMessage] = []
        
        # Add RemoveMessage for compactable groups
        for group in compactable_groups:
            for msg in group.messages:
                if msg.id:
                    replacement.append(RemoveMessage(id=msg.id))
        
        # Add summary
        replacement.append(summary)
        
        # Add preserved groups with multimodal content converted to strings
        for group in preserved_groups:
            for msg in group.messages:
                # Convert multimodal content to clean string
                if isinstance(msg.content, list):
                    msg.content = _extract_text_from_content(msg.content)
                replacement.append(msg)
        
        return replacement
    
    def _truncate_fallback(
        self,
        compactable: list[MessageGroup],
        preserved: list[MessageGroup],
        context: CompactionContext
    ) -> tuple[list[BaseMessage], str]:
        """Fallback truncation when summarization fails.
        
        Args:
            compactable: Groups that would have been summarized.
            preserved: Groups being kept intact.
            context: Compaction context.
            
        Returns:
            Tuple of (replacement_messages, compaction_type).
        """
        replacement: list[BaseMessage] = []
        
        for group in compactable:
            for msg in group.messages:
                if msg.id:
                    replacement.append(RemoveMessage(id=msg.id))
        
        for group in preserved:
            for msg in group.messages:
                # Convert multimodal content to clean string
                if isinstance(msg.content, list):
                    msg.content = _extract_text_from_content(msg.content)
                replacement.append(msg)
        
        return replacement, "truncation"
    
    @staticmethod
    def _is_recently_compacted(last_compacted_at: str) -> bool:
        """Check if compaction occurred recently (within 60 seconds).
        
        Args:
            last_compacted_at: ISO timestamp string.
            
        Returns:
            True if compaction was within last 60 seconds.
        """
        try:
            last_time = datetime.fromisoformat(last_compacted_at)
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (now - last_time).total_seconds() < 60
        except (ValueError, TypeError):
            return False
