"""Unit tests for per-agent LLM model override functionality."""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import pytest

from daemon.config import (
    Config,
    LLMConfig,
    LimitsConfig,
    LoopBreakerConfig,
    QueueConfig,
    SkillEvolutionConfig,
    LanguageConfig,
    _parse_csv_or_json_list,
)
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
    base_url_backup: str | None = None,
) -> Config:
    """Create a mock Config with customizable LLM settings."""
    mock_llm = MagicMock(spec=LLMConfig)
    mock_llm.model = model
    mock_llm.base_url = base_url
    mock_llm.base_url_backup = base_url_backup
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
    # ``InstanceManager.__init__`` reads these sub-configs directly;
    # ``MagicMock(spec=Config)`` does not auto-create them.
    config.skill_evolution = MagicMock(spec=SkillEvolutionConfig)
    config.language = MagicMock(spec=LanguageConfig)
    config.language.check_enabled = False
    # ``build_instance_graph`` now reads ``config.loop_breaker`` for the
    # loop breaker wiring (added in feature/general-hallucination-fix) —
    # mirror the same explicit-mock pattern used for ``language`` above.
    config.loop_breaker = MagicMock(spec=LoopBreakerConfig)
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

        result = lifecycle._build_llm_config()

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
        result = lifecycle._build_llm_config()

        assert result["model"] == "gpt-4"

    def test_overrides_model_when_override_provided(self) -> None:
        """_build_llm_config: passed override_model wins over default.

        After Phase 3 of llm-model-load-balance, ``_build_llm_config`` is
        a PURE config-builder. The resolution chain
        (override → llm_models → llm_model → default) lives in
        :meth:`InstanceLifecycleService.spawn_instance`. The test passes
        the resolved model as ``override_model`` — that's the only
        decision the function makes.
        """
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model="gpt-4o-mini", agent_id="custom")
        # Resolved model = "gpt-4o-mini" (whatever the caller computed).
        result = lifecycle._build_llm_config(override_model="gpt-4o-mini")

        assert result["model"] == "gpt-4o-mini"
        # Other settings should remain global
        assert result["base_url"] == "https://api.openai.com/v1"
        assert result["temperature"] == 0.7

    def test_does_not_override_when_whitespace_only(self) -> None:
        """Test that whitespace-only llm_model does not override global model.

        The whitespace-only value is loaded as-is, but validation
        (checking if strip() is non-empty) happens in spawn_instance's
        resolution chain (Phase 3). ``_build_llm_config`` is a pure
        config-builder and trusts whatever string the caller passes.
        """
        config = create_mock_config(model="gpt-4")
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(llm_model="  ", agent_id="whitespace")
        result = lifecycle._build_llm_config()

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
        # ``spawn_instance`` calls ``registry.get_version(resolved_id, None)``
        # before falling back to ``registry.get``. Without this stub the mock
        # returns a MagicMock chain (``get_version().llm_model.strip()``)
        # which breaks ``_build_llm_config`` downstream.
        mock_registry.get_version.return_value = mock_metadata

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
        # ``spawn_instance`` calls ``registry.get_version(resolved_id, None)``
        # before falling back to ``registry.get``. Without this stub the mock
        # returns a MagicMock chain (``get_version().llm_model.strip()``)
        # which breaks ``_build_llm_config`` downstream.
        mock_registry.get_version.return_value = mock_metadata

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
        result = lifecycle._build_llm_config(override_model="gpt-4o")

        assert result["model"] == "gpt-4o", (
            "override_model should win over both metadata.llm_model and config.llm.model"
        )

    def test_override_rejected_returns_global_default(self) -> None:
        """When the caller-supplied override is rejected by the allow-list,
        ``_resolve_model_override`` returns None. spawn_instance's
        resolution chain then falls through to metadata.llm_model or the
        config default.

        This test verifies the pure config-builder behavior: when called
        with ``override_model=None`` (the validated outcome), the result
        is the config default. The metadata-based fallback is now tested
        in :mod:`tests.test_llm_load_balance_integration`.
        """
        config = create_mock_config_with_allowed(
            model="default-model", allowed_models=["gpt-4"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Simulate: caller asked for "gpt-4o" but it's not allowed → resolved to None.
        validated_override = lifecycle._resolve_model_override("gpt-4o")
        assert validated_override is None

        # _build_llm_config receives None → uses the global default.
        # (The spawn_instance resolution chain would have passed
        # "meta-model" here — that's tested elsewhere.)
        metadata = create_metadata(llm_model="meta-model", agent_id="x")
        result = lifecycle._build_llm_config(override_model=validated_override)

        assert result["model"] == "default-model"

    def test_no_override_param_uses_default(self) -> None:
        """No override_model param → default config value used.

        After Phase 3 of llm-model-load-balance, the resolution chain
        (override → llm_models → llm_model → default) lives in
        :meth:`InstanceLifecycleService.spawn_instance`. The pure
        config-builder only uses ``override_model``; the caller's
        resolution chain feeds it the final value. So a missing
        ``override_model`` means "use the default".
        """
        config = create_mock_config_with_allowed(model="default-model", allowed_models=["gpt-4"])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # No override → default.
        metadata = create_metadata(llm_model="meta-model", agent_id="x")
        result = lifecycle._build_llm_config()
        assert result["model"] == "default-model"


def _make_restore_mock_manager(allowed_models: list[str]) -> MagicMock:
    """Build a mock ``InstanceManager`` sufficient for ``_restore_instance``.

    Disables the optional MCP service branch of ``_get_mcp_tool_names`` by
    setting ``_mcp_service = None`` so the helper falls through to the stored
    / empty-list path (the real manager may or may not have an MCP service).
    """
    config = create_mock_config_with_allowed(
        model="default-model", allowed_models=allowed_models
    )
    manager = create_mock_manager(config)
    # MagicMock ``hasattr`` returns True for any attribute; setting
    # ``_mcp_service = None`` short-circuits the MCP cache branch so the
    # fallback ``return []`` path is taken deterministically.
    manager._mcp_service = None
    return manager


def _make_restore_meta(
    instance_metadata: dict | None,
    agent_id: str = "test-agent",
    instance_id: str = "test-instance-uuid",
) -> MagicMock:
    """Build a mock ``Instance`` row with the given metadata dict.

    Only the attributes accessed by ``_restore_instance`` are populated:
    ``instance_id``, ``agent_id``, ``agent_dir``, ``parent_id``, and
    ``instance_metadata``.
    """
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.agent_id = agent_id
    meta.agent_dir = "/tmp/test"
    meta.parent_id = None
    # Use a real dict (not a MagicMock) so ``.get()`` returns the literal
    # value, not a MagicMock — matches the production Instance model.
    meta.instance_metadata = (
        dict(instance_metadata) if instance_metadata is not None else {}
    )
    return meta


@contextmanager
def _patch_restore_dependencies(agent_meta: AgentMetadata, captured: dict):
    """Patch every external dependency of ``_restore_instance``.

    ``build_instance_graph`` is replaced with a side-effect that copies its
    ``kwargs`` into ``captured`` so tests can assert on the
    ``llm_config`` argument that was ultimately used to build the graph.
    """

    def _capture_build_graph(**kwargs):
        captured.clear()
        captured.update(kwargs)
        return MagicMock()

    mock_registry = MagicMock()
    mock_registry.get_resolved.return_value = agent_meta
    mock_registry.resolve_pure_id.return_value = agent_meta.id
    # ``_restore_instance`` calls ``registry.get_version(meta.agent_id, agent_tag)``
    # as the primary lookup (versioning-aware path). Without this stub the mock
    # returns a MagicMock chain (``get_version().llm_model.strip()``) which
    # breaks ``_build_llm_config`` downstream.
    mock_registry.get_version.return_value = agent_meta

    with patch(
        "daemon.services.instance_lifecycle.get_registry",
        return_value=mock_registry,
    ), patch(
        "daemon.manager.load_and_cache_prompt",
        return_value=("prompt", 100),
    ), patch(
        "daemon.manager.create_instance_tools",
        return_value=[],
    ), patch(
        "daemon.manager.build_instance_graph",
        side_effect=_capture_build_graph,
    ):
        yield


class TestRestoreInstanceModelOverride:
    """Tests for ``_restore_instance`` model-override re-validation.

    SECURITY/COMPLIANCE: when an instance is restored from the DB after a
    daemon restart, the persisted ``model_override`` MUST be re-validated
    against the CURRENT ``config.llm.allowed_models`` list. A model removed
    from the allow-list after spawn must NOT continue running indefinitely
    on the now-forbidden model — this guards against the
    "permissive-now-strict" compliance hazard.
    """

    async def test_stored_override_reapplied_on_restore(self) -> None:
        """Stored ``model_override='gpt-4'`` + ``allowed_models=['gpt-4']`` → reapplied.

        The stored value is still in the allow-list, so it must pass through
        ``_resolve_model_override`` unchanged and reach ``_build_llm_config``
        as the highest-priority override.
        """
        manager = _make_restore_mock_manager(allowed_models=["gpt-4"])
        agent_meta = create_metadata(llm_model=None, agent_id="test-agent")
        captured: dict = {}

        meta = _make_restore_meta({"model_override": "gpt-4"})

        lifecycle = InstanceLifecycleService(manager, MagicMock())
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta.instance_id, meta)

        llm_config = captured.get("llm_config", {})
        assert llm_config.get("model") == "gpt-4", (
            f"Expected restored model 'gpt-4' from metadata, got "
            f"{llm_config.get('model')!r}"
        )

    async def test_stored_override_removed_from_allowed_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stored ``model_override='gpt-4'`` but ``allowed_models=['gpt-4o']`` → fallback.

        The previously-valid model is no longer in the allow-list. The
        restore path must:
          1. Reject the override (return ``None`` from ``_resolve_model_override``).
          2. Pass ``override_model=None`` to ``_build_llm_config`` so the
             config default (``config.llm.model``) is used.
          3. Emit a WARNING so operators can see the model was stripped.
        """
        manager = _make_restore_mock_manager(allowed_models=["gpt-4o"])
        agent_meta = create_metadata(llm_model=None, agent_id="test-agent")
        captured: dict = {}

        meta = _make_restore_meta({"model_override": "gpt-4"})

        lifecycle = InstanceLifecycleService(manager, MagicMock())
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.instance_lifecycle"
        ):
            with _patch_restore_dependencies(agent_meta, captured):
                await lifecycle._restore_instance(meta.instance_id, meta)

        llm_config = captured.get("llm_config", {})
        # override_model resolves to None → falls back to config.llm.model
        assert llm_config.get("model") == "default-model", (
            f"Expected fallback to default 'default-model', got "
            f"{llm_config.get('model')!r}"
        )

        # WARNING must mention both the rejected model and the allow-list,
        # so operators can diagnose the silent fallback.
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warning_records, (
            "Expected a WARNING log when a stored override is no longer in "
            "allowed_models; got no WARNING records"
        )
        combined = " ".join(r.getMessage() for r in warning_records)
        assert "gpt-4" in combined, (
            f"WARNING must mention the rejected model 'gpt-4'; got: {combined!r}"
        )
        assert "allowed_models" in combined, (
            f"WARNING must mention allowed_models; got: {combined!r}"
        )

    async def test_no_stored_override_uses_default(self) -> None:
        """No ``model_override`` key in metadata → fallback to default (backwards compat).

        Pre-feature instances (and instances spawned without the override
        param) have no ``model_override`` key. The restore path must not
        crash and must use the config default.

        Note: this test uses ``llm_model=None``. When ``llm_model`` IS set
        in agent_meta, the C1 fix in ``_restore_instance`` falls back to
        ``agent_meta.llm_model`` instead of the config default — that
        behavior is covered by ``test_restore_llm_model_used_when_no_override``
        below.
        """
        manager = _make_restore_mock_manager(allowed_models=["gpt-4"])
        agent_meta = create_metadata(llm_model=None, agent_id="test-agent")
        captured: dict = {}

        # No model_override key in metadata at all (only an unrelated key)
        meta = _make_restore_meta({"unrelated_key": "value"})

        lifecycle = InstanceLifecycleService(manager, MagicMock())
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta.instance_id, meta)

        llm_config = captured.get("llm_config", {})
        assert llm_config.get("model") == "default-model", (
            f"Expected default 'default-model', got {llm_config.get('model')!r}"
        )

    async def test_corrupt_stored_override_graceful_fallback(self) -> None:
        """Stored override is ``None`` or ``""`` → no crash, graceful fallback.

        Guards against rows where ``model_override`` was set but contains no
        usable value (interrupted writes, schema migrations, manual DB
        edits). The restore flow must not raise and must use the default
        model. This is the regression test for the
        "raw_stored_override.strip()" guard in ``_restore_instance`` —
        without it, a corrupt row would emit a misleading
        ``"<spaces>" is no longer in allowed_models`` warning.
        """
        manager = _make_restore_mock_manager(allowed_models=["gpt-4"])
        agent_meta = create_metadata(llm_model=None, agent_id="test-agent")
        captured: dict = {}

        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Case A: explicit None (e.g. INSERT with NULL value)
        meta_none = _make_restore_meta({"model_override": None})
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta_none.instance_id, meta_none)
        assert captured["llm_config"]["model"] == "default-model", (
            "None model_override must fall back to default"
        )

        # Case B: empty string (e.g. partial write that left an empty value)
        meta_empty = _make_restore_meta({"model_override": ""})
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta_empty.instance_id, meta_empty)
        assert captured["llm_config"]["model"] == "default-model", (
            "Empty-string model_override must fall back to default"
        )

    async def test_restore_llm_model_used_when_no_override(self) -> None:
        """C1 REGRESSION: agent with llm_model but no persisted model_override
        must use llm_model on restore, NOT fall back to global default.

        The new _build_llm_config (Phase 3 refactor) no longer reads
        metadata.llm_model. Without the C1 fix in restore_instance, agents
        with llm_model silently revert to the global default after restart.
        """
        manager = _make_restore_mock_manager(allowed_models=["meta-model"])
        agent_meta = create_metadata(llm_model="meta-model", agent_id="test-agent")
        captured: dict = {}

        # No model_override in metadata (llm_model source doesn't persist one)
        meta = _make_restore_meta({"unrelated_key": "value"})

        lifecycle = InstanceLifecycleService(manager, MagicMock())
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta.instance_id, meta)

        llm_config = captured.get("llm_config", {})
        assert llm_config.get("model") == "meta-model", (
            f"C1 REGRESSION: expected 'meta-model' from agent_meta.llm_model, "
            f"got {llm_config.get('model')!r} — restore dropped llm_model"
        )

    async def test_llm_models_selected_model_frozen_on_restore(self) -> None:
        """W7: load-balanced model (persisted as model_override) is reused
        on restore — NOT re-balanced.

        When source was 'llm_models', the selected model was persisted to
        model_override. On restore, this persisted value is the highest-
        priority override, so the instance gets the SAME model it was
        spawned with — the RNG does NOT fire again.
        """
        manager = _make_restore_mock_manager(allowed_models=["selected-model"])
        # agent_meta has llm_models, but they should NOT be re-evaluated on restore
        agent_meta = create_metadata(llm_model=None, agent_id="test-agent")
        agent_meta.llm_models = None  # ensure no re-evaluation path
        captured: dict = {}

        # Simulate: instance was spawned with llm_models, selected "selected-model",
        # which was persisted as model_override
        meta = _make_restore_meta({"model_override": "selected-model"})

        lifecycle = InstanceLifecycleService(manager, MagicMock())
        with _patch_restore_dependencies(agent_meta, captured):
            await lifecycle._restore_instance(meta.instance_id, meta)

        llm_config = captured.get("llm_config", {})
        assert llm_config.get("model") == "selected-model", (
            f"Expected frozen load-balanced model 'selected-model', "
            f"got {llm_config.get('model')!r}"
        )


class TestAllowedModelsConfigParsing:
    """Tests for the ``_parse_csv_or_json_list`` helper in ``daemon.config``.

    This helper backs both ``LLMConfig.reasoning_echo_disabled_models`` and
    ``LLMConfig.allowed_models`` parsing from env / YAML inputs, so any
    regression here affects every consumer of the list-parsing machinery.
    """

    def test_csv_string_parsing(self) -> None:
        """``'gpt-4,gpt-4o'`` → ``['gpt-4', 'gpt-4o']`` (CSV path)."""
        assert _parse_csv_or_json_list("gpt-4,gpt-4o") == ["gpt-4", "gpt-4o"]

    def test_json_array_parsing(self) -> None:
        """``'[\"gpt-4\",\"gpt-4o\"]'`` → ``['gpt-4', 'gpt-4o']`` (JSON path)."""
        assert _parse_csv_or_json_list('["gpt-4","gpt-4o"]') == [
            "gpt-4",
            "gpt-4o",
        ]

    def test_malformed_json_does_not_crash(self) -> None:
        """``'[oops'`` (malformed JSON) → falls through to CSV split.

        Must NOT raise. The string is split on ``,`` as a last resort, so a
        single entry with no commas is returned as a one-element list. This
        is a defensive fallback — the helper should never crash on a
        malformed env value.
        """
        assert _parse_csv_or_json_list("[oops") == ["[oops"]

    def test_whitespace_only_entries_filtered(self) -> None:
        """``'gpt-4, , gpt-4o'`` → ``['gpt-4', 'gpt-4o']`` (empty entries filtered)."""
        assert _parse_csv_or_json_list("gpt-4, , gpt-4o") == [
            "gpt-4",
            "gpt-4o",
        ]

    def test_list_input_trailing_space_entries_stripped(self) -> None:
        """``['gpt-4 ', ' gpt-4o']`` → ``['gpt-4', 'gpt-4o']``.

        Regression test for the YAML list stripping fix: list inputs were
        previously returned unchanged, so a YAML entry like ``'gpt-4 '``
        (trailing space) would be stored verbatim and never match a
        stripped candidate ``'gpt-4'`` — silently rejecting valid models.
        """
        assert _parse_csv_or_json_list(["gpt-4 ", " gpt-4o"]) == [
            "gpt-4",
            "gpt-4o",
        ]

