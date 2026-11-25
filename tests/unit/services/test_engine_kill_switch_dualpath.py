"""P3 T6 — kill switches (fail-to-legacy) + dual-path harness."""

from __future__ import annotations

import os
from typing import Any

import pytest

from daemon.services.engine.killswitch import (
    EngineSwitchResolver,
    kill_switch_from_env,
    normalize_switch_value,
)
from daemon.services.engine.dualpath import (
    EngineOwnership,
    ownership_for,
    use_engine_enabled,
)


class FixtureModule:
    """A fixture module proving both paths (T3.3).

    Holds a legacy tick counter and an engine run counter. ``tick()`` increments
    exactly ONE of them per call based on the current ownership (runtime flip
    moves ownership on the next tick, no restart).
    """

    def __init__(self, slug: str = "fixture", environ: Any = None) -> None:
        self.slug = slug
        self._environ = environ
        self.legacy_ticks: int = 0
        self.engine_runs: int = 0
        self.registered_with_engine: bool = False

    def _ownership(self) -> EngineOwnership:
        return ownership_for(self.slug, self._environ)

    def set_environ(self, environ: Any) -> None:
        self._environ = environ

    def tick(self) -> str:
        own = self._ownership()
        if own.engine_owns:
            self.engine_runs += 1
            self.registered_with_engine = True
            return "engine"
        else:
            self.legacy_ticks += 1
            self.registered_with_engine = False
            return "legacy"

    def reset(self) -> None:
        self.legacy_ticks = 0
        self.engine_runs = 0
        self.registered_with_engine = False


def test_normalize_switch_value():
    assert normalize_switch_value(None) is None
    assert normalize_switch_value("") is None
    assert normalize_switch_value("garbage") is None
    assert normalize_switch_value("maybe") is None
    assert normalize_switch_value("0") is False
    assert normalize_switch_value("false") is False
    assert normalize_switch_value("off") is False
    assert normalize_switch_value("1") is True
    assert normalize_switch_value("true") is True
    assert normalize_switch_value(True) is True
    assert normalize_switch_value(False) is False


def test_fail_to_legacy_unreadable_engine_disabled():
    # malformed engine-wide switch => engine_enabled False (fail-to-legacy)
    resolver = EngineSwitchResolver(lambda k: "garbage" if k == "ENSEMBLE_ENGINE_ENABLED" else None)
    assert resolver.engine_enabled() is False


def test_explicit_off_values_disable():
    for v in ("0", "false", "no", "off"):
        resolver = EngineSwitchResolver(lambda k, v=v: v if k == "ENSEMBLE_ENGINE_ENABLED" else None)
        assert resolver.engine_enabled() is False


def test_explicit_truthy_enables():
    for v in ("1", "true", "yes", "on"):
        resolver = EngineSwitchResolver(lambda k, v=v: v if k == "ENSEMBLE_ENGINE_ENABLED" else None)
        assert resolver.engine_enabled() is True


def test_absent_engine_defaults_true():
    resolver = EngineSwitchResolver(lambda k: None)
    assert resolver.engine_enabled() is True


def test_per_job_unreadable_killed():
    resolver = EngineSwitchResolver(lambda k: "garbage" if k == "MY_KEY" else None)
    assert resolver.job_killed("MY_KEY") is True


def test_per_job_explicit_off_killed():
    resolver = EngineSwitchResolver(lambda k: "0" if k == "MY_KEY" else None)
    assert resolver.job_killed("MY_KEY") is True


def test_per_job_absent_not_killed():
    resolver = EngineSwitchResolver(lambda k: None)
    assert resolver.job_killed("MY_KEY") is False


def test_resolve_job_killed_returns_skip_reason():
    resolver = EngineSwitchResolver(lambda k: "0" if k == "MY_KEY" else None)
    spec = type("S", (), {"kill_switch_key": "MY_KEY"})()
    run, reason = resolver.resolve_job(spec)
    assert run is False
    assert reason == "KILL_SWITCHED"


def test_shadow_mode_default_true_and_safe_on_malformed():
    resolver = EngineSwitchResolver(lambda k: "garbage" if k == "ENSEMBLE_ENGINE_SHADOW_MODE" else None)
    assert resolver.shadow_mode() is True


def test_kill_switch_from_env_reaper_pattern():
    env = {}
    assert kill_switch_from_env(env, "K") is True
    env = {"K": ""}
    assert kill_switch_from_env(env, "K") is True
    env = {"K": "0"}
    assert kill_switch_from_env(env, "K") is False
    env = {"K": "garbage"}
    assert kill_switch_from_env(env, "K") is True


# dual-path
def test_use_engine_enabled_default_off():
    assert use_engine_enabled("foo", {}) is False
    assert use_engine_enabled("foo", {}) is False


def test_use_engine_enabled_on():
    env = {"SERVICES_FOO_USE_ENGINE": "1"}
    assert use_engine_enabled("foo", env) is True
    env = {"SERVICES_FOO_USE_ENGINE": "true"}
    assert use_engine_enabled("foo", env) is True


def test_use_engine_enabled_off_explicit():
    env = {"SERVICES_FOO_USE_ENGINE": "0"}
    assert use_engine_enabled("foo", env) is False
    env = {"SERVICES_FOO_USE_ENGINE": "false"}
    assert use_engine_enabled("foo", env) is False


def test_ownership_record():
    own = ownership_for("foo", {"SERVICES_FOO_USE_ENGINE": "1"})
    assert own.engine_owns is True
    assert own.legacy_owns is False
    assert own.decide() == "engine"
    own2 = ownership_for("foo", {})
    assert own2.engine_owns is False
    assert own2.legacy_owns is True
    assert own2.decide() == "legacy"


def test_fixture_module_off_legacy_climbs():
    fm = FixtureModule("foo", {})
    for _ in range(5):
        fm.tick()
    assert fm.legacy_ticks == 5
    assert fm.engine_runs == 0
    assert fm.registered_with_engine is False


def test_fixture_module_on_engine_climbs():
    fm = FixtureModule("foo", {"SERVICES_FOO_USE_ENGINE": "1"})
    for _ in range(5):
        fm.tick()
    assert fm.legacy_ticks == 0
    assert fm.engine_runs == 5
    assert fm.registered_with_engine is True


def test_fixture_module_runtime_flip():
    env = {}
    fm = FixtureModule("foo", env)
    fm.tick()
    assert fm.legacy_ticks == 1
    # flip at runtime
    env["SERVICES_FOO_USE_ENGINE"] = "1"
    fm.tick()
    assert fm.engine_runs == 1
    # ownership moved without restart (same instance)
