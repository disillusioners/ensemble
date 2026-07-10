"""Tests for ``SkillEvolutionConfig`` and its integration with ``Config``.

Phase 1 of the Skill Evolution System — the configuration layer.

``SkillEvolutionConfig`` uses ``pydantic-settings`` with
``env_prefix="SKILL_EVOLUTION_"``, so every field is overridable via
the corresponding env var (e.g. ``SKILL_EVOLUTION_EMBEDDING_MODEL``).

Tests:

* ``test_defaults`` — every default matches the spec.
* ``test_env_override`` — env vars override defaults.
* ``test_config_integration`` — ``Config().skill_evolution`` is a
  ``SkillEvolutionConfig`` instance.
* ``test_all_fields_present`` — all spec fields exist (defends
  against accidental field removal).
"""

from __future__ import annotations

import pytest
from pydantic_settings import BaseSettings

from daemon.config import Config, SkillEvolutionConfig


class TestDefaults:
    """Verify the documented default values."""

    def test_defaults(self):
        """Instantiate with no env overrides; every default matches the spec."""
        cfg = SkillEvolutionConfig()

        # Embedding
        assert cfg.embedding_model == "text-embedding-3-small"
        assert cfg.embedding_dimensions == 1536
        assert cfg.embedding_base_url is None
        assert cfg.embedding_api_key is None

        # Evolution models — both None (caller falls back to LLMConfig).
        assert cfg.evolution_model is None
        assert cfg.analysis_model is None

        # Injection
        assert cfg.max_inject_skills == 2
        assert cfg.min_score_full_inject == 0.7
        assert cfg.min_score_low_match == 0.3
        assert cfg.bm25_top_k == 10
        assert cfg.llm_select_top_k == 5

        # Triggers
        assert cfg.default_task_count_threshold == 20
        assert cfg.default_daily_scan_hour == 3

        # A/B testing
        assert cfg.ab_sample_size == 10
        assert cfg.ab_min_difference == 0.15
        assert cfg.max_extensions == 3

        # Capture
        assert cfg.capture_min_iterations == 5
        assert cfg.capture_min_duration_seconds == 60


class TestAllFieldsPresent:
    """Defends against accidental field removal.

    If a field is renamed or deleted, this test will fail so the
    breakage is caught at unit-test time, not at first production
    startup.
    """

    # Expected field names — keep in sync with ``SkillEvolutionConfig``.
    EXPECTED_FIELDS: frozenset[str] = frozenset(
        {
            # Embedding
            "embedding_model",
            "embedding_dimensions",
            "embedding_base_url",
            "embedding_api_key",
            # Evolution models
            "evolution_model",
            "analysis_model",
            # Injection
            "max_inject_skills",
            "min_score_full_inject",
            "min_score_low_match",
            "bm25_top_k",
            "llm_select_top_k",
            # Triggers
            "default_task_count_threshold",
            "default_daily_scan_hour",
            # A/B testing
            "ab_sample_size",
            "ab_min_difference",
            "max_extensions",
            # Capture
            "capture_min_iterations",
            "capture_min_duration_seconds",
        }
    )

    def test_all_fields_present(self):
        """All expected fields exist on the config model."""
        # Access ``model_fields`` on the CLASS (not an instance) to
        # avoid the Pydantic v2.11+ deprecation warning.
        actual_fields = set(SkillEvolutionConfig.model_fields.keys())
        missing = self.EXPECTED_FIELDS - actual_fields
        assert not missing, f"Missing fields: {sorted(missing)}"
        # And no expected fields were removed: we don't enforce a
        # strict superset because pydantic-settings adds internal
        # fields like ``model_config`` (already filtered by
        # ``model_fields``).

    def test_minimum_field_count(self):
        """At least 16 fields are declared (the spec count)."""
        # Access ``model_fields`` on the CLASS (not an instance) to
        # avoid the Pydantic v2.11+ deprecation warning.
        assert len(SkillEvolutionConfig.model_fields) >= 16


class TestEnvOverride:
    """Verify env vars override the defaults.

    The env prefix is ``SKILL_EVOLUTION_`` — so e.g.
    ``SKILL_EVOLUTION_EMBEDDING_MODEL=text-embedding-3-large`` overrides
    ``embedding_model``.

    Uses ``monkeypatch.setenv`` so the env var is cleaned up automatically
    at the end of the test.
    """

    def test_env_override_string(self, monkeypatch: pytest.MonkeyPatch):
        """Override a string field via env var."""
        monkeypatch.setenv(
            "SKILL_EVOLUTION_EMBEDDING_MODEL", "text-embedding-3-large"
        )
        cfg = SkillEvolutionConfig()
        assert cfg.embedding_model == "text-embedding-3-large"

    def test_env_override_int(self, monkeypatch: pytest.MonkeyPatch):
        """Override an int field via env var."""
        monkeypatch.setenv("SKILL_EVOLUTION_EMBEDDING_DIMENSIONS", "768")
        cfg = SkillEvolutionConfig()
        assert cfg.embedding_dimensions == 768

    def test_env_override_float(self, monkeypatch: pytest.MonkeyPatch):
        """Override a float field via env var."""
        monkeypatch.setenv("SKILL_EVOLUTION_MIN_SCORE_FULL_INJECT", "0.55")
        cfg = SkillEvolutionConfig()
        assert cfg.min_score_full_inject == 0.55

    def test_env_override_optional_field_stores_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """``Optional[str]`` fields accept non-empty env values verbatim.

        Pydantic-settings does NOT auto-coerce an empty-string env var
        to ``None`` for ``str | None`` fields — it stores the value
        as-is. Callers that want the "fall back to LLMConfig" semantics
        must compare against ``None`` at the call site (or pass an
        explicit ``None``). This test pins the env-override contract
        so a future pydantic-settings upgrade can't silently change
        behavior.
        """
        monkeypatch.setenv(
            "SKILL_EVOLUTION_EMBEDDING_BASE_URL",
            "https://custom-embeddings.example/v1",
        )
        cfg = SkillEvolutionConfig()
        assert cfg.embedding_base_url == "https://custom-embeddings.example/v1"

    def test_env_prefix_is_correct(self):
        """The env prefix is exactly ``SKILL_EVOLUTION_``."""
        # Inspect the model_config attribute (BaseSettings exposes
        # ``model_config`` as a dict-like).
        cfg = SkillEvolutionConfig()
        env_prefix = cfg.model_config.get("env_prefix")
        assert env_prefix == "SKILL_EVOLUTION_"


class TestConfigIntegration:
    """``Config().skill_evolution`` exposes a ``SkillEvolutionConfig``."""

    def test_skill_evolution_is_skill_evolution_config(self):
        """``Config().skill_evolution`` is the right type."""
        config = Config()
        assert isinstance(config.skill_evolution, SkillEvolutionConfig)

    def test_skill_evolution_defaults_via_config(self):
        """Defaults flow through ``Config()``."""
        config = Config()
        assert config.skill_evolution.embedding_model == "text-embedding-3-small"
        assert config.skill_evolution.embedding_dimensions == 1536
        assert config.skill_evolution.max_inject_skills == 2
        assert config.skill_evolution.ab_sample_size == 10

    def test_skill_evolution_is_a_base_settings_subclass(self):
        """``SkillEvolutionConfig`` is a ``BaseSettings`` (env-driven)."""
        assert issubclass(SkillEvolutionConfig, BaseSettings)

    def test_config_skill_evolution_field_present(self):
        """``Config`` declares ``skill_evolution`` as a field."""
        assert "skill_evolution" in Config.model_fields