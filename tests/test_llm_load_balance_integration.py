"""Integration tests for the spawn_instance resolution chain.

Phase 3 of llm-model-load-balance moved model resolution from
``_build_llm_config`` into :meth:`InstanceLifecycleService.spawn_instance`.
The resolution priority (highest → lowest) is:

    1. ``validated_model_override`` (caller-supplied ``model`` arg,
       pre-validated against ``allowed_models``). When set, load balancing
       is SKIPPED — council/governor/explicit-spawn paths.
    2. ``metadata.llm_models`` (weighted random). RNG fires once here.
       ``None`` return from ``_select_weighted_model`` means all candidates
       were filtered or invalid; falls through to ``llm_model``.
    3. ``metadata.llm_model`` (single-model field in meta.json).
    4. ``self._config.llm.model`` (env ``OPENAI_MODEL`` / config default).

These tests verify the priority ordering end-to-end via the public
``spawn_instance`` API. The follow-on Phase 4 persistence behavior
(``model_override`` written to ``instance_metadata`` only for
``resolved_source in {"override", "llm_models"}``) is verified by the
``TestPersistenceGating`` class below.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config import (
    Config,
    LLMConfig,
    LimitsConfig,
    LoopBreakerConfig,
    QueueConfig,
    SkillEvolutionConfig,
    LanguageConfig,
)
from daemon.registry import AgentMetadata, LLMModelWeight
from daemon.services.instance_lifecycle import InstanceLifecycleService, _SpawnResult
from daemon.services.llm_load_balancer import _select_weighted_model


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def create_mock_config(
    model: str = "gpt-4",
    allowed_models: list[str] | None = None,
) -> Config:
    """Create a mock Config with the LLM fields the resolution chain reads."""
    mock_llm = MagicMock(spec=LLMConfig)
    mock_llm.model = model
    mock_llm.base_url = "https://api.openai.com/v1"
    mock_llm.base_url_backup = None
    mock_llm.api_key = "test-key"
    mock_llm.temperature = 0.7
    mock_llm.request_timeout = 610
    mock_llm.model_vision = None
    mock_llm.allowed_models = allowed_models or []

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
    config.skill_evolution = MagicMock(spec=SkillEvolutionConfig)
    config.language = MagicMock(spec=LanguageConfig)
    config.language.check_enabled = False
    config.loop_breaker = MagicMock(spec=LoopBreakerConfig)
    return config


def create_mock_manager(config: Config) -> MagicMock:
    """Create a mock InstanceManager suitable for spawn_instance tests."""
    manager = MagicMock()
    manager.config = config
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


def create_metadata(
    agent_id: str = "test-agent",
    llm_model: str | None = None,
    llm_models: list[LLMModelWeight] | None = None,
) -> AgentMetadata:
    """Create an AgentMetadata with optional ``llm_model`` and ``llm_models``."""
    return AgentMetadata(
        id=agent_id,
        name=agent_id.title(),
        description="Test agent",
        icon="🤖",
        color="blue",
        path=Path(f"/test/agents/{agent_id}"),
        llm_model=llm_model,
        llm_models=llm_models,
    )


def _make_spawn_result(agent_id: str = "test-agent") -> _SpawnResult:
    """Build a synthetic _SpawnResult for _spawn_instance_db_sync patch."""
    return _SpawnResult(
        created=True,
        parent_id=None,
        agent_id=agent_id,
        project_id="test-project",
        created_at=datetime.now(timezone.utc).isoformat(),
        inherited_source=False,
    )


def _spawn_and_capture_llm_config(
    lifecycle: InstanceLifecycleService,
    agent_id: str,
    metadata: AgentMetadata,
    model_arg: str | None = None,
) -> dict:
    """Call ``spawn_instance`` with all heavy helpers patched, return the
    ``llm_config`` dict that was passed to ``build_instance_graph``.

    Mirrors the established test pattern in
    ``tests/unit/test_llm_config_override.py::TestSpawnInstanceLLMOverride``.
    """
    mock_registry = MagicMock()
    mock_registry.resolve_to_id.return_value = agent_id
    mock_registry.get.return_value = metadata
    mock_registry.get_version.return_value = metadata

    captured: dict = {}

    def capture_build_graph(**kwargs):
        captured.update(kwargs.get("llm_config", {}))
        return MagicMock()

    spawn_result = _make_spawn_result(agent_id)

    with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
         patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
         patch("daemon.manager.create_instance_tools", return_value=[]), \
         patch("daemon.manager.build_instance_graph", side_effect=capture_build_graph), \
         patch(
             "daemon.services.instance_lifecycle.InstanceLifecycleService._spawn_instance_db_sync",
             return_value=spawn_result,
         ):
        lifecycle.spawn_instance(agent_id=agent_id, model=model_arg)
    return captured


def _spawn_and_capture_instance_metadata(
    lifecycle: InstanceLifecycleService,
    agent_id: str,
    metadata: AgentMetadata,
    model_arg: str | None = None,
    allowed_models: list[str] | None = None,
) -> dict:
    """Call ``spawn_instance`` with all heavy helpers patched, return the
    ``instance_metadata`` dict that was passed to ``_spawn_instance_db_sync``.

    Mirrors the ``_spawn_and_capture_llm_config`` pattern but captures the
    persistence-layer dict instead of the graph-build kwargs. The persistence
    block in :meth:`InstanceLifecycleService.spawn_instance` writes
    ``model_override`` to this dict only for ``resolved_source`` in
    ``{"override", "llm_models"}`` (Phase 4 of llm-model-load-balance).
    """
    mock_registry = MagicMock()
    mock_registry.resolve_to_id.return_value = agent_id
    mock_registry.get.return_value = metadata
    mock_registry.get_version.return_value = metadata

    captured: dict = {}

    def capture_db_sync(*args, **kwargs):
        captured.update(kwargs.get("instance_metadata", {}))
        return _make_spawn_result(agent_id)

    # Wire allowed_models into the lifecycle's config so the override path
    # validates against it (mirrors how the real production code reads
    # ``self._config.llm.allowed_models``).
    if allowed_models is not None:
        lifecycle._manager.config.llm.allowed_models = allowed_models

    with patch("daemon.services.instance_lifecycle.get_registry", return_value=mock_registry), \
         patch("daemon.manager.load_and_cache_prompt", return_value=("prompt", 100)), \
         patch("daemon.manager.create_instance_tools", return_value=[]), \
         patch("daemon.manager.build_instance_graph", return_value=MagicMock()), \
         patch(
             "daemon.services.instance_lifecycle.InstanceLifecycleService._spawn_instance_db_sync",
             side_effect=capture_db_sync,
         ):
        lifecycle.spawn_instance(agent_id=agent_id, model=model_arg)
    return captured


# ---------------------------------------------------------------------------
# Priority order tests
# ---------------------------------------------------------------------------


class TestResolutionPriority:
    """Verify the full resolution chain (highest → lowest)."""

    def test_priority_4_default_when_no_metadata_no_override(self) -> None:
        """No override, no llm_models, no llm_model → config default."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # No metadata at all — registry.get returns None, spawn raises.
        # So we test with a metadata that has nothing set.
        metadata = create_metadata(agent_id="plain")
        captured = _spawn_and_capture_llm_config(lifecycle, "plain", metadata)
        assert captured["model"] == "default-model"

    def test_priority_3_llm_model_overrides_default(self) -> None:
        """No override, no llm_models, llm_model set → llm_model wins."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(agent_id="x", llm_model="agent-model")
        captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert captured["model"] == "agent-model"

    def test_priority_2_llm_models_overrides_llm_model(self) -> None:
        """llm_models wins over llm_model when set (single entry → deterministic)."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Single entry → algorithm always returns it. llm_model is set but
        # llm_models takes priority.
        metadata = create_metadata(
            agent_id="x",
            llm_model="agent-model",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert captured["model"] == "pool-model"

    def test_priority_1_override_wins_over_everything(self) -> None:
        """Spawn-time model= override wins over llm_models and llm_model."""
        config = create_mock_config(
            model="default-model", allowed_models=["forced-model"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_model="agent-model",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        captured = _spawn_and_capture_llm_config(
            lifecycle, "x", metadata, model_arg="forced-model"
        )
        assert captured["model"] == "forced-model"

    def test_override_skipped_when_not_in_allowed_list(self) -> None:
        """Caller-supplied override not in allowed list → falls through."""
        config = create_mock_config(
            model="default-model", allowed_models=["gpt-4"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # Override is "blocked" which is not in allowed list → silent None.
        # With llm_model set, that wins.
        metadata = create_metadata(agent_id="x", llm_model="agent-model")
        captured = _spawn_and_capture_llm_config(
            lifecycle, "x", metadata, model_arg="blocked"
        )
        assert captured["model"] == "agent-model"


class TestLlmModelsFiltering:
    """allowed_models filtering at the spawn level."""

    def test_all_llm_models_filtered_falls_back_to_llm_model(self) -> None:
        """When all llm_models entries are filtered → falls through to llm_model."""
        config = create_mock_config(
            model="default-model", allowed_models=["fallback"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        # All pool models are NOT in allowed list → None from algorithm.
        # Falls through to llm_model "fallback-llm-model".
        metadata = create_metadata(
            agent_id="x",
            llm_model="fallback-llm-model",
            llm_models=[
                LLMModelWeight(model="blocked-1", weight=50),
                LLMModelWeight(model="blocked-2", weight=50),
            ],
        )
        captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert captured["model"] == "fallback-llm-model"

    def test_all_llm_models_filtered_no_llm_model_uses_default(self) -> None:
        """All filtered AND no llm_model → config default."""
        config = create_mock_config(
            model="default-model", allowed_models=["some-other"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_model=None,
            llm_models=[LLMModelWeight(model="blocked", weight=1)],
        )
        captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert captured["model"] == "default-model"

    def test_llm_models_with_empty_allowed_uses_pool(self) -> None:
        """Empty allowed list = no restriction; pool model wins."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_model="agent-model",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert captured["model"] == "pool-model"


class TestLlmModelsRandomness:
    """Verify the RNG fires once (single entry → deterministic; multiple → random)."""

    def test_single_entry_deterministic(self) -> None:
        """Single-entry llm_models always picks that entry."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        for _ in range(10):
            metadata = create_metadata(
                agent_id="x",
                llm_models=[LLMModelWeight(model="only-model", weight=1)],
            )
            captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
            assert captured["model"] == "only-model"

    def test_rng_fires_once_per_instance(self) -> None:
        """S11: _select_weighted_model is called exactly ONCE per spawn_instance call.

        Uses wraps= to call the real function (preserving behavior) while
        counting invocations. This is a stronger guarantee than testing
        diversity — it directly verifies the 'single resolution' invariant.
        """
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_models=[
                LLMModelWeight(model="m1", weight=50),
                LLMModelWeight(model="m2", weight=50),
            ],
        )

        with patch(
            "daemon.services.instance_lifecycle._select_weighted_model",
            wraps=_select_weighted_model,
        ) as mock_select:
            captured = _spawn_and_capture_llm_config(lifecycle, "x", metadata)
        assert mock_select.call_count == 1, (
            f"_select_weighted_model called {mock_select.call_count} times, "
            f"expected exactly 1 (single resolution invariant)"
        )
        assert captured["model"] in ("m1", "m2")


class TestSourceAttribution:
    """The resolved_source variable in spawn_instance is local-scope; the
    persistence block reads it. These tests verify the resolution chain
    produces the correct final model (the source itself is an internal
    variable — tested indirectly via the model returned).
    """

    def test_source_override_logged(self, caplog) -> None:
        """When override is set, the resolution log indicates it."""
        config = create_mock_config(
            model="default-model", allowed_models=["forced"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(agent_id="x")
        with caplog.at_level(logging.INFO, logger="daemon.services.instance_lifecycle"):
            captured = _spawn_and_capture_llm_config(
                lifecycle, "x", metadata, model_arg="forced"
            )
        assert captured["model"] == "forced"
        # No load-balance log expected for override path.
        assert "llm_load_balance_selected" not in caplog.text

    def test_source_llm_models_logged(self, caplog) -> None:
        """When llm_models fires, an INFO log is emitted with agent + model."""
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="load_balance_test",
            llm_models=[LLMModelWeight(model="only-model", weight=1)],
        )
        with caplog.at_level(logging.INFO, logger="daemon.services.instance_lifecycle"):
            captured = _spawn_and_capture_llm_config(
                lifecycle, "load_balance_test", metadata
            )
        assert captured["model"] == "only-model"
        assert "llm_load_balance_selected" in caplog.text
        assert "load_balance_test" in caplog.text
        assert "only-model" in caplog.text


# ---------------------------------------------------------------------------
# Phase 4 persistence gating tests
# ---------------------------------------------------------------------------


class TestPersistenceGating:
    """Phase 4 of llm-model-load-balance persists ``model_override`` to
    ``instance_metadata`` ONLY when ``resolved_source`` is ``"override"`` or
    ``"llm_models"``. The other sources (``"llm_model"``, ``"default"``)
    leave the field untouched for backward compatibility.

    These tests cover the persistence gate directly by capturing the
    ``instance_metadata`` dict passed to ``_spawn_instance_db_sync``.
    """

    def test_llm_models_source_persists_model_override(self) -> None:
        """Single-entry ``llm_models`` → ``resolved_source == "llm_models"`` →
        captured ``instance_metadata`` contains ``model_override``.
        """
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_models=[LLMModelWeight(model="test-model-a", weight=1)],
        )
        captured = _spawn_and_capture_instance_metadata(lifecycle, "x", metadata)
        assert captured["model_override"] == "test-model-a"

    def test_llm_model_source_does_not_persist_model_override(self) -> None:
        """``llm_model`` single-model field → ``resolved_source == "llm_model"`` →
        captured ``instance_metadata`` does NOT contain ``model_override``.
        """
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(agent_id="x", llm_model="single-model")
        captured = _spawn_and_capture_instance_metadata(lifecycle, "x", metadata)
        assert "model_override" not in captured

    def test_default_source_does_not_persist_model_override(self) -> None:
        """No ``llm_models``, no ``llm_model`` → ``resolved_source == "default"`` →
        captured ``instance_metadata`` does NOT contain ``model_override``.
        """
        config = create_mock_config(model="default-model", allowed_models=[])
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(agent_id="x")
        captured = _spawn_and_capture_instance_metadata(lifecycle, "x", metadata)
        assert "model_override" not in captured

    def test_override_source_persists_model_override(self) -> None:
        """Explicit caller-supplied ``model`` →
        ``resolved_source == "override"`` → ``model_override`` persisted with
        the forced value (existing behavior; load balancing is skipped).
        """
        config = create_mock_config(
            model="default-model", allowed_models=["forced-model"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_models=[LLMModelWeight(model="pool-model", weight=1)],
        )
        captured = _spawn_and_capture_instance_metadata(
            lifecycle,
            "x",
            metadata,
            model_arg="forced-model",
            allowed_models=["forced-model"],
        )
        assert captured["model_override"] == "forced-model"

    def test_all_llm_models_filtered_does_not_persist_model_override(self) -> None:
        """All ``llm_models`` entries filtered by ``allowed_models`` →
        ``resolved_source`` falls through to ``"llm_model"`` → no
        ``model_override`` persisted.
        """
        config = create_mock_config(
            model="default-model", allowed_models=["other-model"]
        )
        manager = create_mock_manager(config)
        lifecycle = InstanceLifecycleService(manager, MagicMock())

        metadata = create_metadata(
            agent_id="x",
            llm_model="fallback-model",
            llm_models=[LLMModelWeight(model="disallowed-model", weight=1)],
        )
        captured = _spawn_and_capture_instance_metadata(
            lifecycle,
            "x",
            metadata,
            allowed_models=["other-model"],
        )
        assert "model_override" not in captured
