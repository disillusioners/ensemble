from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from typing import Sequence
from langchain_core.messages import BaseMessage

def should_continue(state: MessagesState) -> str:
    """Determine if we should continue or end."""
    messages = state["messages"]
    last_message = messages[-1]
    if last_message.tool_calls:
        return "tools"
    return END

def create_agent_node(llm, tools, system_prompt: str):
    """Create the agent node function."""
    llm_with_tools = llm.bind_tools(tools)
    
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
    system_prompt: str
):
    """Build and return a compiled session graph."""
    llm = ChatOpenAI(**llm_config)
    
    graph = StateGraph(MessagesState)
    
    # Add nodes
    graph.add_node("agent", create_agent_node(llm, tools, system_prompt))
    graph.add_node("tools", ToolNode(tools))
    
    # Add edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    
    return graph.compile(checkpointer=checkpointer)
