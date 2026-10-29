"""FIX ROUND 1 / MAJOR-2 — the LIVE managed values (plan §5 restart column).

``limits.metrics_scrape_interval_hint_s`` and
``limits.metrics_cardinality_cap`` are registered ``requires_restart=False``
(plan §5: "No (per-request)"; plan §13's back-out relies on "raise the
cap … no restart"). Their registry providers used to read the
BOOT-APPLIED config object, so a runtime
``PUT /api/settings/daemon/{key}`` (which writes only the DB row) left
the provider serving the boot value while the settings API told the
operator "no restart needed" — the asymmetry the review flagged.

These legs pin the fix:

* a runtime change of EITHER key (the resolver's DB-tier return) is
  reflected by the registry's provider WITHOUT a restart, within the
  TTL;
* a key with NO DB override falls back to its BOOT value;
* the kill-switch's own behavior is UNCHANGED (the three cases);
* the write-time bounds are still validated (an out-of-bounds value is
  refused by the registry validator — the "raise the cap" back-out
  cannot push the cap past its ceiling).
"""

from __future__ import annotations

import pytest

from daemon.services import metrics_gate
from daemon.services.settings_service import (
    MANAGED_KEYS,
    parse_and_validate,
)

BOOT_CADENCE = 15
BOOT_CAP = 2000


def _install(resolver_values: dict) -> tuple:
    """Install the live resolver + the two registry providers."""
    provider = metrics_gate.build_managed_values_provider(
        lambda: dict(resolver_values)
    )
    metrics_gate.set_managed_metrics_provider(provider)
    cadence, cap = metrics_gate.build_registry_providers(
        scrape_interval_default=BOOT_CADENCE,
        cardinality_cap_default=BOOT_CAP,
    )
    return cadence, cap


def _refresh(now: float) -> None:
    """Force the TTL-bounded refresh (the listener's thread does this)."""
    metrics_gate.refresh_managed_values(now=now, ttl_s=0.0)


# ─── the two live keys ────────────────────────────────────────────────────


def test_the_cap_reflects_a_runtime_change_without_a_restart():
    cadence, cap = _install({})
    assert cap() == BOOT_CAP  # the boot fallback (no DB override)

    # The operator raises the cap through the settings API ⇒ the DB row
    # appears. The registry's provider must see it — no restart.
    _install({"limits.metrics_cardinality_cap": 5000})
    _refresh(1000.0)
    assert cap() == 5000


def test_the_cadence_reflects_a_runtime_change_without_a_restart():
    cadence, cap = _install({})
    assert cadence() == BOOT_CADENCE

    _install({"limits.metrics_scrape_interval_hint_s": 42})
    _refresh(1000.0)
    assert cadence() == 42.0


def test_a_runtime_change_is_reflected_within_the_ttl():
    """The refresh is TTL-bounded: the value lands once the TTL expires."""
    cadence, cap = _install({})
    _refresh(1000.0)
    _install({"limits.metrics_cardinality_cap": 5000})
    # Within the TTL ⇒ the previous value is still served.
    metrics_gate.refresh_managed_values(now=1001.0, ttl_s=5.0)
    assert cap() == BOOT_CAP
    # Past the TTL ⇒ the new value lands.
    metrics_gate.refresh_managed_values(now=1006.0, ttl_s=5.0)
    assert cap() == 5000


def test_the_boot_value_is_the_fallback_when_the_chain_has_no_override():
    cadence, cap = _install({"limits.metrics_enabled": True})
    _refresh(1000.0)
    # No row for the two live keys ⇒ the boot values stay in force.
    assert cap() == BOOT_CAP
    assert cadence() == float(BOOT_CADENCE)


def test_one_read_serves_every_live_key():
    """ONE DB read per TTL serves the kill-switch AND the two live
    keys — the resolver is not a per-key subsystem."""
    reads: list[int] = []

    def _read() -> dict:
        reads.append(1)
        return {
            "limits.metrics_enabled": True,
            "limits.metrics_cardinality_cap": 5000,
            "limits.metrics_scrape_interval_hint_s": 30,
        }

    metrics_gate.set_managed_metrics_provider(
        metrics_gate.build_managed_values_provider(_read)
    )
    cadence, cap = metrics_gate.build_registry_providers(
        scrape_interval_default=BOOT_CADENCE,
        cardinality_cap_default=BOOT_CAP,
    )
    _refresh(1000.0)
    assert len(reads) == 1
    assert cap() == 5000
    assert cadence() == 30.0
    assert metrics_gate.metrics_enabled() is True
    assert len(reads) == 1  # still ONE read


def test_a_resolver_failure_keeps_the_last_known_values():
    _install({"limits.metrics_cardinality_cap": 5000})
    _refresh(1000.0)

    def _boom() -> dict:
        raise RuntimeError("db down")

    metrics_gate.set_managed_metrics_provider(
        metrics_gate.build_managed_values_provider(_boom)
    )
    _refresh(2000.0)
    # The last known value is kept — never a crash, never a reset to 0.
    assert metrics_gate.live_managed_value(
        "limits.metrics_cardinality_cap", BOOT_CAP
    ) == 5000


def test_the_provider_read_is_pure_no_io():
    """The registry consults its providers under a metric's own lock —
    the provider must never do I/O."""
    def _boom() -> dict:
        raise AssertionError("the provider must not read the DB")

    metrics_gate.set_managed_metrics_provider(
        metrics_gate.build_managed_values_provider(_boom)
    )
    cadence, cap = metrics_gate.build_registry_providers(
        scrape_interval_default=BOOT_CADENCE,
        cardinality_cap_default=BOOT_CAP,
    )
    assert cap() == BOOT_CAP
    assert cadence() == float(BOOT_CADENCE)


# ─── the kill-switch's behavior is unchanged ──────────────────────────────


def test_the_gate_key_behavior_is_unchanged(monkeypatch):
    """The three cases still hold after the generalization."""
    _install({"limits.metrics_enabled": True})
    _refresh(1000.0)
    # (i) env explicitly set ⇒ the env decides.
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    assert metrics_gate.metrics_enabled() is False
    monkeypatch.setenv(metrics_gate.ENV_VAR, "1")
    assert metrics_gate.metrics_enabled() is True
    # (ii) explicit OFF wins over the managed chain.
    monkeypatch.setenv(metrics_gate.ENV_VAR, "0")
    assert metrics_gate.metrics_enabled() is False
    # (iii) env unset ⇒ the managed chain decides.
    monkeypatch.delenv(metrics_gate.ENV_VAR, raising=False)
    assert metrics_gate.metrics_enabled() is True


def test_the_gate_key_falls_back_to_the_boot_value():
    """No DB override for the kill-switch ⇒ the BOOT fallback applies."""
    metrics_gate.set_managed_metrics_enabled(True)
    _install({})
    _refresh(1000.0)
    assert metrics_gate.metrics_enabled() is True


# ─── the write-time bounds are still validated ───────────────────────────


@pytest.mark.parametrize(
    "key,value",
    [
        ("limits.metrics_cardinality_cap", 99),
        ("limits.metrics_cardinality_cap", 10001),
        ("limits.metrics_scrape_interval_hint_s", 4),
        ("limits.metrics_scrape_interval_hint_s", 301),
    ],
)
def test_out_of_bounds_values_are_still_refused(key, value):
    """The "raise the cap … no restart" back-out cannot push a value
    past its ceiling — the registry validator is still the one truth."""
    ok, _parsed, failure_class, _detail = parse_and_validate(
        MANAGED_KEYS[key], value
    )
    assert ok is False
    assert failure_class == "validation"


def test_the_two_live_keys_are_still_marked_no_restart():
    """The claim the settings API makes must be TRUE — both keys stay
    ``requires_restart=False``."""
    assert MANAGED_KEYS["limits.metrics_cardinality_cap"].requires_restart is False
    assert (
        MANAGED_KEYS["limits.metrics_scrape_interval_hint_s"].requires_restart
        is False
    )


def test_the_registry_defaults_are_still_mirrored_by_the_pydantic_fields():
    from daemon.config import LimitsConfig

    assert (
        MANAGED_KEYS["limits.metrics_cardinality_cap"].default
        == LimitsConfig.model_fields["metrics_cardinality_cap"].default
    )
    assert (
        MANAGED_KEYS["limits.metrics_scrape_interval_hint_s"].default
        == LimitsConfig.model_fields["metrics_scrape_interval_hint_s"].default
    )
