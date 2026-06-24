"""Integration test that bootstraps a real agent and sends a hello message.

This test validates end-to-end functionality with real LLM API calls.
It requires a valid .env file with OPENAI_API_KEY set.

Run with:
    pytest tests/integration/test_agent_bootstrap.py -v -m integration
"""

import os
import pytest
from pathlib import Path

# All tests in this file require live LLM infrastructure (real OpenAI API + MCP),
# so they are excluded from the default non-integration test gate via the
# `integration` marker defined in pyproject.toml.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="Set OPENAI_API_KEY to run integration tests"
    ),
]


@pytest.fixture
def integration_config():
    """Load real configuration from config.yaml (uses .env)."""
    from daemon.config import load_config
    
    # Ensure we're using the project's config.yaml
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        pytest.skip(f"config.yaml not found at {config_path}")
    
    return load_config(str(config_path))


@pytest.fixture
def agent_system_prompt():
    """Load a simple system prompt for testing."""
    from daemon.loader import load_agent_prompts, compose_system_prompt
    
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = project_root / "agents" / "coder"
    
    if not coder_agent_dir.exists():
        pytest.skip(f"Coder agent not found at {coder_agent_dir}")
    
    prompts = load_agent_prompts(coder_agent_dir)
    system_prompt = compose_system_prompt(prompts)
    
    return system_prompt


def test_agent_bootstrap_and_hello(integration_config, agent_system_prompt):
    """Bootstrap an agent and send a hello message, waiting for real LLM response.
    
    This test:
    1. Loads real config from .env via config.yaml
    2. Builds a LangGraph agent with the coder agent's system prompt
    3. Sends "Hello!" message
    4. Validates we receive a non-empty response from the LLM
    """
    from langgraph.graph import StateGraph, MessagesState, START, END
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage
    
    # Build LLM config from loaded settings
    llm_config = {
        "base_url": integration_config.llm.base_url,
        "api_key": integration_config.llm.api_key,
        "model": integration_config.llm.model,
        "temperature": integration_config.llm.temperature,
    }
    
    # Create LLM instance
    llm = ChatOpenAI(**llm_config)
    
    # Create a simple agent node (no tools for this basic test)
    def agent_node(state: MessagesState) -> dict:
        messages = state["messages"]
        full_messages = [SystemMessage(content=agent_system_prompt)] + messages
        response = llm.invoke(full_messages)
        return {"messages": [response]}
    
    # Build minimal graph
    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    
    compiled_graph = graph.compile()
    
    # Send hello message
    result = compiled_graph.invoke({"messages": [HumanMessage(content="Hello!")]})
    
    # Validate response
    assert "messages" in result, "Result should contain messages"
    messages = result["messages"]
    assert len(messages) > 0, "Should have at least one response message"
    
    # Get the last message (the LLM's response)
    last_message = messages[-1]
    
    # Verify it's an AI response with content
    assert hasattr(last_message, "content"), "Response should have content"
    assert last_message.content, "Response content should not be empty"
    
    # Log the response for debugging
    print(f"\n[INTEGRATION TEST] LLM Response: {last_message.content[:200]}...")
    
    # Basic sanity check - response should contain some meaningful text
    assert len(last_message.content) > 10, "Response seems too short to be meaningful"


async def test_agent_bootstrap_with_instance_manager(integration_config, agent_system_prompt):
    """Test using the full InstanceManager to bootstrap an agent.
    
    This validates the complete flow used by the actual application.
    """
    from daemon.manager import InstanceManager
    from daemon.persistence import PersistenceManager
    from daemon.loader import PromptCache
    
    project_root = Path(__file__).parent.parent.parent
    coder_agent_dir = str(project_root / "agents" / "coder")
    
    # Create persistence manager with in-memory database
    persistence = PersistenceManager(db_path=":memory:")
    
    # Create instance manager
    manager = InstanceManager(
        config=integration_config,
        persistence=persistence,
        prompt_cache=PromptCache()
    )
    
    # Spawn an instance with the coder agent
    instance_id = manager.spawn_instance(agent_id="coder")
    
    assert instance_id, "Should return an instance ID"
    assert instance_id in manager.instances, "Instance should be registered"
    
    # Send hello message
    response = await manager.send_message(instance_id, "Hello! Please respond briefly.")
    
    # Validate response
    assert response, "Should receive a non-empty response"
    assert len(response) > 10, "Response seems too short"
    
    print(f"\n[INTEGRATION TEST] InstanceManager Response: {response[:200]}...")
    
    # Clean up
    manager.terminate_instance(instance_id)
