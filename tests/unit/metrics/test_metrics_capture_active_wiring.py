"""A1 — the ``capture_active`` WIRING pack (registered pack
``metrics_capture_active_wiring_unit_test``).

THE DEFECT THIS PACK PINS.  ``RegistryMetricsQueryProvider`` carries a
``capture_active`` fact whose own contract is *"whether the wedge
capture is active"*, but the ``daemon/api.py`` construction site never
passed it — so the field defaulted to ``False`` forever and every
wedge-capture-idiom family (F1/F3) reported ``dark / capture_off`` even
while its series published.  That is R-i.

THE RULING (decisions.md DS-02/DS-03).  The honest source of truth is
the WEDGE-CAPTURE PROCESS FACT — ``app.state.wedge_capture_watchdog is
not None`` — never a switch value:

* the field's contract is *wedge capture*; the skill-capture
  kill-switch is a different subsystem (a category error);
* ``ENSEMBLE_WEDGE_CAPTURE_ENABLED`` would be a SWITCH fact, and the
  house rejected switch-facts for this exact slot — the sibling
  ``listener_enabled`` fact is switch-INVARIANT by construction;
* the process fact is the only one true at the moment the door is
  asked.

AND IT MUST BE LATE-BINDING.  The provider is constructed ~665 lines
BEFORE the watchdog exists, so the fact has to be resolved per read
(the house pattern already used for ``publisher_cadence``).  A boot
snapshot would be permanently ``False`` — that is the leg M-A1d proves.

Mutation table (each leg plants its defect on PRODUCTION source, the
pack must go RED, the file is restored sha-identically, then GREEN):

* ``M-A1a`` — drop the ``capture_active=`` kwarg at the ``api.py``
  construction site ⇒ the family reports ``capture_off`` while the
  watchdog is live;
* ``M-A1b`` — hard-code ``False`` where the resolver is stored ⇒ the
  same RED;
* ``M-A1c`` — make the closure read a SWITCH instead of the watchdog ⇒
  the source pin reds (the source of the fact is asserted);
* ``M-A1d`` — make the resolver a boot snapshot (evaluate once) ⇒ the
  late-binding pin reds.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from daemon import api as daemon_api
from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider


def _lifespan_source() -> str:
    """The lifespan's source (the house source-level wiring precedent)."""
    return inspect.getsource(inspect.unwrap(daemon_api.lifespan))


def _wedge_metric(name: str = "ensemble_capture_active_gauge") -> cm.Metric:
    """A PUBLISHED wedge-capture-idiom metric (F1/F3's shape)."""
    metric = cm.Metric(
        name,
        "gauge",
        (),
        None,
        "the capture_active wiring fixture",
        None,
        False,
        family=name,
        source_idiom="wedge_capture",
    )
    metric.set(1.0, ())
    metric.publish(force=True)
    return metric


class _Watchdog:
    """A stand-in for the wedge-capture watchdog handle."""


class _FakeState:
    """An ``app.state`` look-alike (the attribute may be ABSENT)."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _provider(state: _FakeState, metric: cm.Metric | None = None):
    """The provider EXACTLY as ``api.py`` wires it (the closure shape)."""
    fixture = metric or _wedge_metric()
    return RegistryMetricsQueryProvider(
        metrics_provider=lambda: [fixture],
        capture_active=lambda: (
            getattr(state, "wedge_capture_watchdog", None) is not None
        ),
    )


class TestTheSourceOfTruthIsTheWatchdogHandle:
    """A1 acceptance 1/2 — live watchdog ⇒ live; no watchdog ⇒ darkness."""

    def test_a_live_watchdog_makes_the_family_live(self) -> None:
        """The headline: F1/F3 must stop reporting ``dark/capture_off``."""
        provider = _provider(_FakeState(wedge_capture_watchdog=_Watchdog()))
        assert provider.capture_active is True
        assert provider.families()[0]["availability"] == {"state": "live"}

    def test_no_watchdog_keeps_the_family_dark(self) -> None:
        """The refusal direction is preserved (capture really is off)."""
        provider = _provider(_FakeState())
        assert provider.capture_active is False
        assert provider.families()[0]["availability"] == {
            "state": "dark",
            "reason": "capture_off",
        }

    def test_an_explicit_none_watchdog_is_also_dark(self) -> None:
        """``api.py`` sets the attribute to ``None`` on a disabled/failed start."""
        provider = _provider(_FakeState(wedge_capture_watchdog=None))
        assert provider.capture_active is False
        assert provider.families()[0]["availability"]["reason"] == "capture_off"

    def test_the_family_is_live_not_merely_not_dark(self) -> None:
        """A live family carries no reason at all — the F1 body shape."""
        provider = _provider(_FakeState(wedge_capture_watchdog=_Watchdog()))
        availability = provider.families()[0]["availability"]
        assert "reason" not in availability
        assert availability == {"state": "live"}


class TestTheFactIsLateBinding:
    """A1 acceptance 3 — a LIVE fact, never a boot snapshot (M-A1d)."""

    def test_a_watchdog_created_AFTER_construction_is_seen(self) -> None:
        """The load-bearing leg: the watchdog does not exist at construction.

        ``api.py`` builds the provider ~665 lines BEFORE the
        wedge-capture start block.  A resolver that captured the value at
        construction would be permanently ``False`` — and the F1/F3
        families would stay dark for the life of the process.
        """
        state = _FakeState()  # the attribute does not exist yet
        provider = _provider(state)
        assert provider.capture_active is False
        assert provider.families()[0]["availability"]["reason"] == "capture_off"

        # …the watchdog starts LATER in the same lifespan:
        state.wedge_capture_watchdog = _Watchdog()
        assert provider.capture_active is True
        assert provider.families()[0]["availability"] == {"state": "live"}

    def test_the_fact_flips_back_when_the_watchdog_dies(self) -> None:
        """A DEAD watchdog must be visible on the next read (not cached)."""
        state = _FakeState(wedge_capture_watchdog=_Watchdog())
        provider = _provider(state)
        assert provider.families()[0]["availability"] == {"state": "live"}

        state.wedge_capture_watchdog = None  # shutdown
        assert provider.capture_active is False
        assert provider.families()[0]["availability"]["reason"] == "capture_off"

    def test_the_resolver_is_read_per_availability_read(self) -> None:
        """The resolution is per READ, not per provider construction."""
        calls: list[int] = []

        def _resolver() -> bool:
            calls.append(len(calls))
            return True

        provider = RegistryMetricsQueryProvider(
            capture_active=_resolver,
            metrics_provider=lambda: [_wedge_metric()],
        )
        assert calls == []
        assert provider.capture_active is True
        assert provider.capture_active is True
        assert len(calls) == 2


class TestTheResolverContractIsBackwardCompatible:
    """The bool form must keep working (existing callers/tests)."""

    def test_a_plain_bool_still_works(self) -> None:
        metric = _wedge_metric()
        assert (
            RegistryMetricsQueryProvider(
                capture_active=True, metrics_provider=lambda: [metric]
            ).capture_active
            is True
        )
        assert (
            RegistryMetricsQueryProvider(
                capture_active=False, metrics_provider=lambda: [metric]
            ).capture_active
            is False
        )

    def test_the_default_is_dark(self) -> None:
        """No fact supplied ⇒ ``capture_off`` (the fail-closed default)."""
        metric = _wedge_metric()
        provider = RegistryMetricsQueryProvider(metrics_provider=lambda: [metric])
        assert provider.capture_active is False
        assert provider.families()[0]["availability"]["reason"] == "capture_off"

    def test_a_raising_resolver_degrades_to_dark(self) -> None:
        """A broken resolver never breaks the door (and never lies live)."""

        def _boom() -> bool:
            raise RuntimeError("resolver exploded")

        provider = RegistryMetricsQueryProvider(
            capture_active=_boom,
            metrics_provider=lambda: [_wedge_metric()],
        )
        assert provider.capture_active is False
        assert provider.families()[0]["availability"]["reason"] == "capture_off"


class TestTheApiWiringSite:
    """A1's construction-site pins — the fact must be PASSED at ``api.py``."""

    def test_the_lifespan_passes_a_capture_active_resolver(self) -> None:
        """M-A1a — dropping the kwarg leaves the family permanently dark."""
        source = _lifespan_source()
        assert "capture_active=" in source, (
            "daemon/api.py must PASS capture_active= to "
            "RegistryMetricsQueryProvider — without it the field is "
            "permanently False and F1/F3 report capture_off forever"
        )

    def test_the_resolver_reads_the_wedge_capture_watchdog(self) -> None:
        """M-A1c — the SOURCE of the fact is the watchdog, not a switch."""
        source = _lifespan_source()
        assert 'getattr(app.state, "wedge_capture_watchdog", None)' in source, (
            "the capture_active resolver must read the wedge-capture "
            "PROCESS FACT (app.state.wedge_capture_watchdog) — a switch "
            "fact would repeat the mistake listener_enabled avoids"
        )

    def test_the_wiring_never_reads_the_skill_capture_kill_switch(self) -> None:
        """The field is wedge capture; skill capture is a different subsystem.

        The assertion is scoped to the CONSTRUCTION BLOCK (the wiring
        region) — the lifespan's own wedge-start log line legitimately
        names ``ENSEMBLE_WEDGE_CAPTURE_ENABLED`` for its operator
        message, which is not a fact source for this provider.
        """
        source = _lifespan_source()
        start = source.index("RegistryMetricsQueryProvider(")
        block = source[start : source.index("app.state.metrics_query = ", start)]
        for forbidden in (
            "ENSEMBLE_SKILL_CAPTURE_ENABLED",
            "ENSEMBLE_WEDGE_CAPTURE_ENABLED",
            "metrics_gate",
        ):
            assert forbidden not in block, (
                f"the capture_active wiring must not read {forbidden} — the "
                "fact is the wedge-capture PROCESS fact, not a switch value"
            )
        # …and the provider READS no switch either (its prose names the
        # two switches only to say why they are NOT the fact source, so
        # the assertion is on the READ surface, not on the token text).
        provider_source = inspect.getsource(RegistryMetricsQueryProvider)
        for forbidden in ("os.environ", "getenv(", "ens_env_read"):
            assert forbidden not in provider_source
        assert "_capture_active_resolver" in provider_source

    def test_the_watchdog_start_block_appears_AFTER_the_wiring(self) -> None:
        """The late-binding requirement is non-vacuous (M-A1d).

        If the watchdog were created BEFORE the provider, a boot
        snapshot would work and this pack's late-binding pin would be
        proving nothing.  The ORDER is the premise.
        """
        source = _lifespan_source()
        assert source.index("RegistryMetricsQueryProvider(") < source.index(
            "app.state.wedge_capture_watchdog = _wedge_watchdog"
        ), (
            "the provider is constructed BEFORE the watchdog exists — "
            "the late-binding resolver is what makes the fact honest"
        )


class TestTheAvailabilityDocstringNamesTheProcessFact:
    """A1's availability.py docstring correction (the field's own contract)."""

    def test_the_field_names_the_wedge_capture_process_fact(self) -> None:
        from daemon.services.metrics_query import availability as availability_module

        field_doc = availability_module.AvailabilityFacts.__annotations__
        assert "capture_active" in field_doc
        source = inspect.getsource(availability_module.AvailabilityFacts)
        assert "wedge" in source.lower()
        assert "process fact" in source.lower()
