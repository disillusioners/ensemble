"""Pytest configuration and common fixtures for daemon tests."""

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime
from types import ModuleType
from unittest.mock import MagicMock, AsyncMock


# Create mock modules and add to sys.modules BEFORE any daemon imports
def create_mock_module(name: str, attrs: dict = None) -> ModuleType:
    mod = ModuleType(name)
    if attrs:
        for k, v in attrs.items():
            setattr(mod, k, v)
    return mod


# Create mock langgraph modules
mock_langgraph = create_mock_module("langgraph", {"__path__": []})
mock_langgraph_graph = create_mock_module("langgraph.graph", {
    "StateGraph": MagicMock(),
    "MessagesState": MagicMock(),
    "START": MagicMock(),
    "END": MagicMock(),
    "CompiledGraph": MagicMock(),
})
mock_langgraph_graph_state = create_mock_module("langgraph.graph.state", {
    "CompiledStateGraph": MagicMock(),
})
mock_langgraph_prebuilt = create_mock_module("langgraph.prebuilt", {
    "ToolNode": MagicMock()
})
mock_langgraph_constants = create_mock_module("langgraph.constants", {
    "CompiledGraph": MagicMock()
})
mock_langgraph_checkpoint = create_mock_module("langgraph.checkpoint")
mock_langgraph_checkpoint_sqlite = create_mock_module("langgraph.checkpoint.sqlite", {
    "SqliteSaver": MagicMock()
})
mock_langgraph_checkpoint_sqlite_aio = create_mock_module("langgraph.checkpoint.sqlite.aio", {
    "AsyncSqliteSaver": MagicMock()
})

# Create mock MCP module (for testing without mcp package installed)
mock_mcp_tool_adapter = create_mock_module("daemon.mcp.tool_adapter", {
    "mcp_tool_name": lambda server_name, tool_name: f"mcp_{server_name}_{tool_name}",
    "is_mcp_tool": lambda name: name.startswith("mcp_") and "_" in name[4:] if name else False,
    "adapt_mcp_tools": lambda server_name, tools: tools,
    "_slugify": lambda name: name.lower().replace("-", "_").replace(" ", "_"),
})

# Create mock MCP SDK module (mcp package)
mock_mcp = create_mock_module("mcp", {"__path__": ["mcp"]})
mock_mcp.ClientSession = MagicMock()
mock_mcp.StdioServerParameters = MagicMock()
mock_mcp.stdio_client = MagicMock()
mock_mcp_client = create_mock_module("mcp.client", {"__path__": []})
mock_mcp_client.sse = create_mock_module("mcp.client.sse", {
    "sse_client": MagicMock(),
})
mock_mcp_client.streamable_http = create_mock_module("mcp.client.streamable_http", {
    "streamablehttp_client": MagicMock(),
})
mock_mcp_client_stdio = create_mock_module("mcp.client.stdio", {})
mock_mcp_server = create_mock_module("mcp.server", {"__path__": []})
mock_mcp_server.stdio = create_mock_module("mcp.server.stdio", {})
mock_mcp_types = create_mock_module("mcp.types", {
    "TextResourceContents": MagicMock(),
    "ImageResourceContents": MagicMock(),
    "EmbeddedResource": MagicMock(),
})
mock_mcp_stdio_client = create_mock_module("mcp.client.stdio.context_manager", {
    "__aenter__": AsyncMock(return_value=MagicMock()),
    "__aexit__": AsyncMock(return_value=None),
})

# Create mock langchain_mcp_adapters module
mock_langchain_mcp = create_mock_module("langchain_mcp_adapters", {"__path__": []})
mock_langchain_mcp_tools = create_mock_module("langchain_mcp_adapters.tools", {
    "load_mcp_tools": AsyncMock(return_value=[]),
})


# Save and replace modules (only for non-integration tests)
_original_modules = {}
_mock_modules = {
    "langgraph": mock_langgraph,
    "langgraph.graph": mock_langgraph_graph,
    "langgraph.graph.state": mock_langgraph_graph_state,
    "langgraph.prebuilt": mock_langgraph_prebuilt,
    "langgraph.constants": mock_langgraph_constants,
    "langgraph.checkpoint": mock_langgraph_checkpoint,
    "langgraph.checkpoint.sqlite": mock_langgraph_checkpoint_sqlite,
    "langgraph.checkpoint.sqlite.aio": mock_langgraph_checkpoint_sqlite_aio,
    "daemon.mcp.tool_adapter": mock_mcp_tool_adapter,
    # Mock MCP SDK modules
    "mcp": mock_mcp,
    "mcp.client": mock_mcp_client,
    "mcp.client.sse": mock_mcp_client.sse,
    "mcp.client.streamable_http": mock_mcp_client.streamable_http,
    "mcp.client.stdio": mock_mcp_client_stdio,
    "mcp.server": mock_mcp_server,
    "mcp.server.stdio": mock_mcp_server.stdio,
    "mcp.types": mock_mcp_types,
    "mcp.client.stdio.context_manager": mock_mcp_stdio_client,
    # Mock langchain_mcp_adapters
    "langchain_mcp_adapters": mock_langchain_mcp,
    "langchain_mcp_adapters.tools": mock_langchain_mcp_tools,
}

# Inject mocks into sys.modules BEFORE any test imports happen
for key, mock_mod in _mock_modules.items():
    if key not in sys.modules:
        sys.modules[key] = mock_mod


def pytest_collection_modifyitems(items):
    """Only apply mocks for unit tests, not integration tests."""
    for item in items:
        if "integration" not in item.fspath.strpath:
            # Ensure mocks are applied for unit tests
            for key in _mock_modules:
                if key not in sys.modules:
                    sys.modules[key] = _mock_modules[key]
        else:
            # Restore real modules for integration tests
            for key in _mock_modules:
                if key in _original_modules:
                    sys.modules[key] = _original_modules[key]
                elif key in sys.modules and sys.modules[key] is _mock_modules.get(key):
                    del sys.modules[key]


@pytest.fixture
def sample_config_yaml():
    """Sample YAML configuration content."""
    return """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"
  temperature: 0.7

daemon:
  host: "0.0.0.0"
  port: 8079

limits:
  max_instances: 100
  max_children_per_instance: 10
  instance_timeout_minutes: 60
  message_rate_limit: 60

persistence:
  db_path: "./data/instances.db"
  checkpoint_interval: 1
  checkpoint_ttl_hours: 168
  checkpoint_cleanup_interval: 24
  checkpoint_max_count: 1000

agents:
  directory: "./agents"
"""


@pytest.fixture
def sample_config_with_env_vars():
    """Sample YAML configuration with environment variable placeholders."""
    return """
llm:
  base_url: "${CUSTOM_LLM_URL:-https://api.openai.com/v1}"
  api_key: "${LLM_API_KEY:-default-key}"
  model: "gpt-4"

daemon:
  host: "${DAEMON_HOST:-0.0.0.0}"
  port: ${DAEMON_PORT:-8079}

limits:
  max_instances: ${MAX_INSTANCES:-100}
"""


@pytest.fixture
def sample_instance_info_data():
    """Sample InstanceInfo data for testing."""
    return {
        "instance_id": "test-instance-123",
        "agent_id": "coder",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "created_at": datetime(2024, 1, 1, 0, 0, 0),
        "updated_at": datetime(2024, 1, 1, 0, 1, 0),
    }


@pytest.fixture
def sample_message_response_data():
    """Sample MessageResponse data for testing."""
    return {
        "message_id": "msg-456",
        "role": "assistant",
        "content": "Hello! How can I help you?",
        "tool_calls": [
            {
                "id": "call_123",
                "type": "function",
                "function": {
                    "name": "some_tool",
                    "arguments": '{"arg1": "value1"}'
                }
            }
        ],
        "created_at": datetime(2024, 1, 1, 0, 0, 0),
    }


@pytest.fixture
def sample_error_response_data():
    """Sample ErrorResponse data for testing."""
    return {
        "code": "INVALID_REQUEST",
        "message": "The request body is invalid",
        "details": {"field": "agent_dir", "reason": "required field"},
    }


@pytest.fixture
def sample_health_response_data():
    """Sample HealthResponse data for testing."""
    return {
        "status": "healthy",
        "uptime_seconds": 3600.0,
        "version": "1.0.0",
    }


@pytest.fixture
def sample_instance_create_data():
    """Sample InstanceCreate data for testing."""
    return {
        "agent_id": "coder",
    }


@pytest.fixture
def sample_instance_create_with_instance_id():
    """Sample InstanceCreate data with custom instance_id."""
    return {
        "agent_id": "coder",
        "instance_id": "custom-instance-123",
    }


@pytest.fixture
def sample_message_create_data():
    """Sample MessageCreate data for testing."""
    return {
        "content": "Hello, agent!",
    }


@pytest.fixture(autouse=True)
def clean_env():
    """Clean up environment variables before and after each test."""
    # Store original env vars
    original_env = os.environ.copy()

    yield

    # Restore original env (but don't restore ENSEMBLE_CONFIG as tests may modify it)
    for key in os.environ:
        if key not in original_env:
            del os.environ[key]

    for key, value in original_env.items():
        if key != "ENSEMBLE_CONFIG" and os.environ.get(key) != value:
            os.environ[key] = value


# ==================== Scheduler Test Fixtures ====================


@pytest.fixture
def mock_on_message():
    """Create a mock async callback for message handling."""
    return AsyncMock()


@pytest.fixture
def mock_execution_callback():
    """Create a mock execution callback."""
    from unittest.mock import Mock
    return Mock()


@pytest.fixture
def mock_source_repo():
    """Create a mock SourceRepository with run counter support."""
    repo = MagicMock()
    repo.increment_scheduler_run_counter = MagicMock(return_value=1)
    return repo


def make_config(source_id: str, config: dict):
    """Helper to create SourceConfig for scheduler tests."""
    from daemon.sources.base import SourceConfig
    return SourceConfig(
        source_id=source_id,
        source_type="scheduler",
        name=f"Test Scheduler {source_id}",
        config=config,
        credentials={},
        enabled=True,
    )
