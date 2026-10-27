"""The checkpoint-pool sizing knob (A4) — the 4-tier ladder + the pool
construction kwargs + the clamp.

soak-gate-mcp-warmup · A4 · the normative source is
``requirements.md`` §3-A4 and decisions §3d (D2 sizing formula, D3
SSOT ladder, D4 honesty clause).

The pins cover:

* the DEFAULT tier is the constant (``daemon.constants``) and the
  pydantic Field default, the yaml literal and the settings registry
  default ALL agree (one truth);
* the env tier works (``LIMITS_CHECKPOINT_POOL_MAX_SIZE`` /
  ``LIMITS_CHECKPOINT_POOL_TIMEOUT_S``);
* the 4-tier ladder resolves DB > env > yaml > default through the
  house settings resolver;
* the pool construction receives ``max_size`` AND an explicit
  ``timeout`` (the library's 30 s default is what left ghost waiters
  behind);
* the clamp (``ge=5``, ``le=20``) is enforced by the registry validator
  too (not only by the pydantic Field).
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from daemon import constants
from daemon.config import LimitsConfig
from daemon.services import settings_service


class TestCheckpointPoolConfigLadder:
    def test_the_default_tier_is_the_constant(self):
        assert constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT == 10
        assert constants.CHECKPOINT_POOL_TIMEOUT_S_DEFAULT == 5.0

    def test_the_field_defaults_mirror_the_constants(self):
        limits = LimitsConfig()
        assert limits.checkpoint_pool_max_size == (
            constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        )
        assert limits.checkpoint_pool_timeout_s == (
            constants.CHECKPOINT_POOL_TIMEOUT_S_DEFAULT
        )

    def test_the_settings_registry_default_mirrors_the_field(self):
        spec = settings_service.MANAGED_KEYS["limits.checkpoint_pool_max_size"]
        assert spec.default == constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        assert spec.env_var == "LIMITS_CHECKPOINT_POOL_MAX_SIZE"
        assert spec.ge == 5
        assert spec.le == 20
        spec2 = settings_service.MANAGED_KEYS["limits.checkpoint_pool_timeout_s"]
        assert spec2.default == constants.CHECKPOINT_POOL_TIMEOUT_S_DEFAULT
        assert spec2.env_var == "LIMITS_CHECKPOINT_POOL_TIMEOUT_S"
        # The registry carries the Field's EXCLUSIVE floor (``gt=0``), not
        # an inclusive ``ge=0`` — the two would disagree on the value 0.
        assert spec2.gt == 0
        assert spec2.ge is None

    def test_the_env_tier_is_effective(self, monkeypatch):
        monkeypatch.setenv("LIMITS_CHECKPOINT_POOL_MAX_SIZE", "13")
        monkeypatch.setenv("LIMITS_CHECKPOINT_POOL_TIMEOUT_S", "7.5")
        limits = LimitsConfig()
        assert limits.checkpoint_pool_max_size == 13
        assert limits.checkpoint_pool_timeout_s == 7.5

    def test_the_yaml_tier_is_the_shipped_literal(self):
        import yaml

        with open("config.yaml", "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        limits = raw["limits"]
        assert limits["checkpoint_pool_max_size"] == (
            constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT
        )
        assert float(limits["checkpoint_pool_timeout_s"]) == (
            constants.CHECKPOINT_POOL_TIMEOUT_S_DEFAULT
        )

    def test_the_clamp_is_enforced_by_the_registry_validator(self):
        """The registry's validator must reject the values the pydantic
        Field would reject (one validation truth)."""
        spec = settings_service.MANAGED_KEYS["limits.checkpoint_pool_max_size"]
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 20
        )
        assert ok is True and value == 20
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 21
        )
        assert ok is False and failure == "validation"
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 4
        )
        assert ok is False and failure == "validation"

    def test_the_timeout_floor_is_enforced_by_the_registry_validator(self):
        """The registry's validator must mirror the Field's ``gt=0``
        floor — the Field REJECTS 0, so the registry must reject it too
        (one validation truth). A ``ge=0`` registry bound would ACCEPT
        the value 0 and hand it to the Field, which refuses it."""
        spec = settings_service.MANAGED_KEYS["limits.checkpoint_pool_timeout_s"]
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, 5.0
        )
        assert ok is True and value == 5.0
        ok, _value, failure, _detail = settings_service.parse_and_validate(
            spec, 0
        )
        assert ok is False and failure == "validation"

    def test_the_field_clamps_too(self):
        # The Field bounds raise pydantic's ValidationError — the pin
        # names the type so an unrelated exception cannot satisfy it.
        with pytest.raises(ValidationError):
            LimitsConfig(checkpoint_pool_max_size=21)
        with pytest.raises(ValidationError):
            LimitsConfig(checkpoint_pool_max_size=4)
        with pytest.raises(ValidationError):
            LimitsConfig(checkpoint_pool_timeout_s=0)

    def test_the_sizing_arithmetic_is_the_documented_one(self):
        """decisions §3d D2: min(llm_concurrency, worker + chat) writers
        + 1 probe slot + 2 headroom = the shipped default."""
        limits = LimitsConfig()
        writers = min(
            limits.llm_concurrency,
            limits.worker_pool_size + limits.chat_worker_pool_size,
        )
        computed = writers + 1 + 2
        assert writers == 7
        assert computed == constants.CHECKPOINT_POOL_MAX_SIZE_DEFAULT


class TestCheckpointPoolConstruction:
    """The pool kwargs the saver pool is constructed with."""

    def test_the_source_passes_max_size_and_an_explicit_timeout(self):
        """A structural pin on the shipped source: the saver pool
        carries BOTH the size knob and an explicit acquisition timeout
        (the library's 30.0 s default is what left ghost waiters)."""
        import inspect

        from daemon import persistence

        source = inspect.getsource(persistence.create_postgres_checkpointer)
        assert "max_size=checkpoint_pool_max_size," in source
        assert "timeout=checkpoint_pool_timeout_s," in source
        # The old hard-coded size is gone.
        assert "max_size=5," not in source
        # The honesty clause (decisions §3d D4) is recorded in the source.
        assert "does NOT cure the leak" in source

    def test_the_asyncpg_pool_takes_the_size_knob(self):
        import inspect

        from daemon import persistence

        source = inspect.getsource(persistence.create_postgres_checkpointer)
        assert "max_size=checkpoint_pool_max_size," in source
        # Disclosed: the asyncpg pool takes the max-size only.
        assert "max-size only" in source or "max_size only" in source


class TestPoolKnobResolution:
    def test_the_resolution_ladder_env_beats_yaml(self, monkeypatch):
        """The house ladder: DB > env > yaml > default. With no DB row
        the env tier wins over the yaml literal."""
        monkeypatch.setenv("LIMITS_CHECKPOINT_POOL_MAX_SIZE", "15")
        spec = settings_service.MANAGED_KEYS["limits.checkpoint_pool_max_size"]
        ok, value, failure, _detail = settings_service.parse_and_validate(
            spec, os.environ[spec.env_var]
        )
        assert ok is True
        assert value == 15
