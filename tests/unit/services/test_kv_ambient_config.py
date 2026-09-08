"""Config-side tests for the kv-ambient C2 kill-switch (Shape A).

Covers the ``ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED`` binding
landed in commit C2 of ``fix/kv-ambient-awareness``:

* ``ContextMessagesConfig`` field surface (default ON per
  decisions.md D8; env flip reaches a fresh settings instance;
  empty-string-safe permissive bool parsing sharing the
  ``_PROACTIVE_*_BOOLS`` vocabulary; unrecognized values raise).
* Pure resolver ``_resolve_kv_ambient_from_sources`` precedence:
  env (non-empty) > yaml > default True; blank env = unset.
* Boot-log wiring (S13 reviewer gate): the INFO line
  ``[ContextMessages] kv_ambient_system_default_enabled=%s (env …)``
  is emitted AT CONFIG-RESOLUTION TIME — during ``load_config`` —
  and the no-arg runtime accessor
  (``_resolve_kv_ambient_system_default_enabled``) NEVER logs per
  call. A lazy first-call emit would make quiet-daemon boot-log
  grep false-fail (phase3-plan Kill-Switch Design → Boot log).
* Resolved-once cache (restart-to-flip): ``load_config`` installs
  the effective bool; the accessor reads the cache and is silent;
  the cold-cache fallback resolves from env once (tests /
  programmatic boots); ``_reset_kv_ambient_for_tests`` restores cold.
* Registry wiring pins (B.S.8): the pydantic env binding resolves to
  the ``daemon/constants.py`` NAME constant (no literal fork), and
  ``ENSEMBLE_AMBIENT_KV_FRESH`` stays RESERVED-UNUSED until C3 (the
  literal must not appear outside ``daemon/constants.py``).

Run only this file::

    pytest tests/unit/services/test_kv_ambient_config.py -v
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import daemon.constants as constants
import daemon.config as config_module
from daemon.config import (
    ContextMessagesConfig,
    Config,
    _install_kv_ambient_system_default_enabled,
    _parse_kv_ambient_env_value,
    _reset_kv_ambient_for_tests,
    _resolve_kv_ambient_from_sources,
    _resolve_kv_ambient_system_default_enabled,
    load_config,
)

DAEMON_DIR = Path(config_module.__file__).resolve().parent

ENV_NAME = "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED"

BOOT_LOG_FRAGMENT = "[ContextMessages] kv_ambient_system_default_enabled="


@pytest.fixture
def cold_cache(monkeypatch: pytest.MonkeyPatch):
    """Cold module cache + clean env for the duration of one test.

    Every test here either resolves the cache or mutates the env; the
    teardown reset makes the module state deterministic for whichever
    test file runs next (the cache is process-global).
    """
    monkeypatch.delenv(ENV_NAME, raising=False)
    _reset_kv_ambient_for_tests()
    yield
    _reset_kv_ambient_for_tests()


def _write_yaml(
    tmp_path: Path,
    context_messages_block: str | None = None,
) -> str:
    """Minimal loadable config.yaml with an optional context_messages
    section (shape mirrored from test_compaction_model_config.py)."""
    text = """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"

persistence:
  db_path: "./data/instances.db"
"""
    if context_messages_block is not None:
        text += f"\ncontext_messages:\n{context_messages_block}\n"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return str(config_file)


# ─── ContextMessagesConfig field surface (Shape A) ───────────────────────────


class TestContextMessagesConfigField:
    """The pydantic field is the declarative binding (default ON)."""

    def test_default_is_true(self) -> None:
        """D8: the un-fixed behavior IS the bug — default ON."""
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is True

    def test_config_exposes_the_section(self) -> None:
        cfg = Config()
        assert hasattr(cfg, "context_messages")
        assert isinstance(cfg.context_messages, ContextMessagesConfig)

    def test_env_flip_reaches_fresh_settings_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Setting the env flips a FRESH settings instance — the binding
        is live, not decorative (restart-to-flip is the runtime cache's
        job, pinned separately below)."""
        monkeypatch.setenv(ENV_NAME, "0")
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is False
        monkeypatch.setenv(ENV_NAME, "1")
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is True

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
    def test_falsy_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv(ENV_NAME, raw)
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch, raw: str
    ) -> None:
        monkeypatch.setenv(ENV_NAME, raw)
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is True

    def test_empty_string_is_safe_default_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bare ``KEY=`` lines in ``.env`` reach pydantic as ``""`` —
        must normalize to the documented True default, never crash
        boot (W-1 precedent)."""
        monkeypatch.setenv(ENV_NAME, "")
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is True
        monkeypatch.setenv(ENV_NAME, "   ")
        assert ContextMessagesConfig().kv_ambient_system_default_enabled is True

    def test_unrecognized_value_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo must fail loud (pydantic type error), never silently
        resolve to a default."""
        monkeypatch.setenv(ENV_NAME, "purple")
        with pytest.raises(ValueError):
            ContextMessagesConfig()

    def test_env_name_derivation_matches_registry_constant(self) -> None:
        """B.S.8: no literal env-name fork — the validation alias uses
        the daemon/constants.py NAME constant, so prefix + field and
        the registry entry stay the same string."""
        assert ENV_NAME == (
            constants.ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED
        )
        assert (
            ContextMessagesConfig.model_fields[
                "kv_ambient_system_default_enabled"
            ].validation_alias
            is not None
        )


# ─── Pure resolver + parser ──────────────────────────────────────────────────


class TestParseKvAmbientEnvValue:
    def test_falsy(self) -> None:
        for raw in ("0", "false", "no", "off", " OFF ", "No"):
            assert _parse_kv_ambient_env_value(raw) is False

    def test_truthy(self) -> None:
        for raw in ("1", "true", "yes", "on", " ON ", "True"):
            assert _parse_kv_ambient_env_value(raw) is True

    def test_unknown_raises_with_flag_name(self) -> None:
        with pytest.raises(ValueError, match="ENSEMBLE_KV_AMBIENT"):
            _parse_kv_ambient_env_value("purple")

    def test_shares_proactive_bool_vocabulary(self) -> None:
        """Risk 7: one vocabulary, two consumers — the field validator
        and this parser cannot drift."""
        assert _parse_kv_ambient_env_value("off") is False
        assert _parse_kv_ambient_env_value("on") is True


class TestResolveKvAmbientFromSources:
    """env (non-empty) > yaml > default True; blank env = unset."""

    def test_env_wins_over_yaml(self) -> None:
        assert (
            _resolve_kv_ambient_from_sources(False, ens_value="1") is True
        )
        assert (
            _resolve_kv_ambient_from_sources(True, ens_value="0") is False
        )

    def test_yaml_bool_honored_when_env_unset(self) -> None:
        assert (
            _resolve_kv_ambient_from_sources(False, ens_value=None) is False
        )
        assert (
            _resolve_kv_ambient_from_sources(True, ens_value=None) is True
        )

    def test_yaml_string_parsed_when_env_unset(self) -> None:
        assert (
            _resolve_kv_ambient_from_sources("off", ens_value=None) is False
        )

    def test_blank_env_treated_as_unset(self) -> None:
        """Bare ``KEY=`` lines re-export as empty string — must fall
        through to yaml/default, never crash or pin False."""
        assert _resolve_kv_ambient_from_sources(True, ens_value="") is True
        assert _resolve_kv_ambient_from_sources("off", ens_value="  ") is False

    def test_default_true_when_both_unset(self) -> None:
        assert _resolve_kv_ambient_from_sources(None, ens_value=None) is True

    def test_empty_yaml_string_same_as_unset(self) -> None:
        assert _resolve_kv_ambient_from_sources("", ens_value=None) is True

    def test_unknown_env_value_raises(self) -> None:
        with pytest.raises(ValueError, match="purple"):
            _resolve_kv_ambient_from_sources(None, ens_value="purple")


# ─── Boot-log wiring (S13 reviewer gate) + resolved-once cache ───────────────


class TestBootLogWiring:
    """The INFO line is emitted at config-resolution time, NOT lazily."""

    def test_load_config_emits_boot_log_line(
        self, tmp_path, caplog, cold_cache
    ) -> None:
        """Default-flag boot: load_config emits exactly one INFO line
        naming the resolved state and the env var."""
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(config_path=_write_yaml(tmp_path))
        boot_lines = [
            rec.message
            for rec in caplog.records
            if BOOT_LOG_FRAGMENT in rec.message
        ]
        assert len(boot_lines) == 1, (
            "exactly ONE boot INFO line is expected per load_config"
        )
        assert "kv_ambient_system_default_enabled=True" in boot_lines[0]
        assert "(env ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED)" in boot_lines[0]
        # The boot install seeded the cache — the accessor is now warm.
        assert _resolve_kv_ambient_system_default_enabled() is True

    def test_boot_log_reflects_env_false(
        self, tmp_path, caplog, cold_cache, monkeypatch
    ) -> None:
        monkeypatch.setenv(ENV_NAME, "0")
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(config_path=_write_yaml(tmp_path))
        boot_lines = [
            rec.message
            for rec in caplog.records
            if BOOT_LOG_FRAGMENT in rec.message
        ]
        assert len(boot_lines) == 1
        assert "kv_ambient_system_default_enabled=False" in boot_lines[0]
        assert _resolve_kv_ambient_system_default_enabled() is False

    def test_boot_log_reflects_yaml_override(
        self, tmp_path, caplog, cold_cache
    ) -> None:
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(
                config_path=_write_yaml(
                    tmp_path,
                    context_messages_block=(
                        "  kv_ambient_system_default_enabled: false"
                    ),
                )
            )
        boot_lines = [
            rec.message
            for rec in caplog.records
            if BOOT_LOG_FRAGMENT in rec.message
        ]
        assert len(boot_lines) == 1
        assert "kv_ambient_system_default_enabled=False" in boot_lines[0]

    def test_accessor_never_logs_per_call(
        self, tmp_path, caplog, cold_cache
    ) -> None:
        """S13: the runtime accessor is SILENT — repeated calls after
        boot add zero log records (the boot line is load_config's job,
        never the per-call read's)."""
        with caplog.at_level(logging.INFO, logger="daemon.config"):
            load_config(config_path=_write_yaml(tmp_path))
        records_after_boot = list(caplog.records)
        for _ in range(3):
            assert _resolve_kv_ambient_system_default_enabled() is True
        new_records = [
            rec for rec in caplog.records if rec not in records_after_boot
        ]
        assert new_records == [], (
            "_resolve_kv_ambient_system_default_enabled must NEVER log — "
            "a lazy per-call emit would make quiet-daemon boot-log grep "
            "false-fail (S13)"
        )

    def test_invalid_env_value_fails_at_boot(
        self, tmp_path, cold_cache, monkeypatch
    ) -> None:
        """The load_config wire-up resolves BEFORE model construction so
        a typo raises with the flag-naming error at startup."""
        monkeypatch.setenv(ENV_NAME, "purple")
        with pytest.raises(ValueError, match="purple"):
            load_config(config_path=_write_yaml(tmp_path))


class TestResolvedOnceCache:
    """Restart-to-flip: resolved once, cached; cold fallback is env."""

    def test_cold_cache_resolves_env_once(
        self, monkeypatch, cold_cache
    ) -> None:
        """No load_config in this process (tests / programmatic boots):
        the accessor resolves from the env var directly — same
        vocabulary, silent — and caches the result."""
        monkeypatch.setenv(ENV_NAME, "0")
        assert _resolve_kv_ambient_system_default_enabled() is False
        # A mid-flight env flip is invisible: the value is cached.
        monkeypatch.setenv(ENV_NAME, "1")
        assert _resolve_kv_ambient_system_default_enabled() is False

    def test_cold_cache_unset_env_defaults_on(self, cold_cache) -> None:
        assert _resolve_kv_ambient_system_default_enabled() is True

    def test_install_wins_over_env(self, monkeypatch, cold_cache) -> None:
        """The boot install is authoritative once it ran — a stale env
        var left in the process cannot override the resolved value."""
        monkeypatch.setenv(ENV_NAME, "0")
        _install_kv_ambient_system_default_enabled(True)
        assert _resolve_kv_ambient_system_default_enabled() is True

    def test_reset_restores_cold(self, monkeypatch, cold_cache) -> None:
        monkeypatch.setenv(ENV_NAME, "0")
        assert _resolve_kv_ambient_system_default_enabled() is False
        _reset_kv_ambient_for_tests()
        monkeypatch.setenv(ENV_NAME, "1")
        # Cold again: re-resolves from the (now flipped) env.
        assert _resolve_kv_ambient_system_default_enabled() is True


# ─── Registry discipline (B.S.8) ─────────────────────────────────────────────


class TestRegistryBindingState:
    """C2 binds the host name; the C3 refresh name stays RESERVED."""

    def test_bound_name_consumed_via_constant_not_literal(self) -> None:
        """config.py reads the NAME from daemon/constants.py — the
        registry test fails on any literal fork (mirrors
        ``test_b_kill_switch_registry.py``'s wiring pins)."""
        source = Path(config_module.__file__).read_text(encoding="utf-8")
        assert "ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED" in source
        # The literal appears exactly in the import list — every other
        # use site goes through the imported NAME.
        assert source.count('"ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED"') == 0, (
            "config.py must import the NAME constant, not re-quote the "
            "literal env string (single-homed registry discipline)"
        )

    def test_c3_refresh_name_stays_reserved_unused(self) -> None:
        """``ENSEMBLE_AMBIENT_KV_FRESH`` binds at C3 (Shape B). Until
        then the literal must appear ONLY in daemon/constants.py — no
        config field, no resolver, no read site."""
        needle = "ENSEMBLE_AMBIENT_KV_FRESH"
        hits: list[Path] = []
        for path in sorted(DAEMON_DIR.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:  # pragma: no cover — unreadable file
                continue
            if needle in text:
                hits.append(path)
        assert hits == [DAEMON_DIR / "constants.py"], (
            "the C3 refresh name is RESERVED-UNUSED at C2 — it must be "
            f"declared ONLY in daemon/constants.py; found {hits}"
        )
        cfg = Config()
        assert not hasattr(cfg.context_messages, "ambient_kv_fresh"), (
            "no C3 field may exist before the C3 binding lands"
        )
