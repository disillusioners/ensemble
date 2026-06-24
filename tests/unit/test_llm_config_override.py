"""Unit tests for per-agent LLM model override functionality."""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from daemon.config import Config, LLMConfig, LimitsConfig, QueueConfig
from daemon.registry import AgentMetadata
from daemon.services.instance_lifecycle import InstanceLifecycleService


def create_mock_config(
    model: str = "gpt-4",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "test-key",
    temperature: float = 0.7,
    request_timeout: int = 610,
    model_vision: str | None = None,
) -> Config:
    """Create a mock Config with customizable LLM settings."""
    mock_llm = MagicMock(spec=LLMConfig)
    mock_llm.model = model
    mock_llm.base_url = base_url
    mock_llm.api_key = api_key
    mock_llm.temperature = temperature
    mock_llm.request_timeout = request_timeout
    mock_llm.model_vision = model_vision

    mock_limits = MagicMock(spec=LimitsConfig)
    mock_limits.max_instances = 100
    mock_limits.max_children_per_instance = 10
    mock_limits.graph_recursion_limit = 100

    mock_queue = MagicMock(spec=QueueConfig)
    mock_queue.llm_retry_transient_attempts = 10
    mock_queue.llm_retry_timeout_attempts = 3

    config = MagicMock(spec=Config)
    config.llm = mock_llm
    config.limits = mock_limits
    config.queue = mock_queue
    return config


def create_mock_manager(config: Config | None = None) -> MagicMock:
    """Create a mock InstanceManager with the given config."""
    manager = MagicMock()
    manager.config = config or create_mock_config()
    manager._checkpointer = MagicMock()
    manager._compactor = MagicMock()
    manager._instance_repository = MagicMock()
    manager._project_repository = MagicMock()
    manager.prompt_cache = {}
    manager.instances = {}
    manager._engine = MagicMock()
    manager._request_registry = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    return manager


def create_metadata(llm_model: str | None = None, agent_id: str = "test") -> AgentMetadata:
    """Create an AgentMetadata with optional llm_model."""
    return AgentMetadata(
        id=agent_id,
        name=agent_id.title(),
        description="Test agent",
        icon="🤖",
        color="blue",
        path=Path(f"/test/agents/{agent_id}"),
        llm_model=llm_model,
    )


class TestBuildLLMConfig:
    """Tests for the _build_llm_config method."""

    def test_returns_global_config_when_metadata_none(self) -> None:
        """Test that global config is returned when metadata is None."""
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        result = lifecycle._build_llm_config(None)

        assert result["model"] == "gpt-4"
        assert result["base_url"] == "https://api.openai.com/v1"
        assert result["api_key"] == "test-key"
        assert result["temperature"] == 0.7
        assert result["request_timeout"] == 610
        assert result["model_vision"] is None

    def test_returns_global_config_when_llm_model_is_none(self) -> None:
        """Test that global config is returned when metadata.llm_model is None."""
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model=None)
        result = lifecycle._build_llm_config(metadata)

        assert result["model"] == "gpt-4"

    def test_overrides_model_when_llm_model_set(self) -> None:
        """Test that model is overridden when metadata.llm_model is set."""
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model="gpt-4o-mini", agent_id="custom")
        result = lifecycle._build_llm_config(metadata)

        assert result["model"] == "gpt-4o-mini"
        # Other settings should remain global
        assert result["base_url"] == "https://api.openai.com/v1"
        assert result["temperature"] == 0.7

    def test_does_not_override_when_whitespace_only(self) -> None:
        """Test that whitespace-only llm_model does not override global model.

        The whitespace-only value is loaded as-is, but validation
        (checking if strip() is non-empty) happens in _build_llm_config.
        """
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model="  ", agent_id="whitespace")
        result = lifecycle._build_llm_config(metadata)

        # whitespace.strip() is empty, so no override should happen
        assert result["model"] == "gpt-4"


class TestSpawnInstanceLLMOverride:
    """Tests for spawn_instance LLM override integration."""

    def test_spawn_instance_passes_overridden_model_to_build_graph(self) -> None:
        """Test that spawn_instance uses the overridden model from metadata."""
        global_config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(global_config)

        # Create mock cancellation service
        cancellation_service = MagicMock()

        # Create lifecycle service
        lifecycle = InstanceLifecycleService(manager, cancellation_service)

        # Create mock registry that returns metadata with custom llm_model
        mock_metadata = create_metadata(llm_model="gpt-4o-mini", agent_id="custom_agent")

        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = "custom_agent"
        mock_registry.get.return_value = mock_metadata

        # Track what gets passed to build_instance_graph
        captured_llm_config = {}

        def capture_build_graph(**kwargs):
            captured_llm_config.update(kwargs.get("llm_config", {}))
            return MagicMock()

        # Mock sqlmodel.Session — spawn_instance creates a real Session
        # inside _spawn_instance_db_sync and calls session.refresh() to
        # load the deferred created_at column. The MagicMock Session is a
        # no-op by default, so we configure refresh() to satisfy the
        # loader itself.
        # Stale test: mock session needs to satisfy deferred loader on refresh()
        def fake_refresh(obj):
            obj.created_at = datetime.now(timezone.utc).isoformat()
            obj.updated_at = obj.created_at

        mock_session = MagicMock()
        mock_session.refresh.side_effect = fake_refresh

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        # Patch get_registry in instance_lifecycle (imported at top of file)
        # Patch other imports in daemon.manager (imported inside spawn_instance method)
        with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
             patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
             patch("daemon.manager.create_instance_tools", return_value=[]), \
             patch("daemon.manager.build_instance_graph", side_effect=capture_build_graph), \
             patch("sqlmodel.Session", return_value=mock_session_ctx()):

            instance_id = lifecycle.spawn_instance(agent_id="custom_agent")

            # Verify the instance was created
            assert instance_id is not None

            # Verify build_instance_graph received the overridden model
            assert captured_llm_config.get("model") == "gpt-4o-mini", \
                f"Expected model 'gpt-4o-mini', got '{captured_llm_config.get('model')}'"

    def test_spawn_instance_uses_global_model_when_no_override(self) -> None:
        """Test that spawn_instance uses global model when no llm_model in metadata."""
        global_config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(global_config)

        cancellation_service = MagicMock()
        lifecycle = InstanceLifecycleService(manager, cancellation_service)

        # Create metadata without llm_model (defaults to None)
        mock_metadata = create_metadata(llm_model=None, agent_id="standard_agent")

        mock_registry = MagicMock()
        mock_registry.resolve_to_id.return_value = "standard_agent"
        mock_registry.get.return_value = mock_metadata

        captured_llm_config = {}

        def capture_build_graph(**kwargs):
            captured_llm_config.update(kwargs.get("llm_config", {}))
            return MagicMock()

        # Stale test: mock session needs to satisfy deferred loader on refresh()
        def fake_refresh(obj):
            obj.created_at = datetime.now(timezone.utc).isoformat()
            obj.updated_at = obj.created_at

        mock_session = MagicMock()
        mock_session.refresh.side_effect = fake_refresh

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
             patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
             patch("daemon.manager.create_instance_tools", return_value=[]), \
             patch("daemon.manager.build_instance_graph", side_effect=capture_build_graph), \
             patch("sqlmodel.Session", return_value=mock_session_ctx()):

            instance_id = lifecycle.spawn_instance(agent_id="standard_agent")

            assert instance_id is not None
            assert captured_llm_config.get("model") == "gpt-4"
