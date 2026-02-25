#!/usr/bin/env python
"""Standalone test for inner_soul tool - runs outside pytest to avoid conftest mocks.

Usage:
    python tests/integration/test_inner_soul_standalone.py
"""

import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# Load .env file
def load_env():
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key] = value

load_env()

if not os.environ.get("OPENAI_API_KEY"):
    print("ERROR: OPENAI_API_KEY not set. Check .env file.")
    sys.exit(1)

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Now import daemon modules (after env is loaded)
from daemon.config import load_config
from daemon.manager import SessionManager


def create_test_agent(tmp_dir: Path) -> str:
    """Create a minimal test agent directory."""
    agent_dir = tmp_dir / "test_agent"
    agent_dir.mkdir()
    
    # Create minimal agent files
    (agent_dir / "soul.md").write_text("""# Who I Am

I am a test agent. When asked to remember something, I use the inner_soul tool.
""")
    (agent_dir / "growth.md").write_text("# Growth\n\nYou are a self-evolving agent. Use inner_soul to remember and learn.\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Receive message\n2. Use tools as needed\n3. Respond\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n## Must\n- Use inner_soul tool when asked to remember\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n## Known Patterns\n\n(Empty)\n")
    
    # Create directories
    (agent_dir / "memories").mkdir()
    (agent_dir / "history").mkdir()
    
    # Create tools.md with inner_soul documentation
    tools_content = """# Tools

## `inner_soul`

Remember, learn, or change yourself.

**Parameters:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `intent` | string | Yes | `remember`, `learn`, or `change` |
| `content` | string | Yes | What to remember/learn/change |
| `target` | string | No | For change: `memory`, `workflow`, or `soul` |

**Example:**
```
inner_soul(intent="remember", content="User prefers TypeScript")
```
"""
    (agent_dir / "tools.md").write_text(tools_content)
    
    return str(agent_dir)


def test_inner_soul_remember():
    """Test: agent uses inner_soul to remember something."""
    print("\n" + "="*60)
    print("TEST: inner_soul remember")
    print("="*60)
    
    # Setup
    project_root = Path(__file__).parent.parent.parent
    config = load_config(str(project_root / "config.yaml"))
    
    # Create temp agent directory
    tmp_dir = project_root / "tmp_test"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    
    agent_dir = create_test_agent(tmp_dir)
    memories_dir = Path(agent_dir) / "memories"
    
    # Count memories before
    memories_before = list(memories_dir.glob("*.md"))
    print(f"Memories before: {len(memories_before)}")
    
    # Create session manager
    manager = SessionManager(config=config)
    
    # Spawn session
    print(f"Spawning session with agent: {agent_dir}")
    session_id = manager.spawn_session(agent_dir=agent_dir)
    print(f"Session ID: {session_id}")
    
    # Send message asking agent to remember
    message = """Please use the inner_soul tool to remember: "My name is TestAgent"

Call inner_soul with intent="remember" and the content above."""
    
    print(f"\nSending message: {message[:100]}...")
    response = manager.send_message(session_id, message)
    
    print(f"\nAgent response:\n{response.content[:500]}...")
    if response.tool_calls:
        print(f"Tool calls made: {response.tool_calls}")
    
    # Check memories
    memories_after = list(memories_dir.glob("*.md"))
    new_memories = [m for m in memories_after if m not in memories_before]
    
    print(f"\nMemories after: {len(memories_after)}")
    print(f"New memories: {len(new_memories)}")
    
    if new_memories:
        memory_file = new_memories[0]
        print(f"\nMemory file created: {memory_file.name}")
        print(f"Content:\n{memory_file.read_text()[:500]}...")
    
    # Cleanup
    manager.terminate_session(session_id)
    shutil.rmtree(tmp_dir)
    
    # Assert
    if len(new_memories) > 0:
        print("\n✅ TEST PASSED: Memory file was created")
        return True
    else:
        print("\n❌ TEST FAILED: No memory file was created")
        return False


def test_inner_soul_workflow_change():
    """Test: agent uses inner_soul to change workflow."""
    print("\n" + "="*60)
    print("TEST: inner_soul change workflow")
    print("="*60)
    
    # Setup
    project_root = Path(__file__).parent.parent.parent
    config = load_config(str(project_root / "config.yaml"))
    
    tmp_dir = project_root / "tmp_test"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    
    agent_dir = create_test_agent(tmp_dir)
    workflow_file = Path(agent_dir) / "workflow.md"
    workflow_before = workflow_file.read_text()
    
    manager = SessionManager(config=config)
    session_id = manager.spawn_session(agent_dir=agent_dir)
    
    message = """Use the inner_soul tool to add a workflow step.

Call: inner_soul(intent="change", target="workflow", content="Step 4: Review before responding")"""
    
    print(f"Sending message...")
    response = manager.send_message(session_id, message)
    print(f"Response: {response.content[:300]}...")
    
    workflow_after = workflow_file.read_text()
    
    manager.terminate_session(session_id)
    shutil.rmtree(tmp_dir)
    
    if len(workflow_after) > len(workflow_before):
        print("\n✅ TEST PASSED: Workflow was updated")
        return True
    else:
        print("\n❌ TEST FAILED: Workflow was not updated")
        return False


if __name__ == "__main__":
    print("="*60)
    print("INNER_SOUL END-TO-END TEST")
    print("="*60)
    
    results = []
    
    # Run tests
    results.append(("remember", test_inner_soul_remember()))
    results.append(("workflow", test_inner_soul_workflow_change()))
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
    
    total = len(results)
    passed = sum(1 for _, p in results if p)
    print(f"\nTotal: {passed}/{total} passed")
    
    sys.exit(0 if passed == total else 1)
