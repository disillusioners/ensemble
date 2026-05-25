from __future__ import annotations

from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    BaseChatOpenAI,
    _convert_delta_to_message_chunk as _base_convert_delta_to_message_chunk,
)
from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages import BaseMessageChunk
from langchain_core.runnables import RunnableLambda
from langchain_core.messages.ai import AIMessageChunk, UsageMetadata
from typing import Any, Mapping, cast
import asyncio
import logging
import openai
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter

logger = logging.getLogger(__name__)

from .llm_error_classifier import (
    classify_llm_errors,
    ContextLengthExceededError,
    TIMEOUT_EXCEPTIONS,
    TRANSIENT_EXCEPTIONS,
    TransientAPIError,
    _truncate_error,
)
from .response_validation import LLMResponseValidationError


class ThinkingChatOpenAI(ChatOpenAI):
    """Custom ChatOpenAI that captures reasoning_content from OpenAI-compatible APIs.
    
    Note: This class does NOT make duplicate requests. The thinking extraction
    is done from the response metadata if available, without additional API calls.
    """
    
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Override to capture reasoning_content from response metadata."""
        # Call parent implementation (this is the ONLY HTTP request)
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        
        # Try to extract reasoning_content from the result's additional_kwargs
        # (no additional HTTP request needed)
        try:
            if result.generations:
                gen_message = result.generations[0].message
                if hasattr(gen_message, 'additional_kwargs'):
                    # Check for reasoning_content in various places
                    reasoning = gen_message.additional_kwargs.get('reasoning_content')
                    if reasoning is None:                                              # ← is NONE: try next source
                        reasoning = gen_message.additional_kwargs.get('reasoning')
                    if reasoning is None and hasattr(gen_message, 'response_metadata'):  # ← is NONE: try next source
                        meta = gen_message.response_metadata or {}
                        reasoning = meta.get('reasoning_content') or meta.get('reasoning')

                    if reasoning is not None and hasattr(gen_message, 'additional_kwargs'):  # ← is NOT NONE: store guard
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                        logger.debug(f"[LLM] Extracted reasoning: {str(reasoning)[:100]}...")
                        
        except Exception as e:
            # Don't fail the whole request if thinking extraction fails
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
        
        Providers like DeepSeek require reasoning_content as a top-level field
        in assistant messages when tool calls are involved, for multi-turn reasoning.
        The parent's _convert_message_to_dict() strips this field, so we re-inject it.
        """
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


class SessionState(MessagesState):
    """Extended state schema for agent sessions.
    
    Inherits all message handling from MessagesState (add_messages reducer).
    Adds compaction metadata fields that persist in checkpoints.
    """
    # Compaction dedup: ISO timestamp of last successful compaction
    # Stored/retrieved via graph.aupdate_state() and state.values["compacted_at"]
    compacted_at: str | None = None


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
    
    # Check if model is still outputting thinking/reasoning content.
    # If reasoning_content is present in additional_kwargs, the model is still
    # processing internally and hasn't produced its final answer yet.
    if hasattr(last_message, 'additional_kwargs'):
        reasoning = last_message.additional_kwargs.get('reasoning_content')
        if reasoning:
            logger.debug(f"[Graph] Model still outputting thinking, continuing...")
            return "agent"  # Re-invoke agent to continue processing
    
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


def create_agent_node(
    llm_with_tools,
    system_prompt: str,
    compactor=None,
    graph_ref=None,
    config=None,
    llm_config=None,
    retry_config=None,
    llm_standard=None,
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
            When provided, vision model is used only for FIRST LLM call with images,
            then standard model is used for subsequent calls per DEC-003.
    """
    
    async def agent_node(state):
        messages = state['messages']
        full_messages = [SystemMessage(content=system_prompt)] + list(messages)
        transient = retry_config.get('transient_attempts', 8) if retry_config else 8
        timeout = retry_config.get('timeout_attempts', 3) if retry_config else 3
        
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
        
        # Per DEC-003: Vision model applies to FIRST LLM call only.
        # After first call, use standard model to avoid unnecessary vision model cost.
        # Detect first call by checking if there are any AIMessages in the messages.
        is_first_call = not any(
            hasattr(msg, 'type') and msg.type == 'ai' 
            for msg in messages
        )
        
        # Select the appropriate LLM:
        # - First call with images: use vision model (llm_with_tools which has vision model)
        # - Subsequent calls OR no images: use standard model if available
        use_vision_model = is_first_call and has_images and model_vision and llm_standard is not None
        current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)
        
        model_name = model_vision if use_vision_model else llm_config.get("model", "unknown") if llm_config else "unknown"
        vision_log = f", vision={model_vision}" if model_vision and has_images else ""
        call_type = "VISION" if use_vision_model else "STANDARD"
        logger.info(f'[LLM] Invoking LLM ({call_type}) with {len(full_messages)} messages (model={model_name}, transient_attempts={transient}, timeout_attempts={timeout}{vision_log})')
        
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
        logger.info(f"[Graph] Vision model configured: {model_vision}, will use for FIRST call only per DEC-003")
        # Filter model_vision from config to avoid passing it to the API
        vision_config = {k: v for k, v in llm_config_with_headers.items() if k != "model_vision"}
        vision_config["model"] = model_vision
        llm_with_tools = ThinkingChatOpenAI(**vision_config).bind_tools(tools)
    else:
        logger.info("[Graph] No vision model configured, using standard model for all calls")

    # Create standard LLM (always needed, even if vision is configured)
    # Filter model_vision from config to avoid noisy LangChain warnings
    standard_config = {k: v for k, v in llm_config_with_headers.items() if k != "model_vision"}
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


def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor=None,
    graph_config=None,
):
    """Build and return a compiled instance graph with LLM-level retry.

    Per DEC-003: Vision model applies to FIRST LLM call only.
    When model_vision is configured, we create two LLM instances:
    - llm_with_tools (vision): Used for first call with images
    - llm_standard: Used for subsequent calls (text-only)
    """
    # Add proxy header to all LLM requests
    llm_config_with_headers = {
        **llm_config,
        "default_headers": {"x-proxy-app": "ensemble"},
    }

    # Check if vision model is configured
    model_vision = llm_config.get("model_vision")
    model_standard = llm_config.get("model")

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

    # Add nodes - pass both vision and standard LLM for DEC-003 compliance
    graph.add_node("agent", create_agent_node(
        llm_with_tools,
        system_prompt,
        compactor=compactor,
        graph_ref=graph_ref,
        config=graph_config,
        llm_config=llm_config_with_headers,
        retry_config=retry_config,
        llm_standard=llm_standard,
    ))
    graph.add_node("tools", ToolNode(tools))
    graph.add_node("nudge", nudge_node)
    
    # Add edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {
        "tools": "tools",  # Normal: LLM made tool calls
        "agent": "agent",  # Ghost promise: LLM promised but no tool_call, retry
        "nudge": "nudge",  # Empty after tool: inject prompt to continue
        END: END,
    })
    graph.add_edge("tools", "agent")
    graph.add_edge("nudge", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)
    
    # Late bind graph reference
    graph_ref[0] = compiled
    
    return compiled


# Backward compatibility alias
build_session_graph = build_instance_graph
