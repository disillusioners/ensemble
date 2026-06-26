"""Unit tests for per-agent LLM model override functionality."""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from daemon.config import Config, LLMConfig, LimitsConfig, QueueConfig
from daemon.registry import AgentMetadata
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.instance_lifecycle import _SpawnResult


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

        # Stale test: _spawn_instance_db_sync does a real DB roundtrip
        # (commit + refresh) that the MagicMock manager cannot satisfy
        # via a fake ``session.refresh`` side-effect. The downstream
        # test only needs spawn_instance to call build_instance_graph
        # with the right llm_config, so patch the DB-sync method
        # directly to return a synthetic _SpawnResult.
        fake_spawn_result = _SpawnResult(
            created=True,
            parent_id=None,
            agent_id="custom_agent",
            project_id="test-project",
            created_at=datetime.now(timezone.utc).isoformat(),
            inherited_source=False,
        )

        # Patch get_registry in instance_lifecycle (imported at top of file)
        # Patch other imports in daemon.manager (imported inside spawn_instance method)
        with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
             patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
             patch("daemon.manager.create_instance_tools", return_value=[]), \
             patch("daemon.manager.build_instance_graph", side_effect=capture_build_graph), \
             patch(
                 "daemon.services.instance_lifecycle.InstanceLifecycleService._spawn_instance_db_sync",
                 return_value=fake_spawn_result,
             ):

            instance_id, _validated_override = lifecycle.spawn_instance(agent_id="custom_agent")

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

        # Stale test: _spawn_instance_db_sync does a real DB roundtrip
        # (commit + refresh) that the MagicMock manager cannot satisfy
        # via a fake ``session.refresh`` side-effect. The downstream
        # test only needs spawn_instance to call build_instance_graph
        # with the right llm_config, so patch the DB-sync method
        # directly to return a synthetic _SpawnResult.
        fake_spawn_result = _SpawnResult(
            created=True,
            parent_id=None,
            agent_id="standard_agent",
            project_id="test-project",
            created_at=datetime.now(timezone.utc).isoformat(),
            inherited_source=False,
        )

        with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
             patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
             patch("daemon.manager.create_instance_tools", return_value=[]), \
             patch("daemon.manager.build_instance_graph", side_effect=capture_build_graph), \
             patch(
                 "daemon.services.instance_lifecycle.InstanceLifecycleService._spawn_instance_db_sync",
                 return_value=fake_spawn_result,
             ):

            instance_id, _validated_override = lifecycle.spawn_instance(agent_id="standard_agent")

            assert instance_id is not None
            assert captured_llm_config.get("model") == "gpt-4"


def create_mock_config_with_allowed(
    model: str = "gpt-4",
    allowed_models: list[str] | None = None,
) -> Config:
    """Create a mock Config with an explicit allowed_models list.

    Unlike ``create_mock_config``, this helper sets ``allowed_models`` to a
    real list value (not a MagicMock), which is required for testing the
    ``_resolve_model_override`` validation logic.
    """
    config = create_mock_config(model=model)
    # Replace the auto-mocked list with a real list (or [] default).
    config.llm.allowed_models = list(allowed_models) if allowed_models else []
    return config


class TestResolveModelOverride:
    """Tests for ``_resolve_model_override`` (allowed_models validation).

    These tests guard against the substring-match bug: a model like ``"gpt-4o"``
    MUST NOT be accepted when the allow-list contains only ``"gpt-4"``. Matching
    must be exact (case-insensitive).
    """

    # --- exact-match acceptance ---

    def test_allowed_exact_match_accepted(self) -> None:
        """allowed_models=['gpt-4'], model='gpt-4' → accepted."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("gpt-4") == "gpt-4"

    def test_gpt4o_rejected_when_only_gpt4_allowed_regression(self) -> None:
        """Regression test for the substring-match bug.

        With substring matching, ``"gpt-4" in "gpt-4o"`` is True and the
        forbidden model would leak through. Exact match must reject it.
        """
        from daemon.services import instance_lifecycle as lifecycle_module

        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Capture the log so we can assert it was emitted at debug level
        # (not info/warn) per Fix 3 in this changeset.
        with patch.object(lifecycle_module.logger, "debug") as mock_debug, \
             patch.object(lifecycle_module.logger, "info") as mock_info, \
             patch.object(lifecycle_module.logger, "warning") as mock_warn:
            result = lifecycle._resolve_model_override("gpt-4o")

        assert result is None, (
            f"BUG REGRESSION: 'gpt-4o' must NOT be allowed when allowed_models=['gpt-4']; "
            f"got {result!r}"
        )
        # Silent-fallback path must emit a debug-level log, NOT info/warn
        # (Fix 3: this is a non-actionable "we silently ignored something" event).
        assert mock_debug.called, "Silent fallback must emit a debug log"
        assert not mock_info.called, "Silent fallback must NOT emit at info level"
        assert not mock_warn.called, "Silent fallback must NOT emit at warning level"

    def test_allowed_case_insensitive_match_accepted(self) -> None:
        """allowed_models=['GPT-4'], model='gpt-4' → accepted (case-insensitive)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["GPT-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("gpt-4") == "gpt-4"

    def test_whitespace_stripped_before_match(self) -> None:
        """model='  gpt-4  ' is stripped and matched against allowed_models=['gpt-4']."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("  gpt-4  ") == "gpt-4"

    def test_prefix_attack_rejected(self) -> None:
        """model='gpt-4-attacker' must NOT be accepted when allowed=['gpt-4'].

        Guards against prefix-style injection: an attacker cannot bypass the
        filter by appending suffixes like '-attacker', ':harmful', etc.
        """
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("gpt-4-attacker") is None

    def test_gpt4o_mini_rejected_when_only_gpt4_allowed(self) -> None:
        """model='gpt-4o-mini' must NOT match allowed=['gpt-4'] (no substring)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("gpt-4o-mini") is None

    def test_empty_allowed_means_all_models_allowed(self) -> None:
        """allowed_models=[] (empty) → any model accepted (no restriction)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("any-model-at-all") == "any-model-at-all"
        assert lifecycle._resolve_model_override("gpt-4o") == "gpt-4o"
        assert lifecycle._resolve_model_override("custom-fine-tuned-model") == "custom-fine-tuned-model"

    def test_model_in_multi_entry_list_accepted(self) -> None:
        """allowed_models=['gpt-4', 'gpt-4o'], model='gpt-4o' → accepted."""
        config = create_mock_config_with_allowed(
            model="gpt-4", allowed_models=["gpt-4", "gpt-4o"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("gpt-4o") == "gpt-4o"

    # --- no-override cases ---

    def test_none_model_returns_none(self) -> None:
        """model=None → no override (returns None)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override(None) is None

    def test_empty_string_model_returns_none(self) -> None:
        """model='' (empty string) → no override (returns None)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("") is None

    def test_whitespace_only_model_returns_none(self) -> None:
        """model='   ' (whitespace only) → no override (returns None)."""
        config = create_mock_config_with_allowed(model="gpt-4", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        assert lifecycle._resolve_model_override("   ") is None


class TestBuildLLMConfigPriority:
    """Tests for model-override priority in ``_build_llm_config``.

    Priority (highest wins):
        1. ``override_model`` (spawn_instance tool param, pre-validated)
        2. ``metadata.llm_model`` (meta.json field)
        3. ``config.llm.model`` (env / config.yaml default)
    """

    def test_override_applied_when_in_allowed_list(self) -> None:
        """model param provided AND in allowed list → override applied (highest priority)."""
        config = create_mock_config_with_allowed(
            model="default-model", allowed_models=["gpt-4o", "gpt-4-turbo"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model="meta-model", agent_id="x")
        result = lifecycle._build_llm_config(metadata, override_model="gpt-4o")

        assert result["model"] == "gpt-4o", (
            "override_model should win over both metadata.llm_model and config.llm.model"
        )

    def test_override_rejected_when_not_in_allowed_list_falls_back(self) -> None:
        """model param provided but NOT in allowed list (non-empty) → falls back.

        When the caller-supplied override is not in the allow-list,
        ``_resolve_model_override`` returns None. ``_build_llm_config`` then
        should NOT receive that value, so we test the contract: passing
        ``override_model=None`` (the validated outcome) yields the metadata
        model (next-priority).
        """
        config = create_mock_config_with_allowed(
            model="default-model", allowed_models=["gpt-4"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Simulate: caller asked for "gpt-4o" but it's not allowed → resolved to None.
        validated_override = lifecycle._resolve_model_override("gpt-4o")
        assert validated_override is None

        # _build_llm_config receives None → falls back to metadata.
        metadata = create_metadata(llm_model="meta-model", agent_id="x")
        result = lifecycle._build_llm_config(metadata, override_model=validated_override)

        assert result["model"] == "meta-model", (
            "When override is rejected, metadata.llm_model should take priority"
        )

    def test_no_override_param_keeps_existing_behavior(self) -> None:
        """No override_model param → existing behavior unchanged (backwards compat).

        Priority falls through to metadata.llm_model, then config.llm.model.
        """
        config = create_mock_config_with_allowed(model="default-model", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Case A: metadata has a model → use it.
        metadata_with = create_metadata(llm_model="meta-model", agent_id="x")
        result_with = lifecycle._build_llm_config(metadata_with)
        assert result_with["model"] == "meta-model"

        # Case B: metadata has no model → use config default.
        metadata_without = create_metadata(llm_model=None, agent_id="x")
        result_without = lifecycle._build_llm_config(metadata_without)
        assert result_without["model"] == "default-model"
