from __future__ import annotations

from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    BaseChatOpenAI,
    _convert_delta_to_message_chunk as _base_convert_delta_to_message_chunk,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages import BaseMessageChunk
from langchain_core.runnables import RunnableLambda
from langchain_core.messages.ai import AIMessageChunk, UsageMetadata
from typing import Any, ClassVar, Mapping, cast
import asyncio
import logging
import re
import openai
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger(__name__)


# ============================================================================
# get_instance_info throttling (escalating backoff)
# ============================================================================
# Counter resets on any non-gii message — see ToolThrottleSlot.bump/reset.
# Delay table maps the consecutive-call count (after the bump) to seconds
# spent sleeping in agent_node before the next LLM call.
# Scope: detects CONSECUTIVE gii calls only. When the agent emits gii in
# parallel with other tools in one AIMessage, ToolNode produces interleaved
# ToolMessages so messages[-1] may not be gii and the counter resets. This is
# intentional — the throttle targets consecutive single-tool polling loops.
GII_TOOL_NAME = "get_instance_info"
GII_DELAY_MAP: dict[int, int] = {
    3: 180,   # 3rd consecutive call: 3 min
    4: 300,   # 4th: 5 min
    5: 600,   # 5th: 10 min
}
GII_MAX_DELAY = 900  # 6+ consecutive: 15 min (cap)

from .llm_error_classifier import (
    classify_llm_errors,
    ContextLengthExceededError,
    TIMEOUT_EXCEPTIONS,
    TRANSIENT_EXCEPTIONS,
    TransientAPIError,
    _truncate_error,
)
from .response_validation import LLMResponseValidationError
from .language_detection import detect_wrong_language
from .utils import serialize_message
# Lazy import below — module-level ``from .services.language_utils`` would
# trigger daemon.services.__init__ → instance_lifecycle → compaction →
# graph (cycle) before this module finishes loading.


# ============================================================================
# Phase 1 / User Message Injection: lightweight handle (C1)
# ============================================================================
# The agent_node pulls a pending user-injection from this handle on every
# invocation and clears it immediately before invoking the LLM (C2). The
# handle intentionally wraps only the two methods that :func:`create_agent_node`
# needs (``get`` / ``clear``) — it does NOT pass the full ``InstanceManager``
# to the agent_node closure, so the graph can be tested with a plain mock
# (see ``tests/test_injection_graph.py``) without spinning up the daemon.
#
# Phase 2 will extend this same handle with ``set()`` for the API path; for
# now the ``set`` side lives on ``InstanceManager`` because no agent-node
# code path needs to write.

class InjectionSlot:
    """Lightweight, mock-friendly handle around InstanceManager injection slot.

    Threaded into :func:`build_instance_graph` and :func:`create_agent_node`
    via factory closure (C1), mirroring the existing ``compactor`` /
    ``graph_ref`` closure parameters. Backed by ``InstanceManager`` so the
    underlying dict is the single source of truth across all paths.

    Args:
        manager: The owning :class:`InstanceManager`. Tests may pass any
            object exposing ``get_injection`` and ``clear_injection``
            methods; the type is intentionally broad.

    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def get(self, instance_id: str) -> dict | None:
        """Peek the pending injection without clearing it.

        Returns ``None`` when no injection exists for this instance.
        """
        getter = getattr(self._manager, "get_injection", None)
        if getter is None:
            return None
        return getter(instance_id)

    def clear(self, instance_id: str) -> dict | None:
        """Pop and return the pending injection (or ``None``).

        Idempotent: calling when no injection exists is a no-op.
        """
        clearer = getattr(self._manager, "clear_injection", None)
        if clearer is None:
            return None
        return clearer(instance_id)


class ToolThrottleSlot:
    """Lightweight, mock-friendly handle around InstanceManager tool-throttle counters.

    Mirrors :class:`InjectionSlot`'s pattern: only the methods agent_node needs
    are exposed, the manager reference is duck-typed via ``getattr`` so the
    agent_node can be tested without a real ``InstanceManager``.

    Args:
        manager: The owning :class:`InstanceManager` (or any object exposing
            ``bump_gii_throttle``, ``reset_gii_throttle``, and
            ``get_gii_throttle_count`` methods).
    """

    def __init__(self, manager: Any) -> None:
        self._manager = manager

    def bump(self, instance_id: str) -> int:
        """Increment and return the consecutive ``get_instance_info`` count."""
        bumper = getattr(self._manager, "bump_gii_throttle", None)
        if bumper is None:
            return 0
        return bumper(instance_id)

    def reset(self, instance_id: str) -> None:
        """Reset the consecutive-call counter (no-op when unset)."""
        resetter = getattr(self._manager, "reset_gii_throttle", None)
        if resetter is not None:
            resetter(instance_id)

    def get_count(self, instance_id: str) -> int:
        """Return the current consecutive-call count (0 if unset)."""
        getter = getattr(self._manager, "get_gii_throttle_count", None)
        if getter is None:
            return 0
        return getter(instance_id)


class ThinkingChatOpenAI(ChatOpenAI):
    """Custom ChatOpenAI that captures reasoning_content from OpenAI-compatible APIs.

    Note: This class does NOT make duplicate requests. The thinking extraction
    is done from the response metadata if available, without additional API calls.
    """

    # Class-level config: model name patterns (case-insensitive substring
    # match) for which reasoning_content MUST be echoed back in multi-turn
    # assistant messages.
    #
    # Why this is configurable:
    #   - DeepSeek thinking mode requires reasoning_content in the assistant
    #     history whenever the prior turn included a tool call, or the model
    #     loses its chain-of-thought context. See:
    #     https://api-docs.deepseek.com/guides/thinking_mode
    #   - Other providers (e.g. raw OpenAI) reject unknown fields like
    #     reasoning_content, so we must NOT echo for those.
    #
    # The daemon sets this from LLMConfig.reasoning_echo_models at startup
    # (see daemon/__main__.py and daemon/manager.py). Default keeps DeepSeek
    # behavior working out of the box.
    reasoning_echo_models: ClassVar[list[str]] = ["deepseek"]

    def _should_echo_reasoning(self) -> bool:
        """Return True if the current model requires reasoning_content echo.

        Substring match (case-insensitive) against ``reasoning_echo_models``.
        """
        model = (self.model_name or "").lower()
        if not model:
            return False
        return any(pattern.lower() in model for pattern in self.reasoning_echo_models)

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        """Override to extract reasoning_content from the raw OpenAI response.

        LangChain's _convert_dict_to_message() does NOT extract the
        ``reasoning_content`` (or ``reasoning``) field that GLM/DeepSeek-style
        extended-thinking responses include at the top level of the assistant
        message dict. Without this override, the non-streaming path silently
        drops the model's thinking, and the web UI cannot render it.
        """
        result = super()._create_chat_result(response, generation_info)

        try:
            response_dict = (
                response if isinstance(response, dict) else response.model_dump()
            )
            choices = response_dict.get("choices") or []
            for i, res in enumerate(choices):
                if i >= len(result.generations):
                    break
                msg_dict = res.get("message") or {}
                reasoning = msg_dict.get("reasoning_content")
                if reasoning is None:
                    reasoning = msg_dict.get("reasoning")
                if reasoning is None:
                    continue
                gen_message = result.generations[i].message
                if not hasattr(gen_message, "additional_kwargs"):
                    continue
                # Store guard: only set if not already present (avoid clobbering
                # streaming path that may have already populated it).
                if gen_message.additional_kwargs.get("reasoning_content") is None:
                    gen_message.additional_kwargs["reasoning_content"] = reasoning
                    logger.debug(
                        f"[LLM] Extracted reasoning_content from raw response: "
                        f"{str(reasoning)[:100]}..."
                    )
        except Exception as e:
            logger.debug(f"[LLM] Could not extract reasoning_content in _create_chat_result: {e}")

        return result

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Override to capture reasoning_content from response metadata.

        This is a secondary safety net for the non-streaming path. The primary
        extraction now happens in _create_chat_result() which has access to the
        raw response message dict (where reasoning_content lives for GLM/DeepSeek
        responses). This method keeps the legacy fallback chain for any case
        where reasoning_content was already promoted to additional_kwargs or
        response_metadata by an upstream parser.
        """
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        try:
            if result.generations:
                gen_message = result.generations[0].message
                if hasattr(gen_message, 'additional_kwargs') and gen_message.additional_kwargs.get('reasoning_content') is not None:
                    # Already populated by _create_chat_result override.
                    return result
                if hasattr(gen_message, 'additional_kwargs'):
                    reasoning = gen_message.additional_kwargs.get('reasoning')
                    if reasoning is not None:
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                if hasattr(gen_message, 'response_metadata'):
                    meta = gen_message.response_metadata or {}
                    reasoning = meta.get('reasoning_content') or meta.get('reasoning')
                    if reasoning is not None and hasattr(gen_message, 'additional_kwargs') \
                            and gen_message.additional_kwargs.get('reasoning_content') is None:
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                        logger.debug(f"[LLM] Extracted reasoning from metadata: {str(reasoning)[:100]}...")

        except Exception as e:
            logger.debug(f"[LLM] Could not extract reasoning_content: {e}")

        return result

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Override to preserve reasoning_content in assistant message dicts.

        Only injects ``reasoning_content`` for models listed in
        ``reasoning_echo_models`` (default: ``["deepseek"]``).

        Why this is gated by model name:
          - DeepSeek thinking mode requires reasoning_content in the assistant
            history whenever the prior turn included a tool call, or the model
            loses its chain-of-thought context. See:
            https://api-docs.deepseek.com/guides/thinking_mode
          - Other providers (e.g. raw OpenAI) reject unknown fields like
            reasoning_content with a 400 error, so we must skip echo for them.
          - Some proxies ignore unknown fields silently, in which case echo is
            harmless but wastes a few hundred bytes of payload per turn.

        The parent class's ``_convert_message_to_dict()`` strips
        ``reasoning_content`` from additional_kwargs, so we re-inject it after
        the parent has built the payload.
        """
        # Fast path: skip the entire message-matching machinery for models that
        # don't require reasoning echo. This keeps the hot path identical to
        # stock ChatOpenAI for GPT-4o, GLM, Claude, etc.
        if not self._should_echo_reasoning():
            return super()._get_request_payload(input_, stop=stop, **kwargs)

        # Extract original messages once BEFORE calling super() to avoid double conversion.
        # super()._get_request_payload() internally calls _convert_input().to_messages(),
        # so we extract messages here first and use them for matching.
        try:
            original_messages = self._convert_input(input_).to_messages()
        except Exception as e:
            logger.debug(f"[LLM] Could not get original messages for reasoning_content injection: {e}")
            return super()._get_request_payload(input_, stop=stop, **kwargs)

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        payload_messages = payload.get("messages", [])

        # Build a mapping of assistant message indices to original AIMessages.
        # Index-based pairing invariant:
        # - The N-th assistant payload dict corresponds to the N-th original AIMessage.
        # - This relies on _convert_message_to_dict preserving message order (it does).
        # - We filter to assistant-only messages for matching since that's all we need to patch.
        assistant_idx = 0
        original_assistants = [m for m in original_messages if isinstance(m, AIMessage)]

        for msg in payload_messages:
            if msg.get("role") == "assistant":
                if assistant_idx < len(original_assistants):
                    original = original_assistants[assistant_idx]
                    reasoning = original.additional_kwargs.get('reasoning_content')
                    if reasoning is not None:
                        msg["reasoning_content"] = reasoning
                        logger.debug(f"[LLM] Injected reasoning_content for assistant message {assistant_idx}")
                    assistant_idx += 1

        return payload

    def _convert_delta_to_message_chunk(
        self, _dict: Mapping[str, Any], default_class: type[BaseMessageChunk]
    ) -> BaseMessageChunk:
        """Override to extract reasoning_content from delta chunks (e.g., GLM extended thinking).

        This is called during streaming via _stream()/_astream() when we override
        _convert_chunk_to_generation_chunk to call self._convert_delta_to_message_chunk
        instead of the module-level function.
        """
        # Extract reasoning_content before parent processes the delta
        reasoning_content = _dict.get("reasoning_content")
        if reasoning_content is None:
            reasoning_content = _dict.get("reasoning")

        # Call module-level function (ChatOpenAI doesn't override it)
        result = _base_convert_delta_to_message_chunk(_dict, default_class)

        # If we found reasoning_content and the result is an AIMessageChunk, store it
        if reasoning_content is not None and isinstance(result, AIMessageChunk):
            result.additional_kwargs["reasoning_content"] = reasoning_content
            logger.debug(f"[LLM] Stream extracted reasoning_content: {str(reasoning_content)[:50]}...")

        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        """Override to route _convert_delta_to_message_chunk through self.

        The parent implementation calls _convert_delta_to_message_chunk as a plain
        module-level function (bypassing our override). We fix that by calling it
        as self._convert_delta_to_message_chunk so our thinking extraction runs.
        """
        # --- Begin identical copy of BaseChatOpenAI._convert_chunk_to_generation_chunk ---
        # (only changed: _convert_delta_to_message_chunk(...) -> self._convert_delta_to_message_chunk(...))
        from langchain_openai.chat_models.base import (
            _create_usage_metadata,
        )

        if chunk.get("type") == "content.delta":  # From beta.chat.completions.stream
            return None
        token_usage = chunk.get("usage")
        choices = (
            chunk.get("choices", [])
            or chunk.get("chunk", {}).get("choices", [])
        )

        usage_metadata: UsageMetadata | None = (
            _create_usage_metadata(token_usage, chunk.get("service_tier"))
            if token_usage
            else None
        )
        if len(choices) == 0:
            generation_chunk = ChatGenerationChunk(
                message=default_chunk_class(content="", usage_metadata=usage_metadata),
                generation_info=base_generation_info,
            )
            if self.output_version == "v1":
                generation_chunk.message.content = []
                generation_chunk.message.response_metadata["output_version"] = "v1"
            return generation_chunk

        choice = choices[0]
        if choice["delta"] is None:
            return None

        # KEY FIX: call through self so our _convert_delta_to_message_chunk override is used
        message_chunk = self._convert_delta_to_message_chunk(
            choice["delta"], default_chunk_class
        )
        # --- End identical copy ---

        generation_info = {**base_generation_info} if base_generation_info else {}

        if finish_reason := choice.get("finish_reason"):
            generation_info["finish_reason"] = finish_reason
            if model_name := chunk.get("model"):
                generation_info["model_name"] = model_name
            if system_fingerprint := chunk.get("system_fingerprint"):
                generation_info["system_fingerprint"] = system_fingerprint
            if service_tier := chunk.get("service_tier"):
                generation_info["service_tier"] = service_tier

        logprobs = choice.get("logprobs")
        if logprobs:
            generation_info["logprobs"] = logprobs

        if usage_metadata and isinstance(message_chunk, AIMessageChunk):
            message_chunk.usage_metadata = usage_metadata

        message_chunk.response_metadata["model_provider"] = "openai"
        return ChatGenerationChunk(
            message=message_chunk, generation_info=generation_info or None
        )


def clean_llm_config(cfg: dict) -> dict:
    """Strip non-kwarg keys before passing to ThinkingChatOpenAI(**cfg).

    model_vision is used for vision routing decisions but is not a valid
    LangChain/ChatOpenAI parameter and must be removed before LLM construction.
    """
    return {k: v for k, v in cfg.items() if k != "model_vision"}


class SessionState(MessagesState):
    """Extended state schema for agent sessions.
    
    Inherits all message handling from MessagesState (add_messages reducer).
    Adds compaction metadata fields that persist in checkpoints.
    Also tracks user language preference check state.
    """
    # Compaction dedup: ISO timestamp of last successful compaction
    # Stored/retrieved via graph.aupdate_state() and state.values["compacted_at"]
    compacted_at: str | None = None
    # Language preference check state. Persisted in checkpoints so retries
    # survive across resumed graph executions.
    language_check_retry: bool = False
    language_check_count: int = 0


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue or end.
    
    Routes:
    - "tools": LLM returned tool_calls (normal flow)
    - "agent": Ghost promise — LLM text ends with ':' but no tool_call
    - "nudge": Empty response after tool execution — inject prompt to continue
    - END: LLM returned actual content with no tool_calls (done speaking)
    """
    messages = state["messages"]
    last_message = messages[-1]
    
    # Normal case: LLM made tool calls
    if getattr(last_message, 'tool_calls', None):
        return "tools"
    
    # Check if the model produced a "thinking-only" response.
    # Some models (e.g. Claude with extended thinking) emit an AIMessage that
    # carries reasoning_content but no content and no tool_calls — meaning the
    # model intends the next LLM call to produce the final answer. In that
    # case we re-route to "agent" to invoke the LLM again.
    #
    # However, streaming models like GLM/DeepSeek return BOTH reasoning_content
    # AND content in a single response. Re-invoking the LLM in that case would
    # either loop indefinitely or overwrite the correct response with a fresh
    # one that lacks reasoning_content, breaking the web UI's "show thinking"
    # feature. So we only re-invoke when the response is genuinely
    # thinking-only.
    if hasattr(last_message, 'additional_kwargs'):
        reasoning = last_message.additional_kwargs.get('reasoning_content')
        content = getattr(last_message, 'content', '') or ''
        has_tool_calls = bool(getattr(last_message, 'tool_calls', None))
        if reasoning and not content and not has_tool_calls:
            logger.debug(f"[Graph] Thinking-only response, continuing...")
            return "agent"
    
    # Ghost promise detection: LLM promised action but didn't emit tool_call
    # Common pattern: "Now let me write the document:" (ends with ':')
    content = getattr(last_message, 'content', '') or ''
    if isinstance(content, str) and content.rstrip().endswith(':'):
        logger.warning(f"[Graph] Ghost promise detected, LLM text ends with ':': {content[:100]}...")
        return "agent"  # Re-invoke agent to produce actual tool_call
    
    # Empty response after tool execution: model ACK'd but didn't continue
    # Inject a nudge so the model either continues working or finishes properly
    if _is_empty_content(content) and _has_recent_tool_result(messages):
        logger.info("[Graph] Empty response after tool execution, nudging agent to continue")
        return "nudge"
    
    return END


def _is_empty_content(content) -> bool:
    """Check if content is empty or whitespace-only."""
    if content is None:
        return True
    if isinstance(content, str):
        return content.strip() == ""
    return False


def _has_recent_tool_result(messages: list) -> bool:
    """Check if there's a ToolMessage in the recent message history.
    
    Looks back through messages (skipping the last empty AIMessage) to find
    a ToolMessage. Stops at the first HumanMessage to avoid false positives
    from tool results in earlier turns.
    """
    # Skip the last message (the empty AI response we're deciding on)
    for msg in reversed(messages[:-1]):
        msg_type = getattr(msg, 'type', None)
        if msg_type == 'tool':
            return True
        # Stop searching at human message boundary
        if msg_type == 'human':
            break
    return False


# Message injected when LLM returns empty after tool execution
NUDGE_MESSAGE = "Continue with your task, or provide your final response if you are finished."


def nudge_node(state):
    """Inject a nudge message to prompt the agent to continue or finish."""
    return {'messages': [HumanMessage(content=NUDGE_MESSAGE)]}


# ---------------------------------------------------------------------------
# User language preference: language check node + routing helpers
# ---------------------------------------------------------------------------
#
# These functions implement Phase 2 of the user language preference feature.
# They intercept the would-be END decision in should_continue() and route the
# final AI response through a detection step that may re-inject a reminder
# message if the response is in the wrong language.
#
# The original should_continue() is NOT modified. Instead, create_should_continue()
# returns a wrapper that translates END -> "end_candidate" so the graph routes
# to the language_check node when language_check_enabled=True.
LANGUAGE_REMINDER_TEMPLATE = (
    "You are responding in the wrong language. "
    "The user's preferred language is {language}. "
    "Please respond again in {language}."
)

LANGUAGE_CHECK_MAX_RETRIES = 2


def create_language_check_node(user_language: str):
    """Create the language check node function.

    The returned node examines the last AI message, runs language detection
    against the user's preferred language, and either:
    - Returns a HumanMessage reminder injected into the conversation, OR
    - Allows the conversation to END.

    Counter logic (S5 fix): language_check_count resets whenever a new
    HumanMessage without the language_check_reminder marker is observed,
    so each user turn starts with a fresh retry budget.

    Skip logic (C4 fix): if a `language_skip_check` tool was invoked since
    the last user message, detection is bypassed entirely for this turn.
    """

    async def language_check_node(state):
        messages = state["messages"]
        last_message = messages[-1]

        # Only check AIMessage content (not tool calls). If the last message
        # has tool_calls, it's a tool execution in progress — nothing to
        # validate yet.
        if not hasattr(last_message, 'content') or getattr(last_message, 'tool_calls', None):
            return {"language_check_retry": False, "language_check_count": 0}

        count = state.get("language_check_count", 0)

        # Combined scan: counter reset (S5) + skip detection (C4).
        # Both original loops scan the same range and break on the first
        # HumanMessage, so they can be safely merged into a single pass.
        # On hitting a skip tool we DO NOT break — we keep scanning so the
        # HumanMessage boundary still resets `count` consistently.
        skip = False
        for msg in reversed(messages[:-1]):
            msg_type = getattr(msg, 'type', None)
            if msg_type == 'human':
                # A reminder-injected HumanMessage is marked via
                # additional_kwargs so we don't reset on our own re-injections.
                if not getattr(msg, 'additional_kwargs', {}).get('language_check_reminder', False):
                    count = 0  # New user message, reset counter
                break  # Stop scanning past the last HumanMessage
            if msg_type == 'tool':
                tool_name = getattr(msg, 'name', None)
                if tool_name == 'language_skip_check':
                    skip = True
                    # Don't break — continue scanning in case there's a
                    # HumanMessage before this we still need to account for

        # Max retries — prevent infinite loop.
        if count >= LANGUAGE_CHECK_MAX_RETRIES:
            logger.warning(
                f"[LanguageCheck] Max retries ({LANGUAGE_CHECK_MAX_RETRIES}) reached, allowing response"
            )
            return {"language_check_retry": False, "language_check_count": 0}

        # Skip if language_skip_check tool was called.
        if skip:
            return {"language_check_retry": False, "language_check_count": 0}

        # Get content.
        content = getattr(last_message, 'content', '') or ''

        # W4 FIX: Wrap detection in try/except — never crash the graph.
        try:
            if detect_wrong_language(content, user_language):
                reminder = HumanMessage(
                    content=LANGUAGE_REMINDER_TEMPLATE.format(language=user_language),
                    additional_kwargs={"language_check_reminder": True},
                )
                logger.info(
                    f"[LanguageCheck] Wrong language detected "
                    f"(attempt {count + 1}/{LANGUAGE_CHECK_MAX_RETRIES}), injecting reminder"
                )
                return {
                    "messages": [reminder],
                    "language_check_retry": True,
                    "language_check_count": count + 1,
                }
        except (ValueError, TypeError, AttributeError, re.error) as e:
            logger.warning(f"[LanguageCheck] Detection error, allowing response: {e}")
            return {"language_check_retry": False, "language_check_count": 0}

        # Correct language — reset counter, no retry.
        return {"language_check_retry": False, "language_check_count": 0}

    return language_check_node


def should_end_language_check(state) -> str:
    """Determine if language check should retry or end.

    Returns "retry" if the language_check_node flagged a retry (wrong
    language detected); otherwise returns END so the conversation finishes.
    """
    if state.get("language_check_retry", False):
        return "retry"
    return END


def create_should_continue(language_check_enabled: bool):
    """Create a should_continue wrapper that routes to language_check when enabled.

    When language_check_enabled=True:
        - Routes final responses (would-be END) to "end_candidate" -> language_check
        - All other branches (tools, agent, nudge) unchanged.

    When language_check_enabled=False:
        - Returns the original should_continue() unchanged (END -> END).
        - No language_check node exists in the graph in this case.
    """
    if not language_check_enabled:
        return should_continue  # Use original function directly

    def should_continue_with_language_check(state: MessagesState) -> str:
        result = should_continue(state)
        if result == END:
            return "end_candidate"
        return result

    return should_continue_with_language_check


def create_agent_node(
    llm_with_tools,
    system_prompt: str,
    compactor=None,
    graph_ref=None,
    config=None,
    llm_config=None,
    retry_config=None,
    llm_standard=None,
    injection_slot: InjectionSlot | None = None,
    live_hub: Any = None,
    throttle_slot: ToolThrottleSlot | None = None,
):
    """Create the agent node function with optional reactive compaction.

    Args:
        llm_with_tools: LLM already bound with tools (vision model if configured).
        system_prompt: System prompt to prepend to messages.
        compactor: Optional ContextCompactor for reactive compaction.
        graph_ref: Optional list for late-bound graph reference.
        config: Optional config for compaction.
        llm_config: Optional LLM config for compaction context.
        retry_config: Optional retry configuration for logging.
        llm_standard: Optional standard LLM bound with tools (for non-vision calls).
            When provided, vision model is used when images are present.
        injection_slot: Optional :class:`InjectionSlot` handle (C1) that
            exposes ``get(instance_id) → dict|None`` and
            ``clear(instance_id) → dict|None``. When supplied, the agent
            node peeks + clears a pending user message on every LLM
            invocation and threads the resulting ``HumanMessage`` into
            the conversation. ``None`` disables injection entirely
            (backward compatible).
        live_hub: Optional ``LiveEventHub`` reference threaded for the
            Phase 2 SSE emission path (``stream_message(... event_type=
            "injection_consumed" ...)``). In Phase 1 the handle is wired
            but only a log-only stub runs so the structural call site is
            exercised; ``None`` skips the stub entirely.
        throttle_slot: Optional :class:`ToolThrottleSlot` handle that
            throttles consecutive ``get_instance_info`` tool calls by
            injecting escalating ``asyncio.sleep`` delays before the
            LLM call. The slot's ``bump``/``reset``/``get_count`` are
            invoked on the last message in the state — non-gii
            messages reset the consecutive-call counter. ``None``
            disables throttling (backward compatible).
    """

    async def agent_node(state, config=None):
        messages = state['messages']
        full_messages = [SystemMessage(content=system_prompt)] + list(messages)
        transient = retry_config.get('transient_attempts', 8) if retry_config else 8
        timeout = retry_config.get('timeout_attempts', 3) if retry_config else 3
        instance_id = (config or {}).get('configurable', {}).get('thread_id', 'unknown')
        instance_short = instance_id.split('-')[0] if '-' in instance_id else instance_id

        # ── Phase 1 / C2: pull + clear the pending user-injection ─────────
        # Pull happens BEFORE the LLM call so the injected HumanMessage is
        # part of the request. Clear happens BEFORE the LLM call too —
        # not after — so a transient LLM failure cannot leave the slot
        # stale: either the LLM sees the injection, or the slot survives
        # to be retried on the next agent turn.
        #
        # Reference is captured in ``injected_msg`` so the reactive
        # compaction handler (C3) can re-append it after a checkpoint
        # re-read, and so the return value (C2) persists BOTH messages.
        injected_msg: HumanMessage | None = None
        if injection_slot is not None:
            pending = injection_slot.get(instance_id)
            if pending is not None:
                content = pending.get("content", "")
                injected_msg = HumanMessage(
                    content=content,
                    additional_kwargs={"injected_message": True},
                )
                full_messages.append(injected_msg)
                cleared = injection_slot.clear(instance_id)
                # Defensive: if the slot was empty on clear (extremely
                # unlikely race — another consumer popped it between our
                # get and clear), log and continue. ``injected_msg`` is
                # already in full_messages and will be returned.
                if cleared is None:
                    logger.warning(
                        f"[Injection] Slot disappeared between get+clear "
                        f"for instance {instance_short} — continuing"
                    )
                logger.info(
                    f"[Injection] Pulled pending message for "
                    f"{instance_short} (len={len(content)})"
                )

                # Phase 2 / Task 7 (W5): finalize the SSE emission at the
                # consumption point. The Phase 1 placeholder exercised the
                # call site so the structural wiring is already proven; this
                # is the real ``stream_message(..., event_type=...)`` call.
                # W5 contract: NO new method on ``LiveEventHub`` — we reuse
                # the existing ``stream_message`` with a custom ``event_type``
                # so the frontend (Phase 3) sees ``event_type="injection_consumed"``
                # under the same payload shape the API uses.
                #
                # The clear returned the entry that was just consumed; we
                # re-emit content + timestamp so the SSE listener sees the
                # exact text the LLM was about to see.
                #
                # BUG FIX (injection-sse-echo-fix): the normal ``send_message``
                # path in ``instance_messaging.py`` pre-emits a ``user_message``
                # SSE event before the LLM runs so the frontend can echo the
                # user's text. The injection path only emitted
                # ``injection_consumed`` and was missing the ``user_message``
                # echo, so injected messages rendered without a user-bubble
                # update on the UI. We mirror the normal-path shape here:
                # serialize a HumanMessage carrying the injected ``content``,
                # stamp ``instance_id``, and emit ``user_message`` with
                # ``checkpoint_id="user"`` so the frontend treats it the same
                # way as a regular user turn.
                if live_hub is not None:
                    try:
                        injected_user_msg = HumanMessage(content=content)
                        user_serialized = serialize_message(injected_user_msg)
                        user_serialized["instance_id"] = instance_id
                        await live_hub.stream_message(
                            instance_id=instance_id,
                            message=user_serialized,
                            event_type="user_message",
                            checkpoint_id="user",
                        )
                    except Exception as e:  # pragma: no cover - defensive
                        # LLM call must not be blocked by an SSE outage —
                        # log and continue. The injection is already
                        # consumed locally (checkpoint persist + injected_msg
                        # in full_messages); the SSE event is best-effort.
                        logger.warning(
                            f"[Injection] user_message SSE emit failed for "
                            f"{instance_short}: {type(e).__name__}: {e}"
                        )

                if live_hub is not None:
                    try:
                        await live_hub.stream_message(
                            instance_id,
                            message={
                                "instance_id": instance_id,
                                "event_type": "injection_consumed",
                                "content": cleared.get("content") if cleared else content,
                                "timestamp": cleared.get("timestamp") if cleared else None,
                            },
                            event_type="injection_consumed",
                        )
                    except Exception as e:  # pragma: no cover - defensive
                        # LLM call must not be blocked by an SSE outage —
                        # log and continue. The injection is already
                        # consumed locally (checkpoint persist + injected_msg
                        # in full_messages); the SSE event is best-effort.
                        logger.warning(
                            f"[Injection] injection_consumed SSE emit "
                            f"failed for {instance_short}: "
                            f"{type(e).__name__}: {e}"
                        )

        # Check if vision model is being used (images present in user message)
        model_vision = llm_config.get("model_vision") if llm_config else None
        has_images = False
        for msg in messages:
            content = getattr(msg, 'content', None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image_url":
                        has_images = True
                        break
            if has_images:
                break

        # Select the appropriate LLM:
        # - Images present: use vision model (llm_with_tools which has vision model)
        # - No images: use standard model if available
        use_vision_model = has_images and model_vision and llm_standard is not None
        current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)

        model_name = model_vision if use_vision_model else llm_config.get("model", "unknown") if llm_config else "unknown"
        vision_log = f", vision={model_vision}" if model_vision and has_images else ""
        call_type = "VISION" if use_vision_model else "STANDARD"
        logger.info(f'[LLM][{instance_short}] Invoking LLM ({call_type}) with {len(full_messages)} messages (model={model_name}, transient_attempts={transient}, timeout_attempts={timeout}{vision_log})')

        # ── get_instance_info throttling ─────────────────────────────────
        # Counts consecutive gii tool messages so we can inject escalating
        # delays and break the hallucination polling loop. The counter
        # resets on any non-gii message — this branch covers every other
        # message type (HumanMessage, AIMessage without tool_calls, etc.).
        if throttle_slot is not None:
            last_msg = messages[-1] if messages else None
            if isinstance(last_msg, ToolMessage) and last_msg.name == GII_TOOL_NAME:
                count = throttle_slot.bump(instance_id)
                if count >= 3:
                    delay = GII_DELAY_MAP.get(count, GII_MAX_DELAY)
                    logger.info(
                        f"[THROTTLE] Instance {instance_short}: "
                        f"get_instance_info consecutive call #{count}, "
                        f"sleeping {delay}s before next LLM call"
                    )
                    await asyncio.sleep(delay)
            else:
                throttle_slot.reset(instance_id)

        try:
            # Use run_in_executor to avoid blocking the event loop.
            # This allows SSE streaming to continue while LLM processes.
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: current_llm.invoke(full_messages)
            )
        except ContextLengthExceededError:
            if compactor is None or graph_ref is None or graph_ref[0] is None:
                logger.warning('[LLM] Context length exceeded (no compactor available)')
                raise

            logger.info(f'[LLM] Context length exceeded, attempting reactive compaction for {len(messages)} messages')

            graph = graph_ref[0]
            thread_config = config or {}

            current_state = await graph.aget_state(thread_config)
            current_messages = current_state.values.get('messages', [])
            compacted_at_val = current_state.values.get('compacted_at')

            from .compaction import CompactionContext
            ctx = CompactionContext(
                messages=current_messages,
                system_prompt_tokens=0,
                model_name=llm_config.get('model', '') if llm_config else '',
                config=compactor.config,
                llm_config=compactor.llm_config,
                last_compacted_at=compacted_at_val,
            )

            result = await compactor.compact_state(ctx)
            if result is None or result.replacement_messages is None:
                logger.warning('Reactive compaction returned no result, re-raising')
                raise

            await graph.aupdate_state(thread_config, {'messages': result.replacement_messages}, as_node='agent')
            if result.compacted_at:
                await graph.aupdate_state(thread_config, {'compacted_at': result.compacted_at}, as_node='agent')

            logger.info(f'[LLM] Reactive compaction complete: {result.messages_before} -> {result.messages_after} messages, {result.tokens_saved} tokens saved ({result.compaction_type})')

            updated_state = await graph.aget_state(thread_config)
            compact_messages = [SystemMessage(content=system_prompt)] + updated_state.values.get('messages', [])

            # C3: Reactive compaction re-append — the injected message
            # lives only in the local ``full_messages`` list above (it
            # has NOT been persisted to the checkpoint via
            # ``add_messages`` yet). ``graph.aget_state`` reads from
            # checkpoint, so without this re-append the LLM retry would
            # lose the user's injected message. We re-append in-place
            # so the retry sees it exactly as the first attempt did.
            if injected_msg is not None:
                compact_messages.append(injected_msg)
                logger.debug(
                    f'[LLM] Reactive compaction: re-appended injected '
                    f'message for {instance_short} '
                    f'(len={len(injected_msg.content or "")})'
                )

            # Use run_in_executor to avoid blocking the event loop after compaction
            # Continue with the same LLM that was being used (may be vision or standard)
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: current_llm.invoke(compact_messages)
            )
        except (openai.APITimeoutError, openai.APIConnectionError, ConnectionResetError,
                BrokenPipeError, ConnectionAbortedError, TransientAPIError, LLMResponseValidationError) as e:
            transient = retry_config.get('transient_attempts', 'N/A') if retry_config else 'N/A'
            timeout = retry_config.get('timeout_attempts', 'N/A') if retry_config else 'N/A'
            category = 'timeout' if isinstance(e, TIMEOUT_EXCEPTIONS) else 'transient' if isinstance(e, TRANSIENT_EXCEPTIONS) else 'non-retryable'
            logger.error(f"[LLM] All retries exhausted ({category}, transient_attempts={transient}, timeout_attempts={timeout}): {type(e).__name__}: {_truncate_error(e)}")
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error after retries: {type(e).__name__}: {_truncate_error(e)}")
            raise

        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', getattr(tc, 'name', '?')) for tc in response.tool_calls]
            # Get first tool's arguments for display
            first_tc = response.tool_calls[0]
            tc_args = first_tc.get('args', getattr(first_tc, 'args', {}))
            tc_args_str = str(tc_args)[:80] if tc_args else ''
            logger.info(f'[LLM] Tool call: {tool_names[0]} — {tc_args_str}..., tools: {tool_names}')
        elif response.content:
            logger.info(f'[LLM] Response: {response.content[:80]}...')
        else:
            logger.info('[LLM] Response: empty')

        # C2: Persist BOTH the injected HumanMessage and the LLM response
        # so the ``add_messages`` reducer writes them to the checkpoint
        # together. When no injection was consumed, fall back to the
        # existing single-message return so the surface is identical to
        # the pre-Phase-1 behavior.
        if injected_msg is not None:
            return {'messages': [injected_msg, response]}
        return {'messages': [response]}

    return agent_node


def build_instance_llms(
    llm_config_with_headers: dict,
    model_standard: str,
    model_vision: str | None,
    tools: list,
    retry_config: dict | None = None,
):
    """Create LLM instances for agent execution.

    This function handles the logic for creating:
    - llm_with_tools: Primary LLM bound to tools (vision if configured, else standard)
    - llm_standard: Standard LLM (always bound to tools for tool-calling)

    Returns:
        Tuple of (llm_with_tools, llm_standard)
    """
    llm_standard = None
    llm_with_tools = None

    if model_vision:
        logger.info(f"[Graph] Vision model configured: {model_vision}")
        # Filter model_vision from config to avoid passing it to the API
        vision_config = clean_llm_config(llm_config_with_headers)
        vision_config["model"] = model_vision
        llm_with_tools = ThinkingChatOpenAI(**vision_config).bind_tools(tools)
    else:
        logger.info("[Graph] No vision model configured, using standard model for all calls")

    # Create standard LLM (always needed, even if vision is configured)
    # Filter model_vision from config to avoid noisy LangChain warnings
    standard_config = clean_llm_config(llm_config_with_headers)
    standard_config["model"] = model_standard
    llm_standard = ThinkingChatOpenAI(**standard_config)

    # Always bind tools to llm_standard, regardless of vision configuration
    if llm_with_tools is None:
        llm_with_tools = llm_standard.bind_tools(tools)
    llm_standard = llm_standard.bind_tools(tools)

    # Wrap with error classification and retry if config provided
    if retry_config:
        # CRITICAL: classify errors BEFORE retry so they can be caught
        llm_with_tools = classify_llm_errors(llm_with_tools)
        if llm_standard is not llm_with_tools:
            llm_standard = classify_llm_errors(llm_standard)

        from daemon.llm_error_classifier import _make_llm_retry_strategy

        transient_attempts = retry_config.get("transient_attempts", 8)
        timeout_attempts = retry_config.get("timeout_attempts", 3)

        retry_predicate = _make_llm_retry_strategy(
            transient_max=transient_attempts,
            timeout_max=timeout_attempts,
        )

        # Use max() as hard safety ceiling; the predicate controls per-category limits
        max_attempts = max(transient_attempts, timeout_attempts)

        # Use tenacity directly since LangChain's with_retry() no longer supports
        # custom retry predicates (the 'retry=' parameter was removed)
        retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(),
            retry=retry_predicate,
            reraise=True,
        )

        # Capture the classified LLMs for retry wrapper
        classified_llm = llm_with_tools

        def _run_with_retry(input_value):
            return retrying(classified_llm.invoke, input_value)

        llm_with_tools = RunnableLambda(_run_with_retry)

        # Also wrap standard LLM with Retrying if it's different from llm_with_tools.
        # This handles the dual-LLM architecture case where both vision and standard
        # models need their own retry wrappers.
        if llm_standard is not llm_with_tools:
            classified_standard = llm_standard
            def _run_standard_with_retry(input_value):
                return retrying(classified_standard.invoke, input_value)
            llm_standard = RunnableLambda(_run_standard_with_retry)

        logger.debug(
            f"LLM configured with {transient_attempts} transient retries, "
            f"{timeout_attempts} timeout retries"
        )

    return llm_with_tools, llm_standard


# ============================================================================
# Question tool: conditional post-tools edge + pause node (Phase 1)
# ============================================================================
# The ``question`` tool sets ``manager._question_pause_requested[instance_id]
# = True`` before returning. The conditional post-tools edge
# (``create_post_tools_router``) reads this flag on every post-tools
# evaluation. When the flag is set, the graph routes to
# ``question_pause_node`` instead of back to ``agent``. The pause node calls
# ``manager.pause_instance_cascade`` which cancels the graph task mid-
# execution (raising ``CancelledError`` at the next ``await``), then clears
# the flag in its ``finally`` block so a future resume can't get stuck in a
# pause loop.
#
# CRITICAL invariants (F2 + F4 from phase1-plan):
#  * The flag MUST be cleared in ``finally`` — never after the await.
#    ``pause_instance_cascade`` cancels the graph task; any code after the
#    await is unreachable. Without ``finally``, the flag would stay set
#    forever and the instance would re-pause on the first post-resume tool
#    call, creating a stuck loop.
#  * ``CancelledError`` is RE-RAISED, never swallowed. Swallowing it would
#    break LangGraph's cancellation contract and leave the task in a
#    running-but-actually-cancelled state.
#  * Non-CancelledError exceptions are logged + the flag is cleared + the
#    exception is re-raised — defense in depth so a transient cascade
#    failure can't leave the flag set either.


def create_post_tools_router(manager: Any):
    """Build the conditional post-tools router that handles question pauses.

    Returns a closure suitable for ``graph.add_conditional_edges``. On
    every post-tools evaluation the closure reads
    ``manager.is_question_pause_requested(instance_id)`` and routes to
    ``"question_pause_node"`` when True, otherwise back to ``"agent"``.

    The ``instance_id`` is taken from the LangGraph config's
    ``configurable.thread_id`` (set when the graph is invoked with
    ``{"configurable": {"thread_id": instance_id}}`` — the same pattern
    used elsewhere in the codebase). Falling back to ``None`` when
    config is missing is safe because ``is_question_pause_requested``
    returns ``False`` for unknown ids.

    Args:
        manager: The ``InstanceManager`` reference threaded from
            ``build_instance_graph``. Must expose
            ``is_question_pause_requested(instance_id) -> bool``.

    Returns:
        A callable ``router(state, config) -> str`` returning the
        next-node name (``"agent"`` or ``"question_pause_node"``).
    """
    def post_tools_router(state: Any, config: Any = None) -> str:
        instance_id: str | None = None
        try:
            if config is not None:
                configurable = (
                    config.get("configurable")
                    if isinstance(config, dict)
                    else getattr(config, "configurable", None)
                )
                if isinstance(configurable, dict):
                    instance_id = configurable.get("thread_id")
        except Exception:
            instance_id = None
        if instance_id and manager.is_question_pause_requested(instance_id):
            return "question_pause_node"
        return "agent"

    return post_tools_router


def create_question_pause_node(manager: Any):
    """Build the ``question_pause_node`` async function with ``manager`` captured.

    This factory mirrors the pattern used by ``create_agent_node`` and
    ``create_language_check_node`` — the closure captures ``manager``
    so the returned coroutine function has the manager reference at
    call time without depending on module-level singletons (which would
    break tests and any multi-graph runtime).

    The returned node pauses the instance after a question tool call:

      1. Reads ``instance_id`` from ``config["configurable"]["thread_id"]``.
      2. Calls ``manager.pause_instance_cascade(instance_id)`` which
         transitions the instance + all children to ``PAUSED`` in the
         DB and cancels the active ``graph_task`` via
         ``graph_task.cancel()``.
      3. The cascade's cancellation raises ``CancelledError`` at the
         next ``await`` — re-raised so LangGraph's cancellation
         contract is honored.
      4. The ``finally`` block clears the pause-requested flag,
         preventing stuck-pause loops on the next resume.

    CRITICAL invariants (F2 + F4 from phase1-plan):
      * Flag MUST be cleared in ``finally`` — code after the await is
        unreachable on the CancelledError success path.
      * ``CancelledError`` is RE-RAISED, never swallowed.
      * Non-CancelledError exceptions are logged + the flag cleared +
        re-raised (defense in depth).

    Args:
        manager: The ``InstanceManager`` reference threaded from
            ``build_instance_graph``. Must expose
            ``pause_instance_cascade(instance_id) -> Awaitable[dict]``
            and
            ``clear_question_pause_requested(instance_id) -> None``.

    Returns:
        An async callable suitable for ``graph.add_node("name", ...)``.
    """
    async def question_pause_node(state: Any, config: Any = None) -> dict:
        instance_id: str | None = None
        try:
            if config is not None:
                configurable = (
                    config.get("configurable")
                    if isinstance(config, dict)
                    else getattr(config, "configurable", None)
                )
                if isinstance(configurable, dict):
                    instance_id = configurable.get("thread_id")
        except Exception:
            instance_id = None

        if instance_id is None:
            # Should never happen in production (the router requires
            # config to set the flag); log + bail so LangGraph sees a
            # normal return and can route to END.
            logger.warning(
                "[question_pause_node] missing instance_id from config — "
                "skipping pause cascade"
            )
            return {}

        try:
            await manager.pause_instance_cascade(instance_id)
        except asyncio.CancelledError:
            # SUCCESS PATH: ``pause_instance_cascade`` calls
            # ``graph_task.cancel()`` which raises CancelledError at the
            # next await — i.e. right here, on the way back from the
            # cascade. Re-raise so LangGraph's cancellation contract is
            # honored. The ``finally`` block below clears the flag
            # BEFORE the CancelledError propagates further (Python
            # guarantees ``finally`` runs during exception unwinding).
            raise
        except Exception as e:
            # Defense-in-depth (F4): non-CancelledError failures — log
            # + clear the flag + re-raise so the graph sees a real
            # error rather than a silent stuck-pause loop. Re-raising
            # also lets upstream observers record the failure.
            logger.error(
                f"[question_pause_node] pause cascade failed for "
                f"{instance_id[:8]}...: {type(e).__name__}: {e}"
            )
            try:
                manager.clear_question_pause_requested(instance_id)
            except Exception as clear_err:
                logger.warning(
                    f"[question_pause_node] failed to clear pause flag "
                    f"after cascade error for {instance_id[:8]}...: "
                    f"{clear_err}"
                )
            raise
        finally:
            # ALWAYS clear the flag, even on CancelledError path (F2).
            # The success path of ``pause_instance_cascade`` raises
            # CancelledError at the next await, so code after the await
            # is UNREACHABLE. The ``finally`` block is the ONLY
            # reliable place to clear the flag — without it the
            # instance would re-pause on the first post-resume tool call.
            try:
                manager.clear_question_pause_requested(instance_id)
            except Exception as clear_err:
                logger.warning(
                    f"[question_pause_node] failed to clear pause flag "
                    f"in finally for {instance_id[:8]}...: {clear_err}"
                )

        # Unreachable in practice — CancelledError from
        # ``pause_instance_cascade`` always interrupts the await. Kept
        # for type-checker / defensive clarity.
        return {}

    return question_pause_node


def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor=None,
    graph_config=None,
    user_language: str = "Auto",
    language_check_enabled: bool = True,
    injection_slot: InjectionSlot | None = None,
    live_hub: Any = None,
    throttle_slot: ToolThrottleSlot | None = None,
    manager: Any = None,
):
    """Build and return a compiled instance graph with LLM-level retry.

    When model_vision is configured, we create two LLM instances:
    - llm_with_tools (vision): Used when images are present
    - llm_standard: Used for text-only calls

    When language_check_enabled=True, the graph gains an additional
    `language_check` node that intercepts the would-be END decision from
    should_continue() and validates the final AI response against the
    user's preferred language. If the language is wrong, a reminder
    HumanMessage is injected and the agent re-runs (up to
    LANGUAGE_CHECK_MAX_RETRIES times).

    Args:
        tools: Tool list bound to the agent LLM.
        checkpointer: LangGraph checkpointer for state persistence.
        llm_config: LLM configuration dict (provider, model, etc.).
        system_prompt: System prompt prepended to every agent turn.
        retry_config: Optional retry/backoff configuration.
        compactor: Optional ``ContextCompactor`` (C3) threaded to the
            agent_node for reactive compaction on context overflow.
        graph_config: Optional LangGraph config (``thread_id``, etc.).
        user_language: User-preferred language for the language-check node.
        language_check_enabled: Whether to enable the language-check node.
        injection_slot: Optional :class:`InjectionSlot` handle (Phase 1
            / C1) that lets the agent_node pull a pending user message
            into the conversation before each LLM call. ``None``
            disables injection (backward-compatible default).
        live_hub: Optional ``LiveEventHub`` reference (Phase 1 / C1)
            threaded for Phase 2 SSE emission (placeholder only in
            Phase 1).
        throttle_slot: Optional :class:`ToolThrottleSlot` handle that
            throttles consecutive ``get_instance_info`` tool calls by
            injecting escalating ``asyncio.sleep`` delays before the
            LLM call. ``None`` disables throttling (backward-compatible
            default).
        manager: Optional ``InstanceManager`` reference (Phase 1 /
            question-tool) threaded so the conditional post-tools edge
            (``create_post_tools_router``) can read the
            ``_question_pause_requested`` flag and the
            ``question_pause_node`` can call ``pause_instance_cascade``.
            ``None`` is backward-compatible (no question-pause behavior;
            the unconditional ``tools → agent`` edge is used instead).
    """
    # Add proxy header to all LLM requests
    llm_config_with_headers = {
        **llm_config,
        "default_headers": {"x-proxy-app": "ensemble"},
    }

    # Check if vision model is configured
    model_vision = llm_config.get("model_vision")
    model_standard = llm_config.get("model")

    # "Auto" means "no preference" — disable the language_check node so the
    # LLM is free to reply in whatever language matches the user's input.
    # Must happen BEFORE the conditional graph wiring below.
    # Lazy import — see top-of-file note about the graph ↔ services cycle.
    from .services.language_utils import is_auto_language
    if is_auto_language(user_language):
        language_check_enabled = False

    # Create LLMs using the helper function
    llm_with_tools, llm_standard = build_instance_llms(
        llm_config_with_headers=llm_config_with_headers,
        model_standard=model_standard,
        model_vision=model_vision,
        tools=tools,
        retry_config=retry_config,
    )

    # Late binding for graph reference
    graph_ref = [None]

    graph = StateGraph(SessionState)

    # Add nodes - pass both vision and standard LLM. Phase 1 / C1 also
    # threads ``injection_slot`` and ``live_hub`` into the agent-node
    # closure so the graph can consume pending user injections without
    # importing InstanceManager (preserves test isolation).
    graph.add_node("agent", create_agent_node(
        llm_with_tools,
        system_prompt,
        compactor=compactor,
        graph_ref=graph_ref,
        config=graph_config,
        llm_config=llm_config_with_headers,
        retry_config=retry_config,
        llm_standard=llm_standard,
        injection_slot=injection_slot,
        live_hub=live_hub,
        throttle_slot=throttle_slot,
    ))
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("nudge", nudge_node)
    
    # Add edges
    graph.add_edge(START, "agent")

    # Conditionally add language_check node + build routing.
    # When language_check_enabled=True, the wrapper routes the original
    # END decision to "end_candidate" -> language_check, which then either
    # retries (back to agent) or ends the graph.
    # When language_check_enabled=False, we use the original should_continue
    # unchanged and no language_check node is added to the graph.
    if language_check_enabled:
        graph.add_node("language_check", create_language_check_node(user_language))

        # Closure wrapper: routes END -> "end_candidate"
        routing_fn = create_should_continue(language_check_enabled=True)

        graph.add_conditional_edges("agent", routing_fn, {
            "tools": "tools",          # Normal: LLM made tool calls
            "agent": "agent",          # Ghost promise: retry agent
            "nudge": "nudge",          # Empty after tool: inject prompt
            "end_candidate": "language_check",  # Would-be END: validate language
        })

        # Language check -> retry or END
        graph.add_conditional_edges("language_check", should_end_language_check, {
            "retry": "agent",
            END: END,
        })
    else:
        # Language check disabled: use original should_continue, no language_check node
        graph.add_conditional_edges("agent", should_continue, {
            "tools": "tools",          # Normal: LLM made tool calls
            "agent": "agent",          # Ghost promise: LLM promised but no tool_call, retry
            "nudge": "nudge",          # Empty after tool: inject prompt to continue
            END: END,
        })

    # Conditional post-tools edge: route to ``question_pause_node`` when
    # the question tool has requested a pause (F1). The original
    # unconditional ``tools → agent`` edge was the bug — it could not
    # honor a pause request because every post-tools routing went back
    # to the agent unconditionally. The conditional router reads
    # ``manager._question_pause_requested`` (set by the ``question``
    # tool, cleared by ``question_pause_node``'s ``finally`` block).
    # Non-question tool calls still route to ``agent`` normally because
    # the flag defaults to False for instances that haven't called the
    # ``question`` tool.
    if manager is None:
        # Backward-compatible fallback: no manager reference means no
        # question-pause behavior. Keep the original unconditional edge.
        graph.add_edge("tools", "agent")
    else:
        question_pause_node = create_question_pause_node(manager)
        graph.add_node("question_pause_node", question_pause_node)
        graph.add_conditional_edges(
            "tools",
            create_post_tools_router(manager),
            {
                "agent": "agent",
                "question_pause_node": "question_pause_node",
            },
        )
        # ``question_pause_node`` routes to END — the cascade has
        # cancelled the graph task, so resuming will start fresh from
        # the checkpoint.
        graph.add_edge("question_pause_node", END)
    graph.add_edge("nudge", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)

    # W4 FIX: Store language_check_active flag on compiled graph for streaming code to read
    compiled.language_check_active = language_check_enabled

    # Late bind graph reference
    graph_ref[0] = compiled

    return compiled


# Backward compatibility alias
build_session_graph = build_instance_graph
