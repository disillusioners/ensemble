"""Pytest configuration for integration tests.

This conftest DOES NOT mock langgraph, allowing real LLM calls.
The parent conftest.py mocks langgraph for unit tests.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from daemon.registry import AgentMetadata


@pytest.fixture(autouse=True, scope="function")
def patch_normalize_project_id(request):
    """Patch normalize_project_id to read SYSTEM_DEFAULT_PROJECT_ID dynamically.
    
    Only applies to tests in the integration directory.
    The function imports SYSTEM_DEFAULT_PROJECT_ID at import time, so patching
    the module attribute doesn't affect the already-captured reference.
    This fixture patches the function itself to read the constant dynamically.
    
    We need to patch in ALL modules that import normalize_project_id because
    Python's `from module import name` creates a local binding.
    """
    import sys
    
    # Only apply to tests in the integration directory
    integration_dir = Path(__file__).parent
    test_path = str(request.fspath)
    
    if str(integration_dir) not in test_path:
        return  # Skip for non-integration tests
    
    import daemon.services.project_normalizer as normalizer_module
    import daemon.routers.schemas as schemas_module
    import daemon.routers.jobs_crud as jobs_crud_module
    import daemon.services.job_queue_service as job_queue_service_module
    from daemon import constants
    
    # Save the original functions
    original_normalize = normalizer_module.normalize_project_id
    
    def patched_normalize(project_id: str | None) -> str:
        """Patched version that reads SYSTEM_DEFAULT_PROJECT_ID dynamically."""
        if constants.SYSTEM_DEFAULT_PROJECT_ID is None:
            raise RuntimeError(
                "normalize_project_id() called before system default project was initialized"
            )
        
        if project_id is None:
            return constants.SYSTEM_DEFAULT_PROJECT_ID
        
        normalized = project_id.strip()
        if normalized == "":
            return constants.SYSTEM_DEFAULT_PROJECT_ID
        
        lower = normalized.lower()
        if lower in ("null", "none"):
            return constants.SYSTEM_DEFAULT_PROJECT_ID
        
        return project_id
    
    # Patch the function in ALL importing modules
    normalizer_module.normalize_project_id = patched_normalize
    schemas_module.normalize_project_id = patched_normalize
    jobs_crud_module.normalize_project_id = patched_normalize
    job_queue_service_module.normalize_project_id = patched_normalize
    
    yield
    
    # Restore originals
    normalizer_module.normalize_project_id = original_normalize
    schemas_module.normalize_project_id = original_normalize
    jobs_crud_module.normalize_project_id = original_normalize
    job_queue_service_module.normalize_project_id = original_normalize


@pytest.fixture
def integration_config(tmp_path):
    """Load real configuration from config.yaml (uses .env) with temp database paths."""
    from daemon.config import load_config
    
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.yaml"
    
    if not config_path.exists():
        pytest.skip(f"config.yaml not found at {config_path}")
    
    config = load_config(str(config_path))
    
    # Override database paths to use temp directories to avoid conflicts
    # when tests run in different working directories
    config.persistence.db_path = str(tmp_path / "instances.db")
    # Checkpointer path is set via ensemble_config, not persistence config.

    return config


@pytest.fixture
def test_agent_dir(tmp_path):
    """Create a minimal test agent directory.
    
    Returns the path to the agent directory.
    """
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


@pytest.fixture
def mock_registry_with_test_agent(test_agent_dir):
    """Create a mock registry that includes the test agent."""
    agent_metadata = AgentMetadata(
        id="test_agent",
        name="Test Agent",
        description="Test agent for inner_soul testing",
        path=Path(test_agent_dir),
        system=False,
    )
    
    mock_registry = MagicMock()
    mock_registry.resolve_to_id.return_value = "test_agent"
    mock_registry.get.return_value = agent_metadata
    
    return mock_registry
