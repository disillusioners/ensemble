from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from typing import Any
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


def build_session_graph(
    tools: list,
    checkpointer,
    llm_config: dict,
    system_prompt: str,
    retry_config: dict | None = None,  # NEW: optional retry config
):
    """Build and return a compiled session graph with LLM-level retry."""
    llm = ThinkingChatOpenAI(**llm_config)

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
