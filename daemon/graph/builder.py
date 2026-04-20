"""Graph builder for instance execution."""
import logging
from langgraph.graph import START, END

from . import StateGraph, ToolNode
from .nodes import create_agent_node, should_continue, nudge_node
from .llm_builder import build_instance_llms
from .state import SessionState

logger = logging.getLogger(__name__)


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
