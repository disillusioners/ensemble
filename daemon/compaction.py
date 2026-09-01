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


# Phase 1 / WS-4.1 (Q5 DECIDED + post-review adjudication C1):
# Module-scope marker helper. Both construction paths MUST reach the SAME
# helper so the engine emits exactly one marker per result (the two paths
# are mutually exclusive per result — partial-summary assembly goes here
# when ``|S| ≥ 1``; full-truncate fallback goes here when ``|S| = 0``).
# APPROVER PIN (2026-08-31): module-scope so neither path can fork a
# divergent copy and double-stamp a marker line. The id-deterministic
# prefix ``truncation-marker-`` lets ``add_messages`` reducer de-dup on
# re-compaction; a freshly-generated UUID4 per call is fine because the
# id remains stable for that result and the synthetic system message
# prepend (persistence.py:404-449) is unaffected.
def _append_truncation_marker(replacement: list) -> None:
    """Append the truncation marker to a replacement list (in-place).

    The marker is a ``SystemMessage`` carrying a short, fixed line that
    tells downstream consumers the older history has been trimmed rather
    than summarized.

    W-4.3 — the id format is ``truncation-marker-<uuid4>`` and is
    freshly minted per call. The marker is NOT intended to be
    id-deterministic across re-compaction runs; the dedup property is
    that within a single construction path (one result), the marker
    fires AT MOST once (this helper appends exactly one). The two
    construction paths (``_truncate_fallback`` and
    ``_build_partial_replacement_messages``) are mutually exclusive
    per result (the partial-summary path requires ``|S| >= 1`` from
    ``_summarize_chunked``; the truncate fallback fires when
    ``|S| = 0``) — so exactly one marker fires per
    ``CompactionResult`` regardless of construction path.

    Args:
        replacement: The mutable replacement message list to append to.
            Mutated in place; nothing is returned.
    """
    replacement.append(
        SystemMessage(
            content="[Earlier messages trimmed to fit context]",
            id=f"truncation-marker-{uuid.uuid4()}",
        )
    )


# Phase 1 / WS-3.1: shared adaptive-timeout formula for the three LLM
# call origins (single-batch :900, merge :939, condense :971 —
# pre-feature the inline expression was duplicated three times; the
# helper consolidates it to a single source of truth). The plan REJECTS
# using ``context.messages`` as the input (architect §3 Correction 1)
# — that would over-estimate every call after the first chunk and
# massively over-estimate merge/condense. Input MUST be the prompt
# actually being sent at the call site.
def _summarization_timeout_s(prompt: str, config: CompactionConfig) -> float:
    """Adaptive per-call LLM timeout for summarization calls.

    Formula:
        ``min(timeout_cap_s, timeout_base_s + (tokens/100_000) * timeout_per_100k_tokens_s)``

    Args:
        prompt: The exact prompt string the caller is about to send. Sized
            via ``estimate_messages_tokens`` (loader.py:465, tiktoken
            cl100k_base) wrapped in a single ``HumanMessage`` so the
            per-message overhead matches the actual payload. NOT
            ``context.messages`` — that over-estimates and breaks
            merge/condense timeouts (architect §3 Correction 1).
        config: Active ``CompactionConfig`` carrying the adaptive knobs.

    Returns:
        Per-call timeout in seconds. Capped at ``config.timeout_cap_s``.
    """
    tokens = estimate_messages_tokens([HumanMessage(content=prompt)])
    return min(
        config.timeout_cap_s,
        config.timeout_base_s + (tokens / 100_000) * config.timeout_per_100k_tokens_s,
    )


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


def resolve_compaction_model(config: CompactionConfig) -> str:
    """Effective compaction-model override from a :class:`CompactionConfig`.

    Precedence: ``config.model`` (canonical, env ``COMPACTION_MODEL`` /
    yaml ``compaction.model`` — env>yaml resolved in ``load_config``)
    → ``config.summarization_model`` (legacy alias, honored for
    backwards compatibility) → ``""`` (no override: session-model
    accessor + ``context_window_overrides``, the pre-existing behavior).

    Pure function of the config object — the parallel summarization pool
    calls this per batch (``_call_summarization_llm``), so every
    concurrent batch call resolves the SAME override with no shared
    mutable state. The empty-string result is falsy by design: callers
    branch on truthiness ("override active") exactly as the legacy
    ``summarization_model`` check did.
    """
    return config.model or config.summarization_model


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
            "truncation", "partial_summary", "emergency_truncation").
        summarization_error: Error message if summarization failed.
        compacted_at: ISO timestamp when compaction occurred.
        forced: True when ``ContextCompactor.compact_state`` was invoked with
            ``force=True`` (WS-2 / architect §2 — only the threshold bypass is
            exposed; dedup + min-messages still apply). Additive default —
            existing construction sites continue to work unchanged, and the
            auto paths (proactive / reactive) emit ``forced=False`` by
            construction (S-7 anti-drift).
        failure_kind: When summarization fails mid-run, this is set to
            ``"timeout"`` (TimeoutError / asyncio.TimeoutError caught per
            WS-3.4 narrowing) or ``"error"`` (other exception). ``None`` on
            the success path. The executor maps ``failure_kind="timeout"``
            to the ``timed_out → fallback_applied`` SSE phases (WS-4 §7
            amendment).
    """
    replacement_messages: list[BaseMessage]
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    messages_before: int
    messages_after: int
    compaction_type: str  # "summarization" | "chunked_summarization" | "truncation" | "partial_summary" | "emergency_truncation"
    summarization_error: str | None = None
    compacted_at: str | None = None
    forced: bool = False  # Phase 1 / WS-2: set by compact_state when force=True
    failure_kind: str | None = None  # Phase 1 / WS-3: "timeout" | "error" | None


@dataclass
class ChunkedOutcome:
    """Phase 1 / WS-3.4 typed return for ``ContextCompactor._summarize_chunked``.

    Attributes:
        summaries: Successful per-batch ``SystemMessage`` summaries in order.
            May be empty (all batches failed) — caller branches on this
            (WS-3.4 binding: ``|S| = 0`` → existing truncate fallback;
            ``|S| ≥ 1`` → partial-summary assembly).
        failed_batches: 0-based batch indices that did NOT produce a summary
            (either timed out per the WS-3.4 narrowing, or were skipped due
            to budget exhaustion). Length equals ``len(batches) - len(summaries)``
            after the loop terminates; partials are tracked for observability
            but the engine already encodes the stop semantics in
            ``stop_reason``.
        stop_reason: ``"completed"`` if all batches succeeded;
            ``"timeout"`` if a per-chunk TimeoutError tripped;
            ``"budget"`` if the whole-operation budget exhausted before the
            remaining batches could be issued;
            ``"error"`` if a non-timeout exception escaped per-chunk (outer
            handler at ``compact_state`` :744-772 still catches it via the
            broader ``except Exception`` for the fallback mapping).
    """
    summaries: list
    failed_batches: list
    stop_reason: str  # "completed" | "timeout" | "error" | "budget"


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
            "default_headers": {
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
                # X-LLMProxy-Buffer-Response: sent by default; omitted
                # entirely (never "false") when buffer_response_header is
                # disabled in the config dict. Default-on for dicts
                # lacking the key (older configs).
                **(
                    {"X-LLMProxy-Buffer-Response": "true"}
                    if llm_config.get("buffer_response_header", True)
                    else {}
                ),
            },
        }

    def _effective_model_name(self, context: CompactionContext) -> str:
        """Model name for context-WINDOW math.

        When a compaction-model override is active
        (:func:`resolve_compaction_model`), token/window math follows the
        OVERRIDE model's context window — thresholds, chunking, and
        summary sizing all scale to the model that will actually serve
        the summarization calls. ``context_window_overrides`` /
        ``context_window_default`` apply to that name exactly as they
        did for the session model (see :func:`get_model_context_limit`).
        Unset override → the session model (``context.model_name``),
        byte-identical with the pre-setting behavior.
        """
        return resolve_compaction_model(context.config) or context.model_name

    def _trigger_window(self, context: CompactionContext) -> int:
        """Context window for the AUTO-path threshold gate (:826-841).

        W1 (review fix): when a compaction-model override is active,
        gate at ``min(session_window, override_window)`` so a LARGER
        override window cannot push proactive compaction past session
        capacity (defeating CLE auto-recovery — force=False reactive
        compaction returns None on context-length error). Internal
        sizing (chunk batching, merge, condense — :1094, :1414)
        continues to follow the OVERRIDE window via
        ``_effective_model_name``; this helper is the TRIGGER side only.

        One-shot WARN per compactor instance when the override window
        exceeds the session window; the message states the gating
        consequence so operators can pre-empt the surprise. Fires at
        the gate site (not ``load_config``) so the operator sees BOTH
        windows in the same log line, and so it is testable without
        loading the daemon config.
        """
        if not resolve_compaction_model(context.config):
            return get_model_context_limit(
                context.model_name, context.config
            )
        override_name = resolve_compaction_model(context.config)
        override_window = get_model_context_limit(
            override_name, context.config
        )
        session_window = get_model_context_limit(
            context.model_name, context.config
        )
        if override_window > session_window:
            if not getattr(self, "_w_overflow_warned", False):
                self._w_overflow_warned = True
                logger.warning(
                    "Compaction override '%s' window (%d) exceeds session "
                    "model '%s' window (%d). Auto-path threshold gated at "
                    "the session window; internal chunking/merge/condense "
                    "sizing still follow the override. Use /compact to "
                    "force-recover once the session model has overflowed.",
                    override_name,
                    override_window,
                    context.model_name,
                    session_window,
                )
            return session_window
        return override_window

    async def compact_state(
        self,
        context: CompactionContext,
        force: bool = False,
    ) -> CompactionResult | None:
        """Compact conversation history if it exceeds context window threshold.

        Args:
            context: CompactionContext with messages and configuration.
            force: Phase 1 / WS-2 (architect §2 narrowed). When True, the
                THRESHOLD check (:765) is bypassed — that is the ONLY
                bypass. Min-messages (:751) and the 60s dedup (:724-726)
                stay in-engine and STILL APPLY under force. Never bypasses
                boundary groups (D2), D3 sentinel persistence, pairing
                guard, or terminal guard. Default ``False`` → automatic
                paths (proactive `instance_messaging.py:1179`, reactive
                `graph.py:3513`) byte-identical when callers do not pass
                the flag (S-7 anti-drift). ``forced`` is stamped on the
                result so callers can distinguish forced compactions.

        Returns:
            CompactionResult if compaction occurred, None if not needed.
            ``compaction_type`` ∈ ``{"summarization", "truncation",
            "partial_summary", "emergency_truncation"}``.
            ``failure_kind`` ∈ ``{None, "timeout", "error"}`` on the
            engine result (WS-3.4 binding).
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

        # 4. Context window and threshold check.
        # Phase 1 / WS-2: ``force=True`` bypasses THIS check ONLY (architect
        # §2 narrowed from the broader dedup+min-messages+threshold form).
        # Min-messages (:751 above) and the 60s dedup (:724-726 above)
        # stay in-engine and STILL APPLY under force. Auto paths do not
        # pass ``force`` so their threshold check is unchanged when
        # ``force=False`` (S-7 byte-identity anti-drift).
        # W1 (review fix): gate the AUTO-path threshold at the SMALLER
        # of session vs override window — see :meth:`_trigger_window`.
        # The threshold check below uses that gated value; the engine's
        # INTERNAL sizing (:1094 chunking, :1414 merge/condense) keeps
        # following the OVERRIDE window via ``_effective_model_name``.
        context_window = self._trigger_window(context)
        if not force and total_tokens <= context_window * context.config.threshold:
            logger.debug(
                f"Skipping compaction: {total_tokens} tokens "
                f"<= threshold {int(context_window * context.config.threshold)}"
            )
            return None

        logger.info(
            f"Compaction triggered: {total_tokens} tokens "
            f"(threshold: {int(context_window * context.config.threshold)}, "
            f"force={force}, regular={len(regular_messages)}, "
            f"injected={len(injected_messages)})"
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

        # 7. Summarization path.
        # Phase 1 / WS-3.4 (C1 hybrid — binding): branch on
        # ``outcome.summaries`` empty vs non-empty; identical semantics
        # for proactive and reactive callers (no per-caller branching —
        # WS-3.4 binding). The marker (WS-4.1) is appended exactly once
        # by whichever path takes over: ``_truncate_fallback`` for
        # |S|=0, the partial-assembly helper for |S|>=1 with
        # stop_reason ∈ {timeout, budget}.
        failure_kind: str | None = None
        summarization_error: str | None = None
        try:
            outcome = await self._summarize_chunked(compactable, context)
            summaries = outcome.summaries

            if not summaries:
                # |S| = 0 — all batches failed (single-batch timeout,
                # multi-batch first-batch timeout, or budget exhausted
                # before any batch succeeded). Existing
                # ``_truncate_fallback`` fires unchanged; the marker is
                # appended in that helper so this path still emits
                # exactly one (compaction_type="truncation").
                replacement, compaction_type = self._truncate_fallback(
                    compactable, preserved, context
                )
                # C3: re-attach injected messages verbatim at the end
                # so they survive truncation.
                replacement.extend(injected_messages)
                if outcome.stop_reason in ("timeout", "budget"):
                    failure_kind = "timeout"
                else:
                    failure_kind = "error"
            elif outcome.stop_reason == "completed":
                # All batches succeeded → single merged or single-batch
                # summary. ``_summarize_chunked`` collapses the merge to
                # one entry on the success path.
                replacement = self._build_replacement_messages(
                    compactable, preserved, summaries[0]
                )
                # C3: re-attach injected messages at the end of the
                # replacement list.
                replacement.extend(injected_messages)
                compaction_type = "summarization"
                failure_kind = None
            else:
                # Partial-summary path: |S| >= 1, stop_reason ∈
                # {"timeout", "budget"}. Per WS-3.4 binding, B's messages
                # are DROPPED — true trim of the un-summarized span.
                # Marker appended between summaries and preserved tail
                # via the partial-assembly helper (WS-4.1 exactly-once).
                replacement = self._build_partial_replacement_messages(
                    compactable, preserved, summaries
                )
                # C3: re-attach injected messages at the end.
                replacement.extend(injected_messages)
                compaction_type = "partial_summary"
                failure_kind = "timeout"

        except (TimeoutError, asyncio.TimeoutError) as e:
            # W-4.1 — merge/condense path can surface a
            # ``TimeoutError`` / ``asyncio.TimeoutError`` that the
            # inner per-chunk narrowing (O14) DOES NOT catch (those
            # exceptions live outside ``_summarize_chunked``). Without
            # this branch, the outer ``except Exception`` catches them
            # and the engine emits ``failure_kind="error"`` — masking
            # a real timeout as a generic error and misclassifying
            # the wire outcome (FE would see "failed" instead of
            # "timed_out → fallback_applied"). The truncate fallback
            # still applies (preserves the auto-path contract); only
            # the classification differs.
            logger.warning(
                "Summarization timed out (merge/condense path), falling "
                "back to truncation: %s",
                e,
            )
            replacement, compaction_type = self._truncate_fallback(
                compactable, preserved, context
            )
            # C3: same re-attach on the truncation fallback path.
            replacement.extend(injected_messages)
            failure_kind = "timeout"
            summarization_error = f"{type(e).__name__}: {e}"
        except Exception as e:
            # Non-timeout exceptions from ``_summarize_chunked`` (O14
            # narrowed per-chunk except) or from merge/condense surface
            # here. ``_truncate_fallback`` applies — marker is appended
            # inside that helper.
            logger.warning(f"Summarization failed, falling back to truncation: {e}")
            replacement, compaction_type = self._truncate_fallback(
                compactable, preserved, context
            )
            # C3: same re-attach on the truncation fallback path.
            replacement.extend(injected_messages)
            failure_kind = "error"
            summarization_error = str(e)

        # 8. Build result — covers summarization, partial_summary, and
        # truncation fallback. ``compacted_at`` is stamped on every
        # branch above (D12 — a partial is a completed compaction, not
        # a failure).
        non_removal = [m for m in replacement if not isinstance(m, RemoveMessage)]
        tokens_after = estimate_messages_tokens(non_removal) + context.system_prompt_tokens

        logger.info(
            f"Compaction complete: {total_tokens} -> {tokens_after} tokens "
            f"(saved {total_tokens - tokens_after}), type={compaction_type}, "
            f"forced={force}, failure_kind={failure_kind}, "
            f"injected_preserved={len(injected_messages)}"
        )

        result_kwargs: dict = dict(
            replacement_messages=replacement,
            tokens_before=total_tokens,
            tokens_after=tokens_after,
            tokens_saved=total_tokens - tokens_after,
            messages_before=len(context.messages),
            messages_after=len(non_removal),
            compaction_type=compaction_type,
            compacted_at=timestamp,
            forced=force,
            failure_kind=failure_kind,
        )
        if summarization_error:
            result_kwargs["summarization_error"] = summarization_error

        return CompactionResult(**result_kwargs)
    
    async def _summarize_chunked(
        self,
        compactable_groups: list[MessageGroup],
        context: CompactionContext,
    ) -> "ChunkedOutcome":
        """Summarize compactable groups, chunking if necessary.

        Phase 1 / WS-3.4 (C1 hybrid): returns a ``ChunkedOutcome`` instead
        of raising on per-chunk failure. The outer handler at
        ``compact_state`` :744-772 branches on ``summaries`` empty vs
        non-empty — identical semantics for proactive (WS-3.5 instance_messaging.py:1179)
        and reactive (WS-3.5 graph.py:3513) callers by construction.

        Per-batch try/except is narrowed to
        ``(TimeoutError, asyncio.TimeoutError)`` (O14) INSIDE each pool
        task; other exceptions are parked by the gather
        (``return_exceptions=True``) and re-raised once the pool joins —
        they propagate to the outer ``except Exception`` (compact_state
        :744-772), which maps them to the existing truncate fallback and
        emits ``failure_kind="error"`` on the engine result.

        Whole-operation budget ``context.config.operation_budget_s``:
        shared wall-clock deadline (``asyncio.wait_for`` around the
        batch-pool gather). The deadline lives entirely inside
        ``_summarize_chunked`` — never between the two
        ``aupdate_state`` persistence calls in callers (D-B5/D-B6 —
        torn-write guard, that lives upstream). Expiry cancels in-flight
        and un-started batch tasks, records ``stop_reason="budget"``,
        and the engine returns with whatever summaries had completed;
        the outer handler decides the path.

        Parallelism (bounded pool): batches are INDEPENDENT — the
        per-batch prompt is a static template over that batch's groups
        only, and ``_merge_summaries`` consumes results strictly after
        the pool. ``chunk_concurrency`` (default 3) bounds in-flight
        calls via ``asyncio.Semaphore``; results are reassembled by
        task-list index (``asyncio.gather`` preserves input order —
        NEVER ``as_completed``), which is the chronological invariant
        ``_build_partial_replacement_messages`` relies on. The existing
        per-prompt adaptive timeout (``_summarization_timeout_s``)
        applies per task and composes with the pool as the per-batch
        failure boundary.

        Args:
            compactable_groups: Groups to summarize.
            context: Compaction context with configuration.

        Returns:
            ``ChunkedOutcome(summaries, failed_batches, stop_reason)``.
            ``stop_reason`` ∈ ``{"completed","timeout","error","budget"}``.
        """
        compactable_messages = [msg for g in compactable_groups for msg in g.messages]
        compactable_tokens = estimate_messages_tokens(compactable_messages)
        context_window = get_model_context_limit(
            self._effective_model_name(context), context.config
        )
        threshold_tokens = context_window * context.config.summarization_chunk_threshold

        # Whole-operation budget wall-clock anchor — measured against the
        # pool gather ONLY (``asyncio.wait_for(pool, ...)`` at ~:1218).
        # The budget does NOT wrap merge/condense — those run AFTER the
        # deadline has fired, serially, as part of the same
        # ``_summarize_chunked`` call frame but with no wall-clock cap
        # of their own. Parallel merge is intentionally deferred to a
        # future soak (Phase-1 design: chunking parallel; post-pool
        # serial). Module-level ``time.monotonic`` is monotonic across
        # the event loop and unaffected by wall-clock skew.
        import time as _time
        budget_started_at = _time.monotonic()
        budget_seconds = float(context.config.operation_budget_s)

        def _budget_remaining() -> float:
            return budget_seconds - (_time.monotonic() - budget_started_at)

        # Single batch if small enough
        if compactable_tokens <= threshold_tokens:
            try:
                summary = await self._summarize_single_batch(
                    compactable_groups, context
                )
                return ChunkedOutcome(
                    summaries=[summary],
                    failed_batches=[],
                    stop_reason="completed",
                )
            except (TimeoutError, asyncio.TimeoutError):
                # O14-narrowed per-chunk timeout. Outer handler maps
                # empty-summaries → truncate fallback (compaction_type
                # "truncation" + marker).
                logger.warning(
                    "Single-batch summarization timed out within "
                    "context.config.operation_budget_s=%ss",
                    context.config.operation_budget_s,
                )
                return ChunkedOutcome(
                    summaries=[],
                    failed_batches=[0],
                    stop_reason="timeout",
                )

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
                    estimate_messages_tokens,
                )
            batches.append(batch_groups)

        # Summarize batches in a bounded parallel pool. Batches are
        # INDEPENDENT: the per-batch prompt is a static template over
        # that batch's groups only (``_summarize_single_batch``) and
        # ``_merge_summaries`` consumes the results strictly AFTER the
        # pool completes — nothing inside the pool reads a prior
        # batch's output. Results are reassembled BY TASK-LIST INDEX:
        # ``asyncio.gather`` preserves input order, which IS the
        # chronological invariant ``_build_partial_replacement_messages``
        # relies on. NEVER ``as_completed`` here.
        #
        # FailoverController race note (parallel-429 review): every
        # batch call constructs its own ``ThinkingChatOpenAI`` +
        # ``wrap_langchain_failover`` wrapper inside
        # ``_call_summarization_llm`` — a fresh ``FailoverController``
        # and a fresh openai client per call — so concurrent
        # 429-driven ``swap_to_backup`` / ``reset_to_primary``
        # mutations never share mutable state across batches. No
        # cross-batch race by construction; no lock needed.
        chunk_concurrency = max(1, int(context.config.chunk_concurrency))
        semaphore = asyncio.Semaphore(chunk_concurrency)

        # Slot i of each structure is batch i. ``summaries_by_idx`` holds
        # completed summaries (``None`` = not completed); ``started``
        # flags batches whose task ACQUIRED a pool slot (i.e. actually
        # began its LLM call). Observability contract for
        # ``failed_batches`` (a member = that batch did not complete):
        #   - "skipped": the task never acquired the semaphore before
        #     the shared deadline cancelled the pool (never started);
        #   - "failed": the task started and then hit its own per-batch
        #     adaptive timeout, or was cancelled in-flight by the
        #     deadline.
        summaries_by_idx: list = [None] * len(batches)
        started = [False] * len(batches)
        timed_out_batches: set = set()

        async def _run_batch(batch_idx: int, batch: list[MessageGroup]) -> None:
            # Wait for a pool slot OUTSIDE the try: a cancellation while
            # waiting means this batch never started, and a ``finally``
            # release here would free a slot we never held.
            await semaphore.acquire()
            started[batch_idx] = True
            try:
                summaries_by_idx[batch_idx] = await self._summarize_single_batch(
                    batch, context
                )
            except (TimeoutError, asyncio.TimeoutError):
                # O14-narrowed per-batch timeout: this batch failed on
                # its OWN adaptive cap, not the shared deadline (a
                # deadline hit surfaces as gather cancellation, never as
                # an exception here). Record it and let siblings finish.
                timed_out_batches.add(batch_idx)
                logger.warning(
                    "Batch %d/%d summarization timed out on its own "
                    "adaptive cap; continuing remaining batches.",
                    batch_idx + 1, len(batches),
                )
            finally:
                semaphore.release()

        pool = asyncio.gather(
            *(_run_batch(i, b) for i, b in enumerate(batches)),
            return_exceptions=True,
        )
        try:
            results = await asyncio.wait_for(pool, timeout=_budget_remaining())
        except (TimeoutError, asyncio.TimeoutError):
            # Shared budget deadline (D-B5/D-B6 preserved): the deadline
            # lives entirely inside ``_summarize_chunked`` — ``wait_for``
            # cancels the gather (cancelling every un-started and
            # in-flight batch task, and awaiting that cancellation)
            # BEFORE this handler runs, so no caller-side
            # ``aupdate_state`` is ever interleaved with a live pool.
            # Completed summaries are kept below. CancelledError is
            # never swallowed here — it is consumed by ``wait_for``
            # itself; no ``except BaseException`` exists in this file.
            logger.warning(
                "Operation budget deadline hit with %d/%d batch summaries "
                "complete (%d in-flight cancelled, %d never started); "
                "keeping completed summaries.",
                sum(1 for s in summaries_by_idx if s is not None),
                len(batches),
                sum(
                    1 for i in range(len(batches))
                    if summaries_by_idx[i] is None and started[i]
                ),
                sum(
                    1 for i in range(len(batches))
                    if summaries_by_idx[i] is None and not started[i]
                ),
            )
            stop_reason = "budget"
        else:
            # Deadline did NOT fire. ``return_exceptions=True`` parks any
            # non-timeout batch exception in the results — re-raise the
            # first one so the outer ``except Exception`` (compact_state)
            # maps it to the truncate fallback with
            # ``failure_kind="error"`` (O14: only timeouts are handled
            # per-batch; everything else propagates).
            for res in results:
                if isinstance(res, BaseException):
                    raise res
            stop_reason = "timeout" if timed_out_batches else "completed"

        # Completion set in batch-index order (non-contiguous survival:
        # every COMPLETED batch's summary is kept; each incomplete
        # batch's messages are dropped individually downstream —
        # ``_build_partial_replacement_messages`` RemoveMessages ALL
        # compactable groups, then re-adds the surviving summaries, the
        # marker, and the preserved tail).
        partial_summaries = [s for s in summaries_by_idx if s is not None]
        completed_idxs = {
            i for i, s in enumerate(summaries_by_idx) if s is not None
        }
        failed_batches = sorted(set(range(len(batches))) - completed_idxs)

        # If we ran out of time before any batch succeeded, surface
        # "timeout" — even if some partials are present. Outer handler
        # ignores this string on the |S|>=1 path; only ``summaries``
        # drives the partial-vs-truncate branching.
        if partial_summaries and stop_reason == "completed":
            # All batches succeeded → merge if multiple.
            if len(partial_summaries) == 1:
                return ChunkedOutcome(
                    summaries=partial_summaries,
                    failed_batches=[],
                    stop_reason="completed",
                )
            merged = await self._merge_summaries(partial_summaries, context)
            return ChunkedOutcome(
                summaries=[merged],
                failed_batches=[],
                stop_reason="completed",
            )

        # Partial-summary path (|S| >= 1, with stop_reason ∈
        # {"timeout", "budget"}) OR all-batches-failed path (|S| = 0).
        # Do NOT call _merge_summaries here — that's the explicit
        # rule for the partial path (architect §3 Correction 2 + C1):
        # only successful batches are summarized, and the preserved
        # tail + injected messages take over from B's raw span.
        return ChunkedOutcome(
            summaries=partial_summaries,
            failed_batches=failed_batches,
            stop_reason=stop_reason,
        )
    
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
        context_window = get_model_context_limit(
            self._effective_model_name(context), context.config
        )
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
        from .graph import ThinkingChatOpenAI, clean_llm_config
        from .services.llm_failover import wrap_langchain_failover

        # Compaction-model override (env COMPACTION_MODEL > yaml
        # compaction.model, resolved in load_config; legacy
        # summarization_model alias honored when unset): when active,
        # EVERY summarization call — including each concurrent batch call
        # in the parallel pool — resolves the SAME override through this
        # pure function on the shared config object, so all N client
        # constructions are consistent.
        override_model = resolve_compaction_model(context.config)
        if override_model:
            llm_config = {
                **self.llm_config_with_headers,
                "model": override_model,
            }
        else:
            llm_config = self.llm_config_with_headers

        # Phase 1 / WS-3.1+3.2: adaptive per-call timeout + facade margin.
        # ``inner_cap`` sizes both the ``asyncio.wait_for`` backstop AND the
        # facade's ``wall_clock_cap_s`` (``inner_cap + margin``). The
        # site-level backstop trips FIRST — that is the contract (architect
        # §9.8, "site TimeoutError still the first tripped"). The facade
        # cap is sized to wrap cleanly after the inner cancel so tenacity
        # retries stay inside the outer cap (llm_failover.py:559-568).
        inner_cap = _summarization_timeout_s(prompt, context.config)
        facade_cap = inner_cap + context.config.timeout_facade_margin_s

        # NEVER-SILENT FALLBACK (Commit B): if the override client cannot
        # be CONSTRUCTED (bad model string rejected by the client, config
        # shape error, facade wrap failure), WARN-log with the traceback
        # and rebuild from the session-model config — never swallowed.
        # (Invoke-time failures for an API-unknown model surface through
        # the EXISTING per-batch/outer handlers, which warn and fall back
        # to truncation — also never silent.)
        try:
            # ``base_url_backup`` is consumed by the HA facade from the raw
            # config dict; clean it only at the constructor.
            llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
            # v2 HA: route through the shared facade. See
            # ``daemon.services.llm_failover``. The facade cap is
            # ``inner_cap + timeout_facade_margin_s`` (default +5s) per the
            # architect §9.8 PINNED margin.
            llm_wrapper = wrap_langchain_failover(llm, llm_config, wall_clock_cap_s=facade_cap)
        except Exception:
            if not override_model:
                raise
            logger.warning(
                "Compaction model %r failed to construct; falling back to "
                "the session model for this summarization call.",
                override_model,
                exc_info=True,
            )
            llm_config = self.llm_config_with_headers
            llm = ThinkingChatOpenAI(**clean_llm_config(dict(llm_config)))
            llm_wrapper = wrap_langchain_failover(llm, llm_config, wall_clock_cap_s=facade_cap)

        # Belt-and-braces: ``inner_cap`` ``asyncio.wait_for`` is the
        # site-level cap (the FIRST to trip on timeout). The REAL
        # primary line of defense is the facade's
        # ``wall_clock_cap_s`` (tenacity ``stop_after_delay``
        # inside the retry loop) — see
        # ``daemon.services.llm_failover`` docstring "Wall-clock
        # cap". Belt-and-braces decision: keep the site-level
        # cap so a future site bypass of the facade still gets
        # cancellation; the facade cap is sized to wrap cleanly
        # after the inner cancel + a small margin so tenacity
        # retries don't overrun the outer ceiling. The
        # ``asyncio.TimeoutError`` propagates to the per-chunk
        # except in ``_summarize_chunked`` (narrowed to
        # ``(TimeoutError, asyncio.TimeoutError)`` per WS-3.4
        # O14), preserving the partial-summary path (C1).
        response = await asyncio.wait_for(
            asyncio.to_thread(
                llm_wrapper.invoke,
                [
                    SystemMessage(
                        content="You are a helpful assistant that summarizes conversations "
                        "concisely while preserving all important details."
                    ),
                    HumanMessage(content=prompt),
                ],
            ),
            timeout=inner_cap,
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

    @staticmethod
    def _build_partial_replacement_messages(
        compactable_groups: list[MessageGroup],
        preserved_groups: list[MessageGroup],
        summaries: list,
    ) -> list[BaseMessage]:
        """Build the partial-summary replacement list (WS-3.4 binding, C1).

        Difference from ``_build_replacement_messages``:

        * Multiple surviving summaries are inserted (one per successful
          batch) — not just one.
        * Incomplete batches are TRULY TRIMMED, individually:
          ``RemoveMessage`` entries are emitted for ALL compactable
          groups (contiguous or not — under the parallel pool the
          surviving summary set may be non-contiguous, e.g. batches
          {0, 2, 4}) but no replacement summary covers the incomplete
          ones. This is the bounded shrink the C1 acceptance criterion
          (b) requires — reduction is provably ≥ the un-summarized
          messages.
        * A truncation marker (WS-4.1 exactly-once) is appended between
          the surviving summaries and the preserved tail. The marker is
          identical in shape to the one ``_truncate_fallback`` emits —
          both paths share the module-scope ``_append_truncation_marker``
          helper, so the marker can never double-stamp or fork.

        Args:
            compactable_groups: All groups targeted for summarization
                (regardless of which batches succeeded — their
                ``RemoveMessage`` entries are still emitted).
            preserved_groups: Groups being kept intact.
            summaries: Surviving summaries from ``_summarize_chunked``
                (``|S| >= 1`` when this helper is called).

        Returns:
            Replacement message list with ``RemoveMessage`` entries,
            surviving summaries, exactly one marker, and the preserved
            tail (multimodal content flattened to strings).
        """
        replacement: list[BaseMessage] = []

        # ``RemoveMessage`` for ALL compactable groups — including the
        # un-summarized span, whose messages are being trimmed.
        for group in compactable_groups:
            for msg in group.messages:
                if msg.id:
                    replacement.append(RemoveMessage(id=msg.id))

        # Surviving summaries (order preserved from
        # ``_summarize_chunked``).
        replacement.extend(summaries)

        # Marker — W-4.3. The marker comes AFTER the surviving
        # summaries and BEFORE the preserved tail. The dedup
        # property is bounded accumulation (this helper appends
        # exactly one marker per call; the helper is called once
        # per ``CompactionResult``); the freshly-minted UUID4 in
        # the marker id would defeat any id-based dedup (this
        # helper does NOT rely on ``add_messages`` dedup — see
        # ``_append_truncation_marker`` docstring).
        _append_truncation_marker(replacement)

        # Preserved tail with multimodal content flattened.
        for group in preserved_groups:
            for msg in group.messages:
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

        Phase 1 / WS-4.1 (Q5 DECIDED, post-review adjudication C1):
        appends the truncation marker via the module-scope helper so
        the auto-path ``compaction_type="truncation"`` result also
        carries the marker line (O15 — intentional behavior change,
        pinned by the C1 acceptance regression test). The marker is
        identical in shape to the one the partial-summary assembly
        emits; the two construction paths are mutually exclusive per
        result so exactly one marker fires (W-4.3 — the dedup
        property is bounded accumulation, NOT
        ``add_messages`` id-dedup; the UUID4 is freshly minted per
        call and would defeat any id-based dedup).

        Args:
            compactable: Groups that would have been summarized.
            preserved: Groups being kept intact.
            context: Compaction context.

        Returns:
            Tuple of (replacement_messages, ``"truncation"``).
        """
        replacement: list[BaseMessage] = []

        # W-4.3 — RemoveMessage for compactable first (drops the old
        # span via the reducer).
        for group in compactable:
            for msg in group.messages:
                if msg.id:
                    replacement.append(RemoveMessage(id=msg.id))

        # W-4.3 — marker comes BEFORE the preserved tail. The
        # previous ordering (marker AFTER preserved tail) rendered
        # the marker as the newest message in the channel, which
        # pushed the surviving history UP and visually buried the
        # truncation notice. With the marker BEFORE the preserved
        # tail, the notice sits between the dropped span and the
        # surviving history — the natural "we trimmed, here's the
        # tail" position.
        _append_truncation_marker(replacement)

        # Preserved tail with multimodal content flattened.
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
