"""Config tests for the service-tool Phase 1 knobs (D7 kill-switch + D5/D6 caps).

This file closes a flagged test-coverage gap. The three service-tool config
resolvers landed in commit ``ea4ae0d2`` (with the empty-yaml guard fix in
``e0ed1afc``) but shipped with zero unit tests.

Coverage pins (mirror the kill-switch + cap resolver patterns from
:mod:`test_empty_guard_config` + the boot-probe pattern from
:mod:`test_critical_notes_config`):

* ``ENSEMBLE_SERVICE_TOOL_ENABLED`` (kill-switch) — env > yaml > default
  ``True``; empty-string safe both sides; permissive bool vocabulary
  (``0/false/no/off`` vs ``1/true/yes/on``); invalid values raise
  ``ValueError`` naming the env.
* ``ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT`` — env > yaml > default ``10``;
  empty-string safe both sides; strict-int (raises ``ValueError`` naming
  the env).
* ``ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL`` — env > yaml > default
  ``90``; empty-string safe both sides; strict-int (raises ``ValueError``
  naming the env).
* ``ServiceToolConfig.max_concurrent`` + ``ServicesConfig.service_tool_reconcile_interval_seconds``
  both have ``Field(ge=1)`` — fail-fast ``ValidationError`` on
  zero/negative (init-kwarg-beats-env inversion trap closed).
* Boot INFO line at config-resolution time (S13 reviewer gate)
  — EXACT format
  ``[ServiceTool] service_tool_enabled=%s (env ENSEMBLE_SERVICE_TOOL_ENABLED), max_concurrent=%s, reconcile_interval=%ss``.
* ``_install_service_tool_enabled`` writes the module cache consumed by
  :func:`service_tool_enabled` — the field the api.py lifespan gates on
  to skip starting the sweep.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from daemon.config import (
    ENSEMBLE_SERVICE_TOOL_ENABLED,
    ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT,
    ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL,
    ServiceToolConfig,
    ServicesConfig,
    _install_service_tool_enabled,
    _parse_service_tool_int,
    _reset_service_tool_for_tests,
    _resolve_service_tool_enabled,
    _resolve_service_tool_max_concurrent,
    _resolve_service_tool_reconcile_interval,
    load_config,
    service_tool_enabled,
)


@pytest.fixture(autouse=True)
def _reset_service_tool_module_state():
    """Clear the cached kill-switch between tests (restart-to-flip semantics).

    The :func:`_install_service_tool_enabled` installer writes the
    module-level ``_SERVICE_TOOL_ENABLED`` cache that
    :func:`service_tool_enabled` reads back. The conftest's ``clean_env``
    autouse handles ``ENSEMBLE_*`` env-var restoration; this fixture
    handles the module-level state (a separate, non-env surface).
    """
    _reset_service_tool_for_tests()
    yield
    _reset_service_tool_for_tests()


class TestResolveServiceToolEnabled:
    """Precedence (env > yaml > default) + empty-string safety + bool vocabulary."""

    # ── env > yaml > default precedence ──────────────────────────────────

    def test_env_zero_wins_over_yaml_true(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled("0", True) is False

    def test_env_one_wins_over_yaml_false(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled("1", False) is True

    def test_yaml_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled(None, True) is True
        assert _resolve_service_tool_enabled(None, False) is False

    def test_default_true_when_both_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        # Documented default ON (D7): an unset env + an absent yaml must
        # NOT silently disable the feature.
        assert _resolve_service_tool_enabled(None, None) is True

    # ── empty-string safety (BOTH sides — e0ed1afc fix) ──────────────────

    def test_empty_env_falls_to_yaml_value(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled("", False) is False
        assert _resolve_service_tool_enabled("   ", True) is True

    def test_empty_env_falls_to_default_when_yaml_also_empty(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled("", None) is True
        assert _resolve_service_tool_enabled("   ", None) is True

    def test_empty_yaml_string_falls_to_default(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        # e0ed1afc guard: yaml shipped an empty/whitespace string MUST
        # not raise and MUST resolve to the documented default ON.
        assert _resolve_service_tool_enabled(None, "") is True
        assert _resolve_service_tool_enabled(None, "   ") is True

    # ── bool resolver vocabulary ──────────────────────────────────────────

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", " OFF "])
    def test_falsy_vocabulary(self, raw, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled(raw, None) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " on "])
    def test_truthy_vocabulary(self, raw, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        assert _resolve_service_tool_enabled(raw, None) is True

    def test_invalid_value_raises_with_env_name(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        with pytest.raises(ValueError, match=ENSEMBLE_SERVICE_TOOL_ENABLED):
            _resolve_service_tool_enabled("banana", None)


class TestResolveServiceToolMaxConcurrent:
    """Precedence (env > yaml > default 10) + empty-string safety + strict-int."""

    # ── env > yaml > default precedence ──────────────────────────────────

    def test_env_wins_over_yaml(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent("25", 5) == 25

    def test_yaml_int_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent(None, 5) == 5

    def test_yaml_str_int_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        # YAML ships the value as a quoted string sometimes — must parse.
        assert _resolve_service_tool_max_concurrent(None, "5") == 5

    def test_default_10_when_both_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent(None, None) == 10

    # ── empty-string safety (e0ed1afc guard on the int side) ─────────────

    def test_empty_env_falls_to_yaml(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent("", 5) == 5
        assert _resolve_service_tool_max_concurrent("   ", 5) == 5

    def test_empty_env_falls_to_default_when_yaml_also_empty(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent("", None) == 10
        assert _resolve_service_tool_max_concurrent("   ", None) == 10

    def test_empty_yaml_string_falls_to_default(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        # The e0ed1afc fix: empty/whitespace YAML MUST NOT raise; it MUST
        # fall through to the documented 10 default.
        assert _resolve_service_tool_max_concurrent(None, "") == 10
        assert _resolve_service_tool_max_concurrent(None, "   ") == 10

    # ── strict-int contract ──────────────────────────────────────────────

    @pytest.mark.parametrize("raw,expected", [("25", 25), ("120", 120), ("  7  ", 7)])
    def test_int_like_string_parses(self, raw, expected, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        assert _resolve_service_tool_max_concurrent(raw, None) == expected

    def test_non_int_string_env_raises_value_error(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        with pytest.raises(ValueError, match=ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT):
            _resolve_service_tool_max_concurrent("abc", None)

    def test_float_string_env_raises_value_error(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        with pytest.raises(ValueError, match=ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT):
            _resolve_service_tool_max_concurrent("1.5", None)

    def test_yaml_non_int_raises_naming_yaml_key(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        with pytest.raises(ValueError, match="services.service_tool.max_concurrent"):
            _resolve_service_tool_max_concurrent(None, "not-a-number")


class TestResolveServiceToolReconcileInterval:
    """Precedence (env > yaml > default 90) + empty-string safety + strict-int."""

    # ── env > yaml > default precedence ──────────────────────────────────

    def test_env_wins_over_yaml(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval("120", 30) == 120

    def test_yaml_int_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval(None, 60) == 60

    def test_yaml_str_int_used_when_env_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval(None, "60") == 60

    def test_default_90_when_both_unset(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval(None, None) == 90

    # ── empty-string safety (e0ed1afc guard on the int side) ─────────────

    def test_empty_env_falls_to_yaml(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval("", 60) == 60
        assert _resolve_service_tool_reconcile_interval("   ", 60) == 60

    def test_empty_env_falls_to_default_when_yaml_also_empty(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval("", None) == 90
        assert _resolve_service_tool_reconcile_interval("   ", None) == 90

    def test_empty_yaml_string_falls_to_default(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval(None, "") == 90
        assert _resolve_service_tool_reconcile_interval(None, "   ") == 90

    # ── strict-int contract ──────────────────────────────────────────────

    @pytest.mark.parametrize("raw,expected", [("60", 60), ("300", 300), ("  45  ", 45)])
    def test_int_like_string_parses(self, raw, expected, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        assert _resolve_service_tool_reconcile_interval(raw, None) == expected

    def test_non_int_string_env_raises_value_error(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        with pytest.raises(ValueError, match=ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL):
            _resolve_service_tool_reconcile_interval("nope", None)

    def test_yaml_non_int_raises_naming_yaml_key(self, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        with pytest.raises(ValueError, match="service_tool_reconcile_interval_seconds"):
            _resolve_service_tool_reconcile_interval(None, "nope")


class TestParseServiceToolInt:
    """Direct coverage for the strict-int helper used by both int resolvers."""

    def test_int_passthrough(self):
        assert _parse_service_tool_int(25, env_name="X") == 25

    def test_int_string_parses(self):
        assert _parse_service_tool_int("25", env_name="X") == 25

    def test_stripped_int_string_parses(self):
        assert _parse_service_tool_int("  25  ", env_name="X") == 25

    def test_bool_true_rejected(self):
        # ``bool`` is a subclass of ``int`` in Python (``True == 1``);
        # the resolver must REJECT it explicitly so ``True`` from yaml
        # does NOT silently coerce to 1.
        with pytest.raises(ValueError, match="X"):
            _parse_service_tool_int(True, env_name="X")

    def test_bool_false_rejected(self):
        with pytest.raises(ValueError, match="X"):
            _parse_service_tool_int(False, env_name="X")

    def test_non_int_string_raises_with_env_name(self):
        with pytest.raises(ValueError, match="X"):
            _parse_service_tool_int("abc", env_name="X")

    def test_float_string_raises(self):
        # ``int("1.5")`` raises; the resolver must NOT silently truncate.
        with pytest.raises(ValueError, match="X"):
            _parse_service_tool_int("1.5", env_name="X")

    def test_none_rejected(self):
        # Operator-typo convention: anything that isn't int / str / bool
        # MUST raise loud.
        with pytest.raises(ValueError, match="X"):
            _parse_service_tool_int(None, env_name="X")


class TestServiceToolConfigGeConstraint:
    """``Field(ge=1)`` fail-fast on the nested block — zero/negative rejected."""

    def test_default_max_concurrent_is_10(self):
        assert ServiceToolConfig().max_concurrent == 10

    def test_default_enabled_is_true(self):
        assert ServiceToolConfig().enabled is True

    def test_max_concurrent_zero_rejected(self):
        with pytest.raises(ValidationError):
            ServiceToolConfig(max_concurrent=0)

    def test_max_concurrent_negative_rejected(self):
        with pytest.raises(ValidationError):
            ServiceToolConfig(max_concurrent=-1)

    def test_max_concurrent_one_is_floor(self):
        assert ServiceToolConfig(max_concurrent=1).max_concurrent == 1


class TestServicesConfigReconcileIntervalGeConstraint:
    """``Field(ge=1)`` fail-fast on the flat ServicesConfig sibling field."""

    def test_default_is_90(self):
        assert ServicesConfig().service_tool_reconcile_interval_seconds == 90

    def test_zero_rejected(self):
        with pytest.raises(ValidationError):
            ServicesConfig(service_tool_reconcile_interval_seconds=0)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            ServicesConfig(service_tool_reconcile_interval_seconds=-5)

    def test_one_is_floor(self):
        assert (
            ServicesConfig(service_tool_reconcile_interval_seconds=1)
            .service_tool_reconcile_interval_seconds
            == 1
        )


class TestBootProbeEmission:
    """Boot INFO line emitted at config-resolution time (1.C.10, S13 reviewer gate).

    The line MUST stay on the boot path — a lazy first-call emit would
    make a quiet-daemon ``grep '\\[ServiceTool\\]'`` false-fail. We pin
    the EXACT format string here.
    """

    @staticmethod
    def _write_minimal_config(tmp_path: Path) -> Path:
        path = tmp_path / "config.yaml"
        # A non-empty yaml (a comment-only file parses to None and
        # ``load_config`` rejects it as empty by design). Mirror the
        # critical_notes fixture idiom.
        path.write_text("queue:\n  discard_on_startup: false\n")
        return path

    def test_default_config_emits_canonical_probe(self, tmp_path, caplog):
        path = self._write_minimal_config(tmp_path)
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(path)
        # EXACT format pinned by plan task 1.C.10 (see config.py:4004-4010).
        assert "[ServiceTool]" in caplog.text
        assert "service_tool_enabled=True" in caplog.text
        assert "max_concurrent=10" in caplog.text
        assert "reconcile_interval=90s" in caplog.text
        assert "(env ENSEMBLE_SERVICE_TOOL_ENABLED)" in caplog.text

    def test_env_overrides_visible_in_probe(self, tmp_path, caplog, monkeypatch):
        path = self._write_minimal_config(tmp_path)
        monkeypatch.setenv(ENSEMBLE_SERVICE_TOOL_ENABLED, "0")
        monkeypatch.setenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, "3")
        monkeypatch.setenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, "30")
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(path)
        # Same probe line; values reflected.
        assert "[ServiceTool]" in caplog.text
        assert "service_tool_enabled=False" in caplog.text
        assert "max_concurrent=3" in caplog.text
        assert "reconcile_interval=30s" in caplog.text

    def test_yaml_overrides_visible_in_probe(self, tmp_path, caplog, monkeypatch):
        path = tmp_path / "config.yaml"
        path.write_text(
            "queue:\n  discard_on_startup: false\n"
            "services:\n"
            "  service_tool:\n"
            "    enabled: false\n"
            "    max_concurrent: 5\n"
            "  service_tool_reconcile_interval_seconds: 45\n"
        )
        # Wipe the env vars so the yaml wins.
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(path)
        assert "service_tool_enabled=False" in caplog.text
        assert "max_concurrent=5" in caplog.text
        assert "reconcile_interval=45s" in caplog.text


class TestKillSwitchOffFlowsIntoServicesConfig:
    """``ENSEMBLE_SERVICE_TOOL_ENABLED=0`` → field flows into ``ServicesConfig``."""

    @staticmethod
    def _write_minimal_config(tmp_path: Path) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text("queue:\n  discard_on_startup: false\n")
        return path

    def test_default_config_keeps_enabled_true(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        path = self._write_minimal_config(tmp_path)
        config = load_config(path)
        assert config.services.service_tool.enabled is True
        assert config.services.service_tool.max_concurrent == 10
        assert config.services.service_tool_reconcile_interval_seconds == 90

    def test_env_zero_sets_enabled_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENSEMBLE_SERVICE_TOOL_ENABLED, "0")
        path = self._write_minimal_config(tmp_path)
        config = load_config(path)
        # The flag the api.py lifespan gates on (it skips starting the
        # ServiceReconciliationService sweep when False).
        assert config.services.service_tool.enabled is False
        # Other knobs still take their normal precedence chain (untouched).
        assert config.services.service_tool.max_concurrent == 10
        assert config.services.service_tool_reconcile_interval_seconds == 90

    def test_yaml_false_sets_enabled_false(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_ENABLED, raising=False)
        path = tmp_path / "config.yaml"
        path.write_text(
            "queue:\n  discard_on_startup: false\n"
            "services:\n  service_tool:\n    enabled: false\n"
        )
        config = load_config(path)
        assert config.services.service_tool.enabled is False

    def test_install_writes_module_cache_for_runtime_reader(self):
        """``_install_service_tool_enabled(False)`` must flip the
        ``service_tool_enabled()`` reader — this is the field the api.py
        lifespan gates on to skip starting the sweep.
        """
        _install_service_tool_enabled(False)
        assert service_tool_enabled() is False
        _install_service_tool_enabled(True)
        assert service_tool_enabled() is True


class TestYamlSideMaxConcurrentAndReconcileInterval:
    """YAML-side precedence — the env-zero branch only changes ``enabled``."""

    def test_yaml_max_concurrent_units(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT, raising=False)
        path = tmp_path / "config.yaml"
        path.write_text(
            "queue:\n  discard_on_startup: false\n"
            "services:\n  service_tool:\n    max_concurrent: 7\n"
        )
        config = load_config(path)
        assert config.services.service_tool.max_concurrent == 7

    def test_yaml_reconcile_interval_units(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL, raising=False)
        path = tmp_path / "config.yaml"
        path.write_text(
            "queue:\n  discard_on_startup: false\n"
            "services:\n  service_tool_reconcile_interval_seconds: 45\n"
        )
        config = load_config(path)
        assert config.services.service_tool_reconcile_interval_seconds == 45
