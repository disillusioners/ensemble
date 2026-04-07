from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    BaseChatOpenAI,
    _convert_delta_to_message_chunk as _base_convert_delta_to_message_chunk,
)
from langchain_core.messages import AIMessageChunk, BaseMessage, BaseMessageChunk, SystemMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from typing import Any, Mapping, Optional, cast
import asyncio
import logging
import openai

logger = logging.getLogger(__name__)

from .llm_error_classifier import (
    classify_llm_errors,
    ContextLengthExceededError,
    TransientAPIError,
    TRANSIENT_EXCEPTIONS,
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
                    if not reasoning:
                        reasoning = gen_message.additional_kwargs.get('reasoning')
                    if not reasoning and hasattr(gen_message, 'response_metadata'):
                        meta = gen_message.response_metadata or {}
                        reasoning = meta.get('reasoning_content') or meta.get('reasoning')
                    
                    if reasoning and hasattr(gen_message, 'additional_kwargs'):
                        gen_message.additional_kwargs['reasoning_content'] = reasoning
                        logger.debug(f"[LLM] Extracted reasoning: {reasoning[:100] if reasoning else 'none'}...")
                        
        except Exception as e:
            # Don't fail the whole request if thinking extraction fails
            logger.debug(f"[LLM] Could not extract reasoning_content: {e}")
        
        return result

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

        # Call module-level function (ChatOpenAI doesn't override it)
        result = _base_convert_delta_to_message_chunk(_dict, default_class)

        # If we found reasoning_content and the result is an AIMessageChunk, store it
        if reasoning_content and isinstance(result, AIMessageChunk):
            result.additional_kwargs["reasoning_content"] = reasoning_content
            logger.debug(f"[LLM] Stream extracted reasoning_content: {reasoning_content[:50]}...")

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

        usage_metadata: Optional[UsageMetadata] = (
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
    compacted_at: Optional[str] = None


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue or end.
    
    Continues if:
    - LLM returned tool_calls (normal flow)
    - LLM text ends with ':' (promised action but no tool_call emitted - "ghost promise" detection)
    """
    messages = state["messages"]
    last_message = messages[-1]
    
    # Normal case: LLM made tool calls
    if getattr(last_message, 'tool_calls', None):
        return "tools"
    
    # Ghost promise detection: LLM promised action but didn't emit tool_call
    # Common pattern: "Now let me write the document:" (ends with ':')
    content = getattr(last_message, 'content', '') or ''
    if isinstance(content, str) and content.rstrip().endswith(':'):
        logger.warning(f"[Graph] Ghost promise detected, LLM text ends with ':': {content[:100]}...")
        return "tools"
    
    return END


def create_agent_node(
    llm_with_tools,
    system_prompt: str,
    compactor=None,
    graph_ref=None,
    config=None,
    llm_config=None,
    retry_config=None,
):
    """Create the agent node function with optional reactive compaction.
    
    Args:
        llm_with_tools: LLM already bound with tools.
        system_prompt: System prompt to prepend to messages.
        compactor: Optional ContextCompactor for reactive compaction.
        graph_ref: Optional list for late-bound graph reference.
        config: Optional config for compaction.
        llm_config: Optional LLM config for compaction context.
        retry_config: Optional retry configuration for logging.
    """
    
    async def agent_node(state):
        messages = state['messages']
        full_messages = [SystemMessage(content=system_prompt)] + list(messages)
        max_retries = retry_config.get('max_retries', 3) if retry_config else 3
        logger.info(f'[LLM] Invoking LLM with {len(full_messages)} messages (max_retries={max_retries})')
        
        try:
            # Use run_in_executor to avoid blocking the event loop.
            # This allows SSE streaming to continue while LLM processes.
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: llm_with_tools.invoke(full_messages)
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
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: llm_with_tools.invoke(compact_messages)
            )
        except (openai.APITimeoutError, openai.APIConnectionError, ConnectionResetError, 
                BrokenPipeError, ConnectionAbortedError, TransientAPIError, LLMResponseValidationError) as e:
            max_retries = retry_config.get('max_retries', 3) if retry_config else 'N/A'
            logger.error(f"[LLM] All retries exhausted after {max_retries} attempts: {type(e).__name__}: {e}")
            raise
        except Exception as e:
            logger.error(f"[LLM] Unexpected error after retries: {type(e).__name__}: {e}")
            raise
        
        tool_info = ''
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', getattr(tc, 'name', '?')) for tc in response.tool_calls]
            tool_info = f', tools: {tool_names}'
        logger.info(f'[LLM] Response: {response.content[:80] if response.content else "empty"}...{tool_info}')
        return {'messages': [response]}
    
    return agent_node


def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,
    compactor=None,
    graph_config=None,
):
    """Build and return a compiled instance graph with LLM-level retry."""
    # Add proxy header to all LLM requests
    llm_config_with_headers = {
        **llm_config,
        "default_headers": {"x-proxy-app": "ensemble"},
    }
    llm = ThinkingChatOpenAI(**llm_config_with_headers)

    # Bind tools BEFORE wrapping with retry (RunnableRetry doesn't have bind_tools)
    llm_with_tools = llm.bind_tools(tools)

    # Wrap with error classification and retry if config provided
    if retry_config:
        # CRITICAL: classify errors BEFORE with_retry so they can be caught
        llm_with_tools = classify_llm_errors(llm_with_tools)
        
        max_retries = retry_config.get("max_retries", 3)
        llm_with_tools = llm_with_tools.with_retry(
            stop_after_attempt=max_retries,
            retry_if_exception_type=TRANSIENT_EXCEPTIONS,
            wait_exponential_jitter=True,
        )
        logger.debug(f"LLM configured with {max_retries} retries")
    
    # Late binding for graph reference
    graph_ref = [None]
    
    graph = StateGraph(SessionState)
    
    # Add nodes
    graph.add_node("agent", create_agent_node(
        llm_with_tools,
        system_prompt,
        compactor=compactor,
        graph_ref=graph_ref,
        config=graph_config,
        llm_config=llm_config_with_headers,
        retry_config=retry_config,
    ))
    graph.add_node("tools", ToolNode(tools))
    
    # Add edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    compiled = graph.compile(checkpointer=checkpointer)
    
    # Late bind graph reference
    graph_ref[0] = compiled
    
    return compiled


# Backward compatibility alias
build_session_graph = build_instance_graph
