"""Empty-response-guard Phase 1 — knob resolvers + module install (item 5).

Covers the ``_resolve_*`` contract from load_config:

* ``ENSEMBLE_EMPTY_RESPONSE_GUARD`` — default ON, kill-switch.
* ``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP`` — default OFF (guard ACTIVE
  on compaction summaries — leader decision).
* Invalid values → ``ValueError`` (boot fails loud, kill-switch
  convention).
* ``LimitsConfig.empty_degenerate_reinvoke_cap`` — default 3, ge=1.

The resolver lives in ``daemon.config``; the installed runtime state
lives in ``daemon.response_validation`` (mirrors the
``_install_vscode_webview_csp_fix`` pattern). ``load_config`` itself is
exercised end-to-end in the install test below via monkeypatched env.
"""

from pathlib import Path

import pytest

import daemon.config as config_module
from daemon.config import (
    LimitsConfig,
    _resolve_empty_guard_compaction_skip,
    _resolve_empty_response_guard_enabled,
)
from daemon.response_validation import (
    get_empty_guard_compaction_skip,
    get_empty_response_guard_enabled,
)
from tests.unit.empty_guard_test_helpers import _restore_empty_guard_defaults  # noqa: F401  (pytest fixture, import-collected)


class TestResolveEmptyResponseGuardEnabled:
    def test_unset_defaults_on(self):
        assert _resolve_empty_response_guard_enabled(None) is True

    def test_empty_string_normalizes_to_default(self):
        # Bare ``KEY=`` in .env (re-exported empty) must not crash boot.
        assert _resolve_empty_response_guard_enabled("") is True
        assert _resolve_empty_response_guard_enabled("   ") is True

    def test_false_vocabulary(self):
        for raw in ("0", "false", "no", "off", "False", " OFF "):
            assert _resolve_empty_response_guard_enabled(raw) is False, raw

    def test_true_vocabulary(self):
        for raw in ("1", "true", "yes", "on", "TRUE", " on "):
            assert _resolve_empty_response_guard_enabled(raw) is True, raw

    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError, match="ENSEMBLE_EMPTY_RESPONSE_GUARD"):
            _resolve_empty_response_guard_enabled("banana")


class TestResolveEmptyGuardCompactionSkip:
    def test_unset_defaults_off(self):
        assert _resolve_empty_guard_compaction_skip(None) is False

    def test_empty_string_normalizes_to_default(self):
        assert _resolve_empty_guard_compaction_skip("") is False

    def test_on_value(self):
        assert _resolve_empty_guard_compaction_skip("1") is True
        assert _resolve_empty_guard_compaction_skip("on") is True

    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError, match="ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP"):
            _resolve_empty_guard_compaction_skip("maybe")


class TestLimitsDegenerateCap:
    def test_default_is_3(self):
        assert LimitsConfig().empty_degenerate_reinvoke_cap == 3

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LIMITS_EMPTY_DEGENERATE_REINVOKE_CAP", "5")
        assert LimitsConfig().empty_degenerate_reinvoke_cap == 5

    def test_zero_rejected(self):
        with pytest.raises(ValueError, match="empty_degenerate_reinvoke_cap"):
            LimitsConfig(empty_degenerate_reinvoke_cap=0)


class TestLoadConfigInstallsGuardFlags:
    def test_load_config_installs_documented_defaults(self, monkeypatch, tmp_path):
        monkeypatch.delenv("ENSEMBLE_EMPTY_RESPONSE_GUARD", raising=False)
        monkeypatch.delenv("ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP", raising=False)
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(self._write_config(tmp_path)))
        config_module.load_config()
        assert get_empty_response_guard_enabled() is True
        assert get_empty_guard_compaction_skip() is False

    def test_load_config_honors_env_overrides(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ENSEMBLE_EMPTY_RESPONSE_GUARD", "0")
        monkeypatch.setenv("ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP", "1")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(self._write_config(tmp_path)))
        config_module.load_config()
        assert get_empty_response_guard_enabled() is False
        assert get_empty_guard_compaction_skip() is True

    def test_load_config_invalid_env_fails_boot_loud(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ENSEMBLE_EMPTY_RESPONSE_GUARD", "banana")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(self._write_config(tmp_path)))
        with pytest.raises(ValueError, match="ENSEMBLE_EMPTY_RESPONSE_GUARD"):
            config_module.load_config()

    @staticmethod
    def _write_config(tmp_path) -> Path:
        """Minimal valid config.yaml (the sections load_config requires)."""
        import yaml

        cfg = {
            "openai": {"api_key": "sk-test", "model": "gpt-4o-mini"},
            "server": {"host": "127.0.0.1", "port": 8079},
            "limits": {"empty_degenerate_reinvoke_cap": 3},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path
