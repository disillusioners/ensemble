"""Pytest configuration and common fixtures for daemon tests."""

import os
import sys
import pytest
from pathlib import Path
from datetime import datetime
from types import ModuleType
from unittest.mock import MagicMock


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
    "CompiledGraph": MagicMock(),  # Add CompiledGraph here since source code imports from here
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

# Pre-populate sys.modules
_mock_modules = {
    "langgraph": mock_langgraph,
    "langgraph.graph": mock_langgraph_graph,
    "langgraph.graph.state": mock_langgraph_graph_state,
    "langgraph.prebuilt": mock_langgraph_prebuilt,
    "langgraph.constants": mock_langgraph_constants,
    "langgraph.checkpoint": mock_langgraph_checkpoint,
    "langgraph.checkpoint.sqlite": mock_langgraph_checkpoint_sqlite,
    "langgraph.checkpoint.sqlite.aio": mock_langgraph_checkpoint_sqlite_aio,
}

# Save and replace modules
_original_modules = {}
for key in _mock_modules:
    if key in sys.modules:
        _original_modules[key] = sys.modules[key]
    sys.modules[key] = _mock_modules[key]


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
  port: 8080

limits:
  max_sessions: 100
  max_children_per_session: 10
  session_timeout_minutes: 60
  message_rate_limit: 60

persistence:
  db_path: "./data/sessions.db"
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
  port: ${DAEMON_PORT:-8080}

limits:
  max_sessions: ${MAX_SESSIONS:-100}
"""


@pytest.fixture
def sample_session_info_data():
    """Sample SessionInfo data for testing."""
    return {
        "session_id": "test-session-123",
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
def sample_session_create_data():
    """Sample SessionCreate data for testing."""
    return {
        "agent_dir": "/path/to/agent",
    }


@pytest.fixture
def sample_session_create_with_session_id():
    """Sample SessionCreate data with custom session_id."""
    return {
        "agent_dir": "/path/to/agent",
        "session_id": "custom-session-123",
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
