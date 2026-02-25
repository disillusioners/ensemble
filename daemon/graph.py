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
    """Custom ChatOpenAI that captures reasoning_content from OpenAI-compatible APIs."""
    
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Override to capture reasoning_content from raw response."""
        # Call parent implementation
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        
        # Try to get raw response with reasoning_content
        try:
            # Get the bound client
            client = self.client
            
            # Convert messages to dict format
            message_dicts = [
                {"role": "system" if m.type == "system" else "user" if m.type == "human" else "assistant", 
                 "content": m.content}
                for m in messages
            ]
            
            # Build params
            params = dict(self._default_params)
            if stop:
                params["stop"] = stop
            params.update(kwargs)
            
            # Make raw request
            raw_response = client.create(
                messages=message_dicts,
                **params,
            )
            
            # Extract reasoning_content from raw response
            if raw_response.choices:
                raw_message = raw_response.choices[0].message
                reasoning_content = getattr(raw_message, 'reasoning_content', None)
                
                if reasoning_content and result.generations:
                    # Store in the first generation's message additional_kwargs
                    gen_message = result.generations[0].message
                    if hasattr(gen_message, 'additional_kwargs'):
                        gen_message.additional_kwargs['reasoning_content'] = reasoning_content
                        
        except Exception as e:
            # Don't fail the whole request if thinking extraction fails
            print(f"[DEBUG] Failed to extract reasoning_content: {e}")
        
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
        response = llm_with_tools.invoke(full_messages)
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
