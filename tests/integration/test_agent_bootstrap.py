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
    developer_agent_dir = project_root / "agents" / "developer"
    
    if not developer_agent_dir.exists():
        pytest.skip(f"Developer agent not found at {developer_agent_dir}")
    
    prompts = load_agent_prompts(developer_agent_dir)
    system_prompt = compose_system_prompt(prompts)
    
    return system_prompt


def test_agent_bootstrap_and_hello(integration_config, agent_system_prompt):
    """Bootstrap an agent and send a hello message, waiting for real LLM response.
    
    This test:
    1. Loads real config from .env via config.yaml
    2. Builds a LangGraph agent with the developer agent's system prompt
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

    wc-wake-report-integrity T6b completion (2026-08-30): this test
    previously round-tripped a message through the deleted
    ``Manager.send_message`` (C1-D7). It now enqueues via
    ``manager.enqueue_message`` (the durable wake path — asserting the
    ``AsyncMessageResult`` contract) and then drives the REAL engine
    turn through ``_process_message_with_tracking`` (the same pipeline
    the WorkerPool's processor runs; a bare InstanceManager has no
    worker pool). The LLM-response assertions are unchanged in spirit:
    the turn must produce a non-empty response.
    """
    from daemon.manager import InstanceManager

    project_root = Path(__file__).parent.parent.parent
    developer_agent_dir = str(project_root / "agents" / "developer")

    # Use in-memory database for test isolation
    integration_config.persistence.db_path = ":memory:"

    # Create instance manager
    manager = InstanceManager(
        config=integration_config,
    )

    # Spawn an instance with the developer agent
    instance_id, _ = manager.spawn_instance(agent_id="developer")

    assert instance_id, "Should return an instance ID"
    assert instance_id in manager.instances, "Instance should be registered"

    # Enqueue hello message via the durable wake path
    message = "Hello! Please respond briefly."
    enqueue_result = await manager.enqueue_message(instance_id, message, source="api")

    assert enqueue_result is not None, "enqueue must return an AsyncMessageResult"
    assert enqueue_result.message_id, "enqueue must mint a durable message_id"
    assert enqueue_result.instance_id == instance_id
    assert enqueue_result.status == "queued"

    # Drive the real-engine turn (the WorkerPool processor's pipeline)
    response = await manager._messaging_service._process_message_with_tracking(
        instance_id=instance_id,
        message=message,
        message_id=enqueue_result.message_id,
    )

    # Validate response
    assert response, "Should receive a non-empty response"
    assert response.content, "Response content should not be empty"
    assert len(response.content) > 10, "Response seems too short"

    print(f"\n[INTEGRATION TEST] InstanceManager Response: {response.content[:200]}...")

    # Clean up
    manager.terminate_instance(instance_id)
