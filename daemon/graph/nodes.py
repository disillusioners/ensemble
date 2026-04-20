"""Graph node functions and helpers."""
from langgraph.graph import END, MessagesState
from langchain_core.messages import HumanMessage, SystemMessage
from ..llm_error_classifier import (
    ContextLengthExceededError,
    TransientAPIError,
    TIMEOUT_EXCEPTIONS,
    TRANSIENT_EXCEPTIONS,
)
from ..response_validation import LLMResponseValidationError
import asyncio
import logging
import openai

logger = logging.getLogger(__name__)

# Message injected when LLM returns empty after tool execution
NUDGE_MESSAGE = "Continue with your task, or provide your final response if you are finished."


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
            
            # Lazy import to avoid circular dependency
            from ..compaction import CompactionContext
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
            logger.error(f"[LLM] All retries exhausted ({category}, transient_attempts={transient}, timeout_attempts={timeout}): {type(e).__name__}: {e}")
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
