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
    "END": "__end__",  # Must be string for should_continue() and graph building
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
mock_langgraph_checkpoint = create_mock_module("langgraph.checkpoint", {"__path__": []})
mock_langgraph_checkpoint_memory = create_mock_module("langgraph.checkpoint.memory", {
    "CheckpointTuple": MagicMock()
})
mock_langgraph_checkpoint_sqlite = create_mock_module("langgraph.checkpoint.sqlite", {
    "SqliteSaver": MagicMock()
})
mock_langgraph_checkpoint_sqlite_aio = create_mock_module("langgraph.checkpoint.sqlite.aio", {
    "AsyncSqliteSaver": MagicMock()
})

# Create mock MCP SDK module (mcp package)
mock_mcp_tool_adapter = create_mock_module("daemon.mcp.tool_adapter", {
    "mcp_tool_name": lambda server_name, tool_name: f"mcp_{server_name}_{tool_name}",
    "is_mcp_tool": lambda name: name.startswith("mcp_") and "_" in name[4:] if name else False,
    "adapt_mcp_tools": lambda server_name, tools, tool_call_timeout=120: tools,
    "_slugify": lambda name: name.lower().replace("-", "_").replace(" ", "_"),
})

# Create mock MCP SDK module (mcp package)
mock_mcp = create_mock_module("mcp", {"__path__": ["mcp"]})
mock_mcp.ClientSession = MagicMock()
mock_mcp.StdioServerParameters = MagicMock()
mock_mcp.ListToolsResult = MagicMock()
mock_mcp.Tool = MagicMock()
mock_mcp.stdio_client = MagicMock()

# Create mock mcp.shared module
mock_mcp_shared = create_mock_module("mcp.shared", {"__path__": []})
mock_mcp_shared_exceptions = create_mock_module("mcp.shared.exceptions", {})


# Create mock ErrorData for McpError
class MockErrorData:
    """Mock ErrorData object that McpError holds."""

    def __init__(self, message: str = None):
        self.message = message or "Unknown error"


# Create McpError exception class for mocking
class MockMcpError(Exception):
    """Mock McpError for testing.

    Mimics the real McpError from mcp.shared.exceptions which contains
    an error attribute with ErrorData.message for the actual error message.
    """

    def __init__(self, error=None):
        self.error = error
        if hasattr(error, 'message') and error.message:
            message = error.message
        elif hasattr(error, '__str__'):
            message = str(error)
        else:
            message = "Unknown MCP error"
        super().__init__(message)


mock_mcp_shared_exceptions.McpError = MockMcpError
mock_mcp.McpError = MockMcpError
mock_mcp_client = create_mock_module("mcp.client", {"__path__": []})
mock_mcp_client.sse = create_mock_module("mcp.client.sse", {
    "sse_client": MagicMock(),
})
mock_mcp_client.streamable_http = create_mock_module("mcp.client.streamable_http", {
    "streamablehttp_client": MagicMock(),
})
mock_mcp_client_stdio = create_mock_module("mcp.client.stdio", {})

# Create mock slack_bolt modules
mock_slack_bolt = create_mock_module("slack_bolt", {"__path__": ["slack_bolt"]})
mock_slack_bolt_adapter = create_mock_module("slack_bolt.adapter", {"__path__": []})
mock_slack_bolt_adapter_socket_mode = create_mock_module("slack_bolt.adapter.socket_mode", {"__path__": []})
mock_slack_bolt_adapter_socket_mode_aiohttp = create_mock_module("slack_bolt.adapter.socket_mode.aiohttp", {
    "AsyncSocketModeHandler": MagicMock()
})
mock_slack_bolt.App = MagicMock()
mock_slack_bolt.WorkflowApp = MagicMock()

# Mock slack_bolt.async_app (required by slack adapter)
mock_slack_bolt_async_app = create_mock_module("slack_bolt.async_app", {
    "AsyncApp": MagicMock()
})

# Create mock slack_sdk modules
mock_slack_sdk = create_mock_module("slack_sdk", {"__path__": ["slack_sdk"]})
mock_slack_sdk.WebClient = MagicMock()
mock_mcp_server = create_mock_module("mcp.server", {"__path__": []})
mock_mcp_server.stdio = create_mock_module("mcp.server.stdio", {})


class MockTool:
    """Mock tool that wraps a function and exposes fn attribute."""
    def __init__(self, fn, name, description="", parameters=None):
        self.fn = fn
        self.name = name
        self.description = description
        self.parameters = parameters or {}


class MockToolManager:
    """Mock tool manager that stores tools and returns them via get_tool."""
    def __init__(self):
        self._tools = {}

    def add_tool(self, fn, name, description="", parameters=None):
        self._tools[name] = MockTool(fn, name, description, parameters)

    def get_tool(self, name):
        return self._tools.get(name)

    def list_tools(self):
        return list(self._tools.values())


class MockFastMCP:
    """Mock FastMCP that properly handles tools for testing."""
    def __init__(self, name="", instructions="", stateless_http=False, json_response=False):
        self.name = name
        self.instructions = instructions
        self._tool_manager = MockToolManager()
        self._session_manager = MagicMock()

    def tool(self):
        """Decorator to register a tool - returns the decorator function."""
        def decorator(func):
            self._tool_manager.add_tool(func, func.__name__)
            return func
        return decorator

    @property
    def session_manager(self):
        return self._session_manager

    def streamable_http_app(self):
        """Return a mock HTTP app."""
        return MagicMock()

    def sse_app(self, mount_path="/sse"):
        """Return a mock SSE app."""
        return MagicMock()


mock_mcp_server_fastmcp = create_mock_module("mcp.server.fastmcp", {
    "FastMCP": MockFastMCP,
})
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


_mock_modules = {
    "langgraph": mock_langgraph,
    "langgraph.graph": mock_langgraph_graph,
    "langgraph.graph.state": mock_langgraph_graph_state,
    "langgraph.prebuilt": mock_langgraph_prebuilt,
    "langgraph.constants": mock_langgraph_constants,
    "langgraph.checkpoint": mock_langgraph_checkpoint,
    "langgraph.checkpoint.memory": mock_langgraph_checkpoint_memory,
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
    "mcp.server.fastmcp": mock_mcp_server_fastmcp,
    "mcp.types": mock_mcp_types,
    "mcp.client.stdio.context_manager": mock_mcp_stdio_client,
    "mcp.shared": mock_mcp_shared,
    "mcp.shared.exceptions": mock_mcp_shared_exceptions,
    # Mock langchain_mcp_adapters
    "langchain_mcp_adapters": mock_langchain_mcp,
    "langchain_mcp_adapters.tools": mock_langchain_mcp_tools,
    # Mock slack_bolt modules
    "slack_bolt": mock_slack_bolt,
    "slack_bolt.adapter": mock_slack_bolt_adapter,
    "slack_bolt.adapter.socket_mode": mock_slack_bolt_adapter_socket_mode,
    "slack_bolt.adapter.socket_mode.aiohttp": mock_slack_bolt_adapter_socket_mode_aiohttp,
    "slack_bolt.async_app": mock_slack_bolt_async_app,
    # Mock slack_sdk modules
    "slack_sdk": mock_slack_sdk,
}


# Inject mocks into sys.modules BEFORE any test imports happen.
# Always inject (no guard) so mocks replace any real modules that may have
# been imported during pytest collection or from prior test runs.
#
# IMPORTANT: Do NOT delete these mocks from sys.modules at collection time.
# pytest discovers and imports test files throughout the collection phase, and
# removing ``mcp`` (or any other mock) here would break subsequent test files
# that import ``daemon.manager`` -> ``daemon.mcp.warmup_pool`` -> ``mcp``.
# Tests that need the real ``mcp`` SDK (e.g. tests/e2e) are responsible for
# swapping the mock for the real module per-test (see tests/e2e/conftest.py).
for key, mock_mod in _mock_modules.items():
    sys.modules[key] = mock_mod


def pytest_pycollect_makemodule(module_path, parent):
    """Re-inject mocked modules before each test file is imported.

    Some test files (and the daemon modules they import) can indirectly cause
    ``sys.modules`` entries to be removed during collection. Re-installing the
    mocks right before each test file is loaded guarantees that every test
    file sees the mocks, regardless of what earlier collection steps did.
    """
    for key, mock_mod in _mock_modules.items():
        sys.modules[key] = mock_mod
    # Default behaviour: let pytest build the Module the usual way.
    return pytest.Module.from_parent(parent, path=module_path)


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
  max_children_per_instance: 50
  instance_timeout_minutes: 60
  message_rate_limit: 60

persistence:
  db_path: "./data/instances.db"
  checkpoint_interval: 1
  checkpoint_ttl_hours: 168
  checkpoint_cleanup_interval: 24
  max_instance_history: 300

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
  max_children_per_instance: 50
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


# ==================== Phase 3: app.state.manager safety net ====================


@pytest.fixture(autouse=True)
def _ensure_app_state_manager():
    """Provide a default ``app.state.manager`` for tests that don't set one.

    Phase 3 of the database migration added write-pause guards at the top
    of every router endpoint (``if manager.is_write_paused: raise 503``)
    and routes ``request.app.state.manager`` lookups through a helper.
    Test fixtures that build a bare ``FastAPI()`` and forget to set
    ``app.state.manager`` now raise ``AttributeError`` from Starlette's
    ``State.__getattr__``; fixtures that set it to ``None`` raise
    ``AttributeError: 'NoneType' object has no attribute 'is_write_paused'``
    when the router reads the property.

    This fixture patches ``starlette.datastructures.State.__getattr__`` so
    a missing-or-``None`` ``manager`` attribute is replaced with a
    ``MagicMock`` whose ``is_write_paused`` is ``False``. Tests that
    explicitly set ``app.state.manager`` to a real (or mock) object are
    unaffected because the patch only fires when the key is missing or
    set to ``None``.
    """
    from starlette.datastructures import State
    from unittest.mock import MagicMock

    original_getattr = State.__getattr__

    def patched_getattr(self, key):
        if key == "manager":
            existing = self._state.get("manager", "__missing__")
            if existing == "__missing__" or existing is None:
                default = MagicMock()
                default.is_write_paused = False
                self._state["manager"] = default
                return default
            return existing
        return original_getattr(self, key)

    State.__getattr__ = patched_getattr
    try:
        yield
    finally:
        State.__getattr__ = original_getattr


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


@pytest.fixture(autouse=True)
def reset_reasoning_echo_models():
    """Reset ThinkingChatOpenAI.reasoning_echo_models after each test.

    The daemon's __main__/lifespan sets this from config; tests that import
    ``daemon.graph`` shouldn't leak state between modules. We snapshot and
    restore around every test.
    """
    try:
        from daemon.graph import ThinkingChatOpenAI
    except Exception:
        yield
        return

    original = list(getattr(ThinkingChatOpenAI, "reasoning_echo_models", ["deepseek"]))
    yield
    ThinkingChatOpenAI.reasoning_echo_models = original


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
