"""Unit tests for the canonical leader completion attestation resolver.

Phase 4 of the leader completion attestation feature, task 4.1 / 4.2.
Covers ``daemon/services/attestation_resolver.py``:

* tri-state mode env resolution (``off`` / ``dry`` / ``enforce``);
* blank / unset / typo handling — Pattern C FAIL-OPEN to ``dry`` (ruling 4);
* integer-knob parsing (``WINDOW`` / ``DENY_BOUND``) — fail-OPEN;
* restart-read semantics — cached global wins over mid-flight env mutation;
* the O1 boot-assert WARN line on ``WINDOW > MIN_RECENT_WINDOW``;
* the one-shot boot-log emission (idempotent + dual-line shape on WARN);
* promotion-metric counter contract (canonical names per task 4.6).
"""
from __future__ import annotations

import logging

import pytest

from daemon.services import attestation_resolver as resolver_module
from daemon.services.attestation_resolver import (
    DEFAULT_DENY_BOUND,
    DEFAULT_MODE,
    DEFAULT_WINDOW,
    ENSEMBLE_ATTESTATION_DENY_BOUND_ENV,
    ENSEMBLE_ATTESTATION_MODE_ENV,
    ENSEMBLE_ATTESTATION_WINDOW_ENV,
    METRIC_DRY_LOG_DENY_PREDICATE_TOTAL,
    METRIC_DRY_LOG_TOTAL,
    METRIC_ENFORCE_DENIED_TOTAL,
    AttestationConfig,
    emit_attestation_boot_log,
    get_config,
    get_promotion_metrics,
    record_promotion_metric,
    reset_attestation_resolver_for_tests,
)


@pytest.fixture(autouse=True)
def _resolver_reset():
    """Clear the cached resolver + boot-log + metric state between tests.

    Mirrors the WC-wake resolver's ``_reset_wc_wake_enqueue_for_tests``
    discipline — every resolver state slot is per-process, and tests
    that mutate env must reset to avoid leaking into other tests.
    """
    reset_attestation_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()


@pytest.fixture
def clean_env(monkeypatch):
    """Wipe the three env vars so tests start from a known blank slate."""
    monkeypatch.delenv(ENSEMBLE_ATTESTATION_MODE_ENV, raising=False)
    monkeypatch.delenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, raising=False)
    monkeypatch.delenv(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, raising=False)


# =============================================================================
# Defaults + tri-state mode parsing (task 4.1)
# =============================================================================


class TestTriStateMode:
    def test_unset_returns_default_dry(self, clean_env):
        config = get_config()
        assert config.mode == DEFAULT_MODE == "dry"
        assert config.window == DEFAULT_WINDOW == 3
        assert config.deny_bound == DEFAULT_DENY_BOUND == 3
        assert config.attestation_enabled is True  # mode != "off"
        assert config.scope_applicable is True

    def test_explicit_off(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "off")
        config = get_config()
        assert config.mode == "off"
        assert config.attestation_enabled is False

    def test_explicit_dry(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "dry")
        config = get_config()
        assert config.mode == "dry"
        assert config.attestation_enabled is True

    def test_explicit_enforce(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforce")
        config = get_config()
        assert config.mode == "enforce"
        assert config.attestation_enabled is True

    def test_blank_value_falls_back_to_default_dry(self, monkeypatch, clean_env):
        """Blank value resolves to default — NOT to ``off`` (the WC-wake
        resolver precedent resolves blank to its default; ruling 4 keeps
        the contract for attestation)."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "")
        config = get_config()
        assert config.mode == "dry"

    def test_whitespace_only_falls_back_to_default_dry(
        self, monkeypatch, clean_env
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "   ")
        config = get_config()
        assert config.mode == "dry"

    def test_uppercase_normalized_to_lowercase(
        self, monkeypatch, clean_env
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "ENFORCE")
        config = get_config()
        assert config.mode == "enforce"


class TestInvalidModeFailsOpen:
    """Ruling 4 — Pattern C fail-OPEN with one-shot WARN."""

    def test_typo_falls_back_to_dry_with_warn(
        self, monkeypatch, clean_env, caplog
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforse")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            config = get_config()
        assert config.mode == "dry"
        assert "is not a recognized mode" in caplog.text
        assert "off|dry|enforce" in caplog.text

    def test_single_bool_value_falls_back_to_dry_with_warn(
        self, monkeypatch, clean_env, caplog
    ):
        """The legacy single-bool value (e.g. ``enabled=true`` or ``1``)
        fails OPEN — AC-7.9 (corrected to fail-OPEN per ruling 4)."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enabled")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            config = get_config()
        assert config.mode == "dry"
        assert "is not a recognized mode" in caplog.text

    def test_warn_is_one_shot_per_process(
        self, monkeypatch, clean_env, caplog
    ):
        """Re-resolving under the same invalid value emits the WARN exactly
        once (idempotence for cached-global Pattern C)."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforse")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            get_config()  # first call: WARN emitted
            get_config()  # second call: cache hit, NO additional WARN
        # The resolver WARN appears once (boot-log WARN is a different
        # line — emitted only via emit_attestation_boot_log).
        warn_count = sum(
            1
            for record in caplog.records
            if "is not a recognized mode" in record.message
        )
        assert warn_count == 1


# =============================================================================
# Integer knob parsing — fail-OPEN on invalid value
# =============================================================================


class TestWindowParsing:
    def test_valid(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "5")
        config = get_config()
        assert config.window == 5

    def test_zero_fails_open_to_default(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "0")
        config = get_config()
        assert config.window == DEFAULT_WINDOW == 3

    def test_negative_fails_open_to_default(
        self, monkeypatch, clean_env, caplog
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "-2")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            config = get_config()
        assert config.window == 3
        assert "is < 1" in caplog.text

    def test_non_integer_fails_open_with_warn(
        self, monkeypatch, clean_env, caplog
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "three")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            config = get_config()
        assert config.window == 3
        assert "is not a valid integer" in caplog.text


class TestDenyBoundParsing:
    def test_valid(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, "10")
        config = get_config()
        assert config.deny_bound == 10

    def test_zero_fails_open_to_default(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, "0")
        config = get_config()
        assert config.deny_bound == DEFAULT_DENY_BOUND == 3

    def test_non_integer_fails_open_with_warn(
        self, monkeypatch, clean_env, caplog
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_DENY_BOUND_ENV, "five")
        with caplog.at_level(
            logging.WARNING, logger="daemon.services.attestation_resolver"
        ):
            config = get_config()
        assert config.deny_bound == 3
        assert "is not a valid integer" in caplog.text


# =============================================================================
# Restart-read semantics — Pattern C cached global
# =============================================================================


class TestRestartReadSemantics:
    def test_env_mutation_after_resolve_does_not_change_cache(
        self, monkeypatch, clean_env
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforce")
        first = get_config()
        assert first.mode == "enforce"
        # Mid-flight flip has no effect — restart required.
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "off")
        second = get_config()
        assert second.mode == "enforce"  # cache wins
        assert first is second  # identity — same cached object

    def test_cache_reset_re_reads_env(self, monkeypatch, clean_env):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforce")
        first = get_config()
        assert first.mode == "enforce"
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "off")
        # Test-only reset simulates a process restart.
        reset_attestation_resolver_for_tests()
        second = get_config()
        assert second.mode == "off"


# =============================================================================
# AttestationConfig dataclass shape
# =============================================================================


class TestConfigDataclass:
    def test_dataclass_is_frozen(self, monkeypatch, clean_env):
        config = get_config()
        with pytest.raises((AttributeError, Exception)):
            config.mode = "off"  # type: ignore[misc]

    def test_attestation_enabled_derives_from_mode(
        self, monkeypatch, clean_env
    ):
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "off")
        assert get_config().attestation_enabled is False
        reset_attestation_resolver_for_tests()
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "dry")
        assert get_config().attestation_enabled is True
        reset_attestation_resolver_for_tests()
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforce")
        assert get_config().attestation_enabled is True


# =============================================================================
# Boot log + O1 assert (task 4.2)
# =============================================================================


class TestBootLog:
    def test_default_config_emits_boot_log_line(
        self, clean_env, caplog
    ):
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        # Single-shot INFO line with the resolved values.
        info_lines = [
            r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ]
        assert any(
            "Leader completion attestation resolved" in msg for msg in info_lines
        )
        assert any("mode=dry" in msg for msg in info_lines)
        assert any("window=3" in msg for msg in info_lines)
        assert any("deny_bound=3" in msg for msg in info_lines)
        assert any("N_le_min_recent_window=PASS" in msg for msg in info_lines)
        assert any("Restart required to flip" in msg for msg in info_lines)

    def test_boot_log_emitted_exactly_once_per_process(
        self, clean_env, caplog
    ):
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
            emit_attestation_boot_log()
            emit_attestation_boot_log()
        info_count = sum(
            1
            for r in caplog.records
            if "Leader completion attestation resolved" in r.message
        )
        assert info_count == 1

    def test_o1_warn_fires_when_window_above_floor(
        self, monkeypatch, clean_env, caplog
    ):
        """O1 boot assert — ``WINDOW > MIN_RECENT_WINDOW`` emits the WARN."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "5")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        # The main INFO line carries the WARN marker.
        assert any(
            "N_le_min_recent_window=WARN" in r.message
            for r in caplog.records
        )
        # The separate WARN line carries the operator-visible detail.
        warn_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "attestation_denied_count risk" in r.message
            for r in warn_records
        )

    def test_o1_pass_when_window_equals_floor(
        self, monkeypatch, clean_env, caplog
    ):
        """Default ``WINDOW=3`` matches ``MIN_RECENT_WINDOW=3`` — PASS."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "3")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        assert any(
            "N_le_min_recent_window=PASS" in r.message
            for r in caplog.records
        )
        assert not any(
            r.levelno == logging.WARNING for r in caplog.records
        )

    def test_o1_pass_when_window_below_floor(
        self, monkeypatch, clean_env, caplog
    ):
        """``WINDOW=2 < MIN_RECENT_WINDOW=3`` — PASS (assert is one-sided)."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_WINDOW_ENV, "2")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        assert any(
            "N_le_min_recent_window=PASS" in r.message
            for r in caplog.records
        )
        assert not any(
            r.levelno == logging.WARNING for r in caplog.records
        )

    def test_boot_log_carries_env_unset_marker(
        self, clean_env, caplog
    ):
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        # The INFO line carries "<unset>" for unset env vars (WC-wake
        # boot log precedent — operators grep for "<unset>" to confirm
        # the env was not set).
        assert any(
            "<unset>" in r.message for r in caplog.records
        )

    def test_invalid_mode_emits_warn_in_boot_log(
        self, monkeypatch, clean_env, caplog
    ):
        """Invalid mode → boot log carries mode=dry + the resolver WARN."""
        monkeypatch.setenv(ENSEMBLE_ATTESTATION_MODE_ENV, "enforse")
        with caplog.at_level(
            logging.INFO, logger="daemon.services.attestation_resolver"
        ):
            emit_attestation_boot_log()
        # Main INFO line — fail-OPEN to dry (gate still works).
        assert any("mode=dry" in r.message for r in caplog.records)
        # Separate WARN line — operator-visible typo signal.
        warn_lines = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("is not a recognized mode" in m for m in warn_lines)


# =============================================================================
# Promotion metrics (task 4.6 — canonical names)
# =============================================================================


class TestPromotionMetrics:
    def test_initial_state_is_zero(self):
        snapshot = get_promotion_metrics()
        assert snapshot == {
            "dry_log_total": 0,
            "dry_log_deny_predicate_total": 0,
            "enforce_denied_total": 0,
        }

    def test_canonical_names_exist(self):
        # The Phase 5 runbook drift test pins these names — the resolver
        # owns them as a contract.
        assert METRIC_DRY_LOG_TOTAL == "dry_log_total"
        assert METRIC_DRY_LOG_DENY_PREDICATE_TOTAL == (
            "dry_log_deny_predicate_total"
        )
        assert METRIC_ENFORCE_DENIED_TOTAL == "enforce_denied_total"

    def test_increment_dry_log_total(self):
        value = record_promotion_metric(METRIC_DRY_LOG_TOTAL)
        assert value == 1
        assert get_promotion_metrics()["dry_log_total"] == 1

    def test_increment_dry_log_deny_predicate(self):
        record_promotion_metric(METRIC_DRY_LOG_DENY_PREDICATE_TOTAL)
        record_promotion_metric(METRIC_DRY_LOG_DENY_PREDICATE_TOTAL)
        assert get_promotion_metrics()["dry_log_deny_predicate_total"] == 2

    def test_increment_enforce_denied_total(self):
        record_promotion_metric(METRIC_ENFORCE_DENIED_TOTAL)
        assert get_promotion_metrics()["enforce_denied_total"] == 1

    def test_unknown_metric_name_raises(self):
        with pytest.raises(ValueError, match="unknown promotion metric"):
            record_promotion_metric("not_a_real_metric")  # type: ignore[arg-type]

    def test_reset_clears_metric_counters(self):
        record_promotion_metric(METRIC_DRY_LOG_TOTAL)
        record_promotion_metric(METRIC_DRY_LOG_DENY_PREDICATE_TOTAL)
        record_promotion_metric(METRIC_ENFORCE_DENIED_TOTAL)
        snapshot = get_promotion_metrics()
        assert snapshot["dry_log_total"] == 1
        assert snapshot["dry_log_deny_predicate_total"] == 1
        assert snapshot["enforce_denied_total"] == 1
        reset_attestation_resolver_for_tests()
        # Counters back to zero (test-only reset).
        assert get_promotion_metrics() == {
            "dry_log_total": 0,
            "dry_log_deny_predicate_total": 0,
            "enforce_denied_total": 0,
        }


# =============================================================================
# Round-trip — the resolver module is import-clean (no circular deps)
# =============================================================================


def test_module_imports_cleanly():
    # Smoke check — re-importing the module never raises (the canonical
    # home must not have a cycle through services.__init__). We don't
    # assert isinstance against the pre-reload class object because
    # ``importlib.reload`` rebinds the class in the module's namespace,
    # leaving the original import alias stale.
    import importlib

    importlib.reload(resolver_module)
    config = resolver_module.get_config()
    assert config.mode in {"off", "dry", "enforce"}
    assert isinstance(config.window, int)
    assert isinstance(config.deny_bound, int)