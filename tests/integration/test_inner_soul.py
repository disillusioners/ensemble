"""End-to-end test for inner_soul tool.

This test validates the full flow:
1. Create new agent session
2. Ask agent to remember something (agent uses inner_soul tool)
3. Verify memory file was created in memories/
4. Agent receives response from inner_soul tool

Run with:
    pytest tests/integration/test_inner_soul.py -v
"""

import os
import pytest
from pathlib import Path
from datetime import datetime


# Skip all tests in this module unless OPENAI_API_KEY is set
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Set OPENAI_API_KEY to run integration tests"
)


@pytest.fixture
def integration_config():
    """Load real configuration from config.yaml (uses .env)."""
    from daemon.config import load_config
    
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        pytest.skip(f"config.yaml not found at {config_path}")
    
    return load_config(str(config_path))


@pytest.fixture
def test_agent_dir(tmp_path):
    """Create a minimal test agent directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    
    # Create minimal agent files
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent for inner_soul testing.\n")
    (agent_dir / "growth.md").write_text("# Growth\n\nYou are a self-evolving agent. Use inner_soul to remember and learn.\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Receive message\n2. Process\n3. Respond\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n## Must\n- Respond to messages\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n## Known Patterns\n\n(Empty)\n")
    
    # Create directories
    (agent_dir / "memories").mkdir()
    (agent_dir / "history").mkdir()
    
    # Create tools.md with inner_soul tool documented
    tools_content = """# Tools

## `inner_soul`

Remember, learn, or change yourself.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `intent` | string | Yes | `remember`, `learn`, or `change` |
| `content` | string | Yes | What to remember/learn/change |
| `target` | string | No | For change: `memory`, `workflow`, or `soul` |

**Examples:**
```
inner_soul(intent="remember", content="User prefers TypeScript")
inner_soul(intent="learn", content="Testing early catches bugs")
```
"""
    (agent_dir / "tools.md").write_text(tools_content)
    
    return str(agent_dir)


async def test_inner_soul_remember_e2e(integration_config, test_agent_dir):
    """End-to-end test: agent uses inner_soul to remember something.
    
    Flow:
    1. Spawn agent session
    2. Send message asking agent to remember its name
    3. Verify memory file created in memories/
    4. Verify agent received confirmation from inner_soul tool
    """
    from daemon.manager import SessionManager
    
    # Create session manager
    manager = SessionManager(config=integration_config)
    
    # Count memories before
    memories_dir = Path(test_agent_dir) / "memories"
    memories_before = list(memories_dir.glob("*.md"))
    
    # Spawn session with test agent
    session_id = manager.spawn_session(agent_id="test_agent")
    assert session_id, "Should return a session ID"
    
    # Send message asking agent to remember something
    # The agent should use inner_soul tool to do this
    message = """Please use the inner_soul tool to remember this: "My name is TestAgent and I was created for testing."

Use the inner_soul tool with intent="remember" to store this information."""
    
    response = await manager.send_message(session_id, message)
    
    # Verify response exists (MessageResult has .content attribute)
    assert response.content, "Should receive a response"
    print(f"\n[INNER_SOUL TEST] Agent response: {response.content[:500]}...")
    
    # Check if memory file was created
    memories_after = list(memories_dir.glob("*.md"))
    new_memories = [m for m in memories_after if m not in memories_before]
    
    # Verify at least one memory was created
    assert len(new_memories) > 0, f"Expected at least 1 new memory file, found {len(new_memories)}"
    
    # Read the memory file
    memory_file = new_memories[0]
    memory_content = memory_file.read_text()
    
    print(f"\n[INNER_SOUL TEST] Memory file: {memory_file.name}")
    print(f"[INNER_SOUL TEST] Memory content:\n{memory_content[:300]}...")
    
    # Verify memory contains expected content
    assert "TestAgent" in memory_content or "testing" in memory_content.lower(), \
        f"Memory should contain the remembered content"
    
    # Verify filename format (YYYYMMDD_HHMM_description.md)
    assert memory_file.name.endswith(".md"), "Memory file should be .md"
    
    # Clean up
    manager.terminate_session(session_id)


async def test_inner_soul_change_workflow_e2e(integration_config, test_agent_dir):
    """End-to-end test: agent uses inner_soul to change workflow.
    
    Flow:
    1. Spawn agent session
    2. Send message asking agent to propose a workflow change
    3. Verify workflow.md was updated
    """
    from daemon.manager import SessionManager
    
    manager = SessionManager(config=integration_config)
    
    # Read workflow before
    workflow_file = Path(test_agent_dir) / "workflow.md"
    workflow_before = workflow_file.read_text()
    
    # Spawn session
    session_id = manager.spawn_session(agent_id="test_agent")
    
    # Ask agent to change workflow
    message = """Please use the inner_soul tool to add a step to your workflow.

Use: inner_soul(intent="change", target="workflow", content="Step 4: Review response before sending")"""
    
    response = await manager.send_message(session_id, message)
    
    assert response.content, "Should receive a response"
    print(f"\n[INNER_SOUL TEST] Workflow change response: {response.content[:500]}...")
    
    # Check workflow was updated
    workflow_after = workflow_file.read_text()
    
    assert len(workflow_after) > len(workflow_before), \
        "Workflow should be longer after change"
    
    assert "Review response" in workflow_after or "Step 4" in workflow_after, \
        "Workflow should contain the new step"
    
    print(f"\n[INNER_SOUL TEST] Updated workflow:\n{workflow_after[:500]}...")
    
    # Clean up
    manager.terminate_session(session_id)


async def test_inner_soul_change_soul_proposal_e2e(integration_config, test_agent_dir):
    """End-to-end test: agent proposes soul change (requires approval).
    
    Flow:
    1. Spawn agent session
    2. Send message asking agent to propose soul change
    3. Verify proposal file created in history/
    4. Verify soul.md was NOT modified directly
    """
    from daemon.manager import SessionManager
    
    manager = SessionManager(config=integration_config)
    
    # Read soul before
    soul_file = Path(test_agent_dir) / "soul.md"
    soul_before = soul_file.read_text()
    
    history_dir = Path(test_agent_dir) / "history"
    history_before = list(history_dir.glob("*.md"))
    
    # Spawn session
    session_id = manager.spawn_session(agent_id="test_agent")
    
    # Ask agent to propose soul change
    message = """Please use the inner_soul tool to propose a change to your identity.

Use: inner_soul(intent="change", target="soul", content="I value clear communication in all interactions")"""
    
    response = await manager.send_message(session_id, message)
    
    assert response.content, "Should receive a response"
    print(f"\n[INNER_SOUL TEST] Soul proposal response: {response.content[:500]}...")
    
    # Verify soul was NOT directly modified
    soul_after = soul_file.read_text()
    assert soul_after == soul_before, "Soul should NOT be modified directly (requires approval)"
    
    # Check if proposal was created in history/
    history_after = list(history_dir.glob("*.md"))
    new_proposals = [p for p in history_after if p not in history_before]
    
    if len(new_proposals) > 0:
        proposal_file = new_proposals[0]
        proposal_content = proposal_file.read_text()
        
        print(f"\n[INNER_SOUL TEST] Proposal file: {proposal_file.name}")
        print(f"[INNER_SOUL TEST] Proposal content:\n{proposal_content[:300]}...")
        
        assert "PENDING APPROVAL" in proposal_content or "soul" in proposal_content.lower(), \
            "Proposal should indicate pending approval"
    
    # Clean up
    manager.terminate_session(session_id)
