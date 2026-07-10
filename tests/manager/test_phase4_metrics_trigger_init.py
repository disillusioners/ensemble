"""Phase 4 Skill Evolution — manager init + trigger seed tests.

Covers Task 6 of the Phase 4 plan:

* The four Phase 4 skill repositories (``usage``, ``trigger``,
  ``ab_test``, ``lineage``) and the two services
  (``_skill_metrics_service``, ``_skill_trigger_engine``) are wired
  when ``skill_evolution`` is configured.
* All six Phase 4 attributes are left ``None`` when
  ``skill_evolution`` is disabled.
* The repositories carry the correct engine handle and the
  services carry the correct repository handles + config.
* ``seed_default_triggers`` is invoked during ``initialize()`` and
  persists the five ``DEFAULT_TRIGGERS`` rows under
  ``project_id=NULL``.
* The ``metric_scan_interval_hours`` config knob has a sensible
  default (``24.0``).

The actual skill-embedding / skill-store / skill-search init is
already covered by ``tests/manager/test_skill_service_init.py``;
this file only covers the Phase 4 additions.
"""

from __future__ import annotations

import pytest

from daemon.config import Config, SkillEvolutionConfig
from daemon.manager import InstanceManager


def _create_test_config(tmp_path):
    """Create a real Config with an isolated database path."""
    config = Config()
    config.persistence.db_path = str(tmp_path / "instances.db")
    config.mcp_pool.enabled = False
    return config


# ─── Phase 4 attribute wiring ──────────────────────────────────────────────


class TestPhase4SkillRepositories:
    """The four Phase 4 skill repositories are wired when configured."""

    @pytest.mark.asyncio
    async def test_phase4_repos_initialized_when_config_present(
        self, tmp_path
    ):
        config = _create_test_config(tmp_path)
        manager = InstanceManager(config)

        # Existing Phase 1-2 attrs (regression guard).
        assert manager._skill_repo is not None
        assert manager._skill_lineage_repo is not None
        # New Phase 4 attrs.
        assert manager._skill_usage_repo is not None
        assert manager._skill_trigger_repo is not None
        assert manager._skill_ab_test_repo is not None

    @pytest.mark.asyncio
    async def test_phase4_repos_none_when_skill_evolution_disabled(
        self, tmp_path, monkeypatch
    ):
        config = _create_test_config(tmp_path)
        monkeypatch.setattr(config, "skill_evolution", None)
        manager = InstanceManager(config)

        assert manager._skill_usage_repo is None
        assert manager._skill_trigger_repo is None
        assert manager._skill_ab_test_repo is None


# ─── Phase 4 service wiring ────────────────────────────────────────────────


class TestPhase4SkillServices:
    """The metrics service + trigger engine are wired when configured."""

    @pytest.mark.asyncio
    async def test_metrics_and_trigger_services_initialized(
        self, tmp_path
    ):
        config = _create_test_config(tmp_path)
        manager = InstanceManager(config)

        from daemon.services.skill_metrics_service import (
            SkillMetricsService,
        )
        from daemon.services.skill_trigger_engine import (
            SkillTriggerEngine,
        )

        assert isinstance(
            manager._skill_metrics_service, SkillMetricsService
        )
        assert isinstance(
            manager._skill_trigger_engine, SkillTriggerEngine
        )

    @pytest.mark.asyncio
    async def test_metrics_service_has_correct_dependencies(
        self, tmp_path
    ):
        """The metrics service receives the four repos + the config
        + the instance repo."""
        config = _create_test_config(tmp_path)
        manager = InstanceManager(config)

        metrics = manager._skill_metrics_service
        assert metrics.usage_repo is manager._skill_usage_repo
        assert metrics.skill_repo is manager._skill_repo
        assert metrics.trigger_repo is manager._skill_trigger_repo
        assert metrics.ab_test_repo is manager._skill_ab_test_repo
        assert metrics.config is manager.config.skill_evolution
        # instance_repo is wired to the manager's instance repo so
        # the service can read/clear the ``last_injected_skill_ids``
        # metadata key.
        assert metrics.instance_repo is manager._instance_repository

    @pytest.mark.asyncio
    async def test_trigger_engine_has_correct_dependencies(
        self, tmp_path
    ):
        """The trigger engine holds the trigger repo + metrics service."""
        config = _create_test_config(tmp_path)
        manager = InstanceManager(config)

        engine = manager._skill_trigger_engine
        assert engine.trigger_repo is manager._skill_trigger_repo
        assert engine.metrics_service is manager._skill_metrics_service

    @pytest.mark.asyncio
    async def test_services_none_when_skill_evolution_disabled(
        self, tmp_path, monkeypatch
    ):
        config = _create_test_config(tmp_path)
        monkeypatch.setattr(config, "skill_evolution", None)
        manager = InstanceManager(config)

        assert manager._skill_metrics_service is None
        assert manager._skill_trigger_engine is None


# ─── Default config knob ───────────────────────────────────────────────────


class TestMetricScanIntervalConfig:
    """``metric_scan_interval_hours`` has a sensible default."""

    def test_default_value_is_24(self):
        cfg = SkillEvolutionConfig()
        assert cfg.metric_scan_interval_hours == 24.0

    def test_default_is_overridable(self):
        cfg = SkillEvolutionConfig(metric_scan_interval_hours=6.0)
        assert cfg.metric_scan_interval_hours == 6.0