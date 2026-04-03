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
import logging

logger = logging.getLogger(__name__)

# Define transient exceptions for LLM retry
try:
    import openai
    TRANSIENT_EXCEPTIONS = (
        openai.RateLimitError,
        openai.APITimeoutError,
        openai.APIConnectionError,
    )
except ImportError:
    TRANSIENT_EXCEPTIONS = ()


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


def should_continue(state: MessagesState) -> str:
    """Determine if we should continue or end."""
    messages = state["messages"]
    last_message = messages[-1]
    if getattr(last_message, 'tool_calls', None):
        return "tools"
    return END


def create_agent_node(llm_with_tools, system_prompt: str):
    """Create the agent node function.
    
    Args:
        llm_with_tools: LLM already bound with tools.
        system_prompt: System prompt to prepend to messages.
    """
    def agent_node(state: MessagesState) -> dict:
        messages = state["messages"]
        # Prepend system prompt
        full_messages = [SystemMessage(content=system_prompt)] + messages
        logger.debug(f"Invoking LLM with {len(full_messages)} messages")
        response = llm_with_tools.invoke(full_messages)
        tool_info = ""
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_names = [tc.get('name', getattr(tc, 'name', '?')) for tc in response.tool_calls]
            tool_info = f", tools: {tool_names}"
        logger.info(f"LLM response: {response.content[:80] if response.content else 'empty'}...{tool_info}")
        return {"messages": [response]}
    
    return agent_node


def build_instance_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,  # NEW: optional retry config
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

    # Wrap with retry if config provided
    if retry_config:
        max_retries = retry_config.get("max_retries", 3)
        llm_with_tools = llm_with_tools.with_retry(
            stop_after_attempt=max_retries,
            retry_if_exception_type=TRANSIENT_EXCEPTIONS,
            wait_exponential_jitter=True,
        )
        logger.debug(f"LLM configured with {max_retries} retries")
    
    graph = StateGraph(MessagesState)
    
    # Add nodes
    graph.add_node("agent", create_agent_node(llm_with_tools, system_prompt))
    graph.add_node("tools", ToolNode(tools))
    
    # Add edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    return graph.compile(checkpointer=checkpointer)
