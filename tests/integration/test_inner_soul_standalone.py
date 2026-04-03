#!/usr/bin/env python
"""Standalone test for inner_soul tool - runs outside pytest to avoid conftest mocks.

Usage:
    python tests/integration/test_inner_soul_standalone.py
"""

import os
import sys
import shutil
import asyncio
from pathlib import Path
from datetime import datetime

import pytest
import socket


def _load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key] = value


# Load .env first so checks can see the key
_load_env()


def _has_valid_api_key() -> bool:
    """Check if OPENAI_API_KEY is set and not a placeholder."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    
    # Skip if empty or looks like a comment
    if not api_key or api_key.startswith("#"):
        return False
    
    # Skip obvious placeholders (contains placeholder words, too short, or looks fake)
    placeholder_patterns = [
        "your-key", "placeholder", "fake", "test-key", "sk-test", 
        "example", "dummy", "sk-000", "sk-xxxx", "sk-null"
    ]
    key_lower = api_key.lower()
    if any(p in key_lower for p in placeholder_patterns):
        return False
    
    # OpenAI keys are typically 50+ chars, skip suspiciously short ones
    if len(api_key) < 40:
        return False
    
    # Skip keys that are all zeros or sequential patterns (fake keys)
    if api_key.replace("sk-", "").replace("0", "").replace("-", "").replace("_", "") == "":
        return False
    
    return True


def _should_skip_llm_tests() -> bool:
    """Check if LLM tests should be skipped (opt-out for CI/automated environments)."""
    # Allow explicit skip via environment variable
    if os.environ.get("SKIP_LLM_TESTS", "").lower() in ("1", "true", "yes"):
        return True
    return False


def _is_local_server_reachable() -> bool:
    """Check if local LLM server is reachable."""
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if not base_url:
        return True  # Assume reachable if not specified (will use default)
    
    # Only check localhost URLs
    if "localhost" in base_url or "127.0.0.1" in base_url:
        try:
            # Extract host and port
            import re
            match = re.search(r"://([^:/]+):?(\d*)", base_url)
            if match:
                host = match.group(1)
                port = match.group(2) or "80"
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex((host, int(port)))
                sock.close()
                return result == 0
        except Exception:
            pass
    return True  # Assume reachable for non-local servers


def _needs_llm_tests() -> bool:
    """Check if we should run LLM integration tests."""
    return not (_should_skip_llm_tests() or not _has_valid_api_key())


skip_llm_tests = pytest.mark.skipif(
    not _needs_llm_tests(),
    reason="Requires valid OPENAI_API_KEY environment variable"
)

requires_local_server = pytest.mark.skipif(
    not _is_local_server_reachable(),
    reason="Requires local LLM server running at OPENAI_BASE_URL"
)

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Now import daemon modules (after env is loaded)
from daemon.config import load_config
from daemon.manager import InstanceManager


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


def _get_config():
    """Load config or raise FileNotFoundError with helpful message."""
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}. "
            "Set ENSEMBLE_CONFIG env var or create config.yaml"
        )
    
    return load_config(str(config_path))


@pytest.fixture
def integration_config():
    """Load real configuration from config.yaml (uses .env)."""
    return _get_config()


async def _run_remember_test(config):
    """Run the remember test logic."""
    project_root = Path(__file__).parent.parent.parent
    
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
    
    # Create instance manager
    manager = InstanceManager(config=config)
    
    # Spawn instance
    print(f"Spawning instance with agent: {agent_dir}")
    instance_id = manager.spawn_instance(agent_id="coder")
    print(f"Instance ID: {instance_id}")
    
    # Send message asking agent to remember
    message = """Please use the inner_soul tool to remember: "My name is TestAgent"

Call inner_soul with intent="remember" and the content above."""
    
    print(f"\nSending message: {message[:100]}...")
    response = await manager.send_message(instance_id, message)
    
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
    manager.terminate_instance(instance_id)
    shutil.rmtree(tmp_dir)
    
    # Assert
    if len(new_memories) > 0:
        print("\n✅ TEST PASSED: Memory file was created")
        return True
    else:
        print("\n❌ TEST FAILED: No memory file was created")
        return False


@skip_llm_tests
@requires_local_server
@pytest.mark.asyncio
async def test_inner_soul_remember(integration_config):
    """Test: agent uses inner_soul to remember something."""
    print("\n" + "="*60)
    print("TEST: inner_soul remember")
    print("="*60)
    return await _run_remember_test(integration_config)


async def _run_workflow_test(config):
    """Run the workflow change test logic."""
    project_root = Path(__file__).parent.parent.parent
    
    tmp_dir = project_root / "tmp_test"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()
    
    agent_dir = create_test_agent(tmp_dir)
    workflow_file = Path(agent_dir) / "workflow.md"
    workflow_before = workflow_file.read_text()
    
    manager = InstanceManager(config=config)
    instance_id = manager.spawn_instance(agent_id="coder")
    
    message = """Use the inner_soul tool to add a workflow step.

Call: inner_soul(intent="change", target="workflow", content="Step 4: Review before responding")"""
    
    print(f"Sending message...")
    response = await manager.send_message(instance_id, message)
    print(f"Response: {response.content[:300]}...")
    
    workflow_after = workflow_file.read_text()
    
    manager.terminate_instance(instance_id)
    shutil.rmtree(tmp_dir)
    
    if len(workflow_after) > len(workflow_before):
        print("\n✅ TEST PASSED: Workflow was updated")
        return True
    else:
        print("\n❌ TEST FAILED: Workflow was not updated")
        return False


@skip_llm_tests
@requires_local_server
@pytest.mark.asyncio
async def test_inner_soul_workflow_change(integration_config):
    """Test: agent uses inner_soul to change workflow."""
    print("\n" + "="*60)
    print("TEST: inner_soul change workflow")
    print("="*60)
    return await _run_workflow_test(integration_config)


if __name__ == "__main__":
    print("="*60)
    print("INNER_SOUL END-TO-END TEST")
    print("="*60)
    
    # Check for config first
    try:
        config = _get_config()
    except FileNotFoundError as e:
        print(f"\n❌ SKIPPED: {e}")
        sys.exit(0)  # Exit gracefully for CI/automation
    
    results = []
    
    # Run tests using asyncio
    results.append(("remember", asyncio.run(test_inner_soul_remember(config))))
    results.append(("workflow", asyncio.run(test_inner_soul_workflow_change(config))))
    
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
