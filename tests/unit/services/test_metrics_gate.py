"""P0 T3 — the metrics kill-switch gate (plan §5 / catalog §0.8).

Pins the ONE rule, three cases:

    (i)   env EXPLICITLY SET ⇒ truthy set ``{1, true, yes}``
          (case-insensitive) ⇒ ON; EVERY other explicit value ⇒ OFF
          (an unrecognised explicit value FAILS CLOSED);
    (ii)  explicit OFF ⇒ OFF and it WINS over the managed-key chain;
    (iii) env UNSET ⇒ the managed-key chain decides.

Plus the ONE boot log line (env unset + managed key on), the
per-request env read (no TTL cache — the switch flips WITHOUT a
restart), and the managed-tier refresh contract.
"""

from __future__ import annotations

import logging

import pytest

from daemon.services import metrics_gate


@pytest.fixture(autouse=True)
def _clean_gate(monkeypatch):
    """Every test starts from a pristine gate + an UNSET env."""
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.reset_for_tests()
    yield
    metrics_gate.reset_for_tests()


# ─── case (i): env explicitly set ──────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    ["1", "true", "TRUE", "True", "yes", "YES", "Yes", "yEs"],
)
def test_explicit_truthy_values_are_on(monkeypatch, raw):
    monkeypatch.setenv(metrics_gate.ENV_VAR, raw)
    assert metrics_gate.env_explicit_enabled() is True
    assert metrics_gate.metrics_enabled() is True


@pytest.mark.parametrize(
    "raw",
    [
        "ture",  # the classic typo
        "on",
        "enabled",
        "0",
        "false",
        "no",
        "",
        "2",
        "true-ish",
        "  true  ",
        "y",
    ],
)
def test_explicit_non_truthy_values_fail_closed(monkeypatch, raw):
    """An UNRECOGNISED explicit value ⇒ OFF (fail closed)."""
    monkeypatch.setenv(metrics_gate.ENV_VAR, raw)
    assert metrics_gate.env_explicit_enabled() is False
    assert metrics_gate.metrics_enabled() is False


def test_set_but_empty_is_explicit_not_unset(monkeypatch):
    """Set-but-empty is an EXPLICIT value ⇒ OFF, NOT the unset case."""
    monkeypatch.setenv(metrics_gate.ENV_VAR, "")
    assert metrics_gate.env_raw() == ""
    assert metrics_gate.env_explicit_enabled() is False


def test_unset_env_is_none(monkeypatch):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    assert metrics_gate.env_raw() is None
    assert metrics_gate.env_explicit_enabled() is None


# ─── case (ii): explicit OFF wins over the managed chain ───────────────────


def test_explicit_off_wins_over_managed_on(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    metrics_gate.set_managed_metrics_enabled(True)
    assert metrics_gate.metrics_enabled() is False


def test_explicit_off_wins_over_managed_on_every_off_spelling(monkeypatch):
    for raw in ("0", "false", "no", "ture", "on", "enabled", ""):
        monkeypatch.setenv(metrics_gate.ENV_VAR, raw)
        metrics_gate.set_managed_metrics_enabled(True)
        assert metrics_gate.metrics_enabled() is False, raw


# ─── case (iii): env unset ⇒ the managed-key chain decides ─────────────────


def test_unset_plus_managed_on_is_on(monkeypatch):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(True)
    assert metrics_gate.metrics_enabled() is True


def test_unset_plus_default_is_off(monkeypatch):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    # default managed value = False (the registry default)
    assert metrics_gate.managed_metrics_enabled() is False
    assert metrics_gate.metrics_enabled() is False


def test_explicit_on_overrides_managed_default(monkeypatch):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    metrics_gate.set_managed_metrics_enabled(False)
    assert metrics_gate.metrics_enabled() is True


# ─── per-request read: the switch flips WITHOUT a restart ──────────────────


def test_switch_flips_without_restart(monkeypatch):
    """The same process flips ON → OFF → ON with no restart and no
    cached state (the env is read per call)."""
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(False)
    assert metrics_gate.metrics_enabled() is False

    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    assert metrics_gate.metrics_enabled() is True

    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    assert metrics_gate.metrics_enabled() is False

    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    assert metrics_gate.metrics_enabled() is False


def test_env_read_is_uncached(monkeypatch):
    """Two reads with a changed env in between ⇒ two different answers
    (no TTL cache — the arch round rejected one)."""
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    assert metrics_gate.metrics_enabled() is True
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    assert metrics_gate.metrics_enabled() is False


# ─── the ONE boot log line ─────────────────────────────────────────────────


def test_deferral_log_fires_only_when_env_unset_and_managed_on(
    monkeypatch, caplog
):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(True)
    with caplog.at_level(logging.INFO, logger=metrics_gate.logger.name):
        assert metrics_gate.log_deferral_once() is True
    assert metrics_gate.DEFER_LOG_MESSAGE in caplog.text
    assert "ENSEMBLE_METRICS_ENABLED` unset; deferring to" in caplog.text


def test_deferral_log_is_emitted_exactly_once(monkeypatch, caplog):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(True)
    with caplog.at_level(logging.INFO, logger=metrics_gate.logger.name):
        assert metrics_gate.log_deferral_once() is True
        assert metrics_gate.log_deferral_once() is False
    assert caplog.text.count(metrics_gate.DEFER_LOG_MESSAGE) == 1


def test_deferral_log_absent_when_env_set(monkeypatch, caplog):
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    metrics_gate.set_managed_metrics_enabled(True)
    with caplog.at_level(logging.INFO, logger=metrics_gate.logger.name):
        assert metrics_gate.log_deferral_once() is False
    assert metrics_gate.DEFER_LOG_MESSAGE not in caplog.text


def test_deferral_log_absent_when_managed_default(monkeypatch, caplog):
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(False)
    with caplog.at_level(logging.INFO, logger=metrics_gate.logger.name):
        assert metrics_gate.log_deferral_once() is False
    assert metrics_gate.DEFER_LOG_MESSAGE not in caplog.text


# ─── the managed tier: provider + TTL refresh ──────────────────────────────


def test_managed_provider_is_used_and_cached_within_ttl(monkeypatch):
    calls: list[int] = []

    def provider() -> bool:
        calls.append(1)
        return True

    metrics_gate.set_managed_metrics_provider(provider)
    assert metrics_gate.refresh_managed_metrics_enabled(now=100.0) is True
    # within the TTL ⇒ the cached value, provider NOT called again
    assert metrics_gate.refresh_managed_metrics_enabled(now=101.0) is True
    assert len(calls) == 1
    # past the TTL ⇒ refreshed
    assert metrics_gate.refresh_managed_metrics_enabled(now=200.0) is True
    assert len(calls) == 2


def test_managed_provider_failure_keeps_last_known_value(monkeypatch):
    metrics_gate.set_managed_metrics_enabled(True)

    def boom() -> bool:
        raise RuntimeError("db down")

    metrics_gate.set_managed_metrics_provider(boom)
    assert metrics_gate.refresh_managed_metrics_enabled(now=100.0) is True
    assert metrics_gate.managed_metrics_enabled() is True


def test_managed_refresh_is_a_pure_read_for_consumers(monkeypatch):
    """``metrics_enabled`` NEVER touches the provider (the census sampler
    depends on that — it must not consume a pool slot)."""
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    metrics_gate.set_managed_metrics_enabled(True)

    def boom() -> bool:
        raise AssertionError("the gate must not call the provider")

    metrics_gate.set_managed_metrics_provider(boom)
    assert metrics_gate.metrics_enabled() is True
