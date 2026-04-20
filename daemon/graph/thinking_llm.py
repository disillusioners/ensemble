"""Custom ChatOpenAI with reasoning_content extraction for thinking models."""
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    BaseChatOpenAI,
    _convert_delta_to_message_chunk as _base_convert_delta_to_message_chunk,
)
from langchain_core.messages import AIMessageChunk, BaseMessage, BaseMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_core.outputs.chat_generation import ChatGeneration
from typing import Any, Mapping, Optional
import logging
import openai

logger = logging.getLogger(__name__)


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

        usage_metadata: Optional[Any] = (
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
