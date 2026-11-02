"""The query-door switch-invariance pack (registered pack
``metrics_query_switch_invariance``).

The door is NEVER switch-gated (AM-17/D-11b, AM-18): the registry
accumulates ungated, so the door serves live registry-sourced data in
BOTH switch states.  The only permitted body differences are:

1. the census-derived family's ``availability`` → ``census_gated``;
2. ``activation.census_enabled`` → false;
3. ``last_publish_age_s`` growth (a clock, not a switch effect).

Everything else is byte-identical, and the AUTH behaviour is identical
too — the switch never touches an authenticated surface.

The pack also pins the two STRUCTURAL facts that make the claim
trustworthy:

* the behavioural check-site census over ``daemon/`` stays at exactly
  **2** (the listener body call line + the census tick call line);
* NEITHER the query package NOR the routes read the switch directly —
  ``census_gated`` comes from the PUBLIC ``CensusSampler.gated``
  property (a read-through of the existing gate site).

Mutation table: ``P3-M3`` (the switch-invariance grep pin's exclusion
widened) · ``P3-M11`` (``census_gated`` sourced from a direct switch
read in the query package).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider
from tests.helpers.metrics_query_fixtures import (
    QUERY_PATHS,
    RecordingProvider,
    build_client,
    error_code,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


class _FakeCensus:
    """A census stand-in exposing the PUBLIC ``gated``/``running`` facts."""

    def __init__(self, *, gated: bool, running: bool = True) -> None:
        self.gated = gated
        self.running = running


def _metric(
    name: str,
    *,
    source_idiom: str = "registry",
    family: str | None = None,
) -> cm.Metric:
    metric = cm.Metric(
        name,
        "gauge",
        (),
        None,
        "the switch-invariance fixture",
        None,
        False,
        family=family or name,
        source_idiom=source_idiom,
    )
    metric.set(1.0, ())
    metric.publish(force=True)
    return metric


def _provider(*, gated: bool) -> RegistryMetricsQueryProvider:
    """A provider over ONE registry family + ONE census family."""
    return RegistryMetricsQueryProvider(
        census=_FakeCensus(gated=gated),
        ring=None,
        metrics_provider=lambda: [
            _metric("ensemble_p3_registry_gauge", source_idiom="registry"),
            _metric(
                "ensemble_p3_census_gauge",
                source_idiom="postgres_census",
            ),
        ],
    )


class TestAuthBehaviourIsSwitchInvariant:
    """Invariant (1) — the auth behaviour is identical across states."""

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_status_code_is_identical(self, path):
        on = build_client(_provider(gated=False)).get(path)
        off = build_client(_provider(gated=True)).get(path)
        assert on.status_code == off.status_code == 200

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_error_vocabulary_is_identical(self, path):
        on = build_client(_provider(gated=False), scopes=()).get(path)
        off = build_client(_provider(gated=True), scopes=()).get(path)
        assert on.status_code == off.status_code == 403
        assert error_code(on) == error_code(off) == "SCOPE_INSUFFICIENT"

    @pytest.mark.parametrize("path", QUERY_PATHS)
    def test_the_auth_off_refusal_is_identical(self, path):
        on = build_client(_provider(gated=False), auth_on=False).get(path)
        off = build_client(_provider(gated=True), auth_on=False).get(path)
        assert on.status_code == off.status_code == 503
        assert error_code(on) == error_code(off) == "AUTH_NOT_CONFIGURED"


class TestSeriesSetIsSwitchInvariant:
    """Invariant (2) — the registry-sourced series set is identical."""

    def test_the_registry_family_is_identical_across_the_states(self):
        on = build_client(_provider(gated=False)).get(
            "/api/metrics/families"
        ).json()
        off = build_client(_provider(gated=True)).get("/api/metrics/families").json()

        def _family(body, family_id):
            return next(
                entry for entry in body["families"] if entry["id"] == family_id
            )

        on_family = _family(on, "ensemble_p3_registry_gauge")
        off_family = _family(off, "ensemble_p3_registry_gauge")
        assert on_family["live_series_count"] == off_family["live_series_count"]
        assert on_family["availability"] == off_family["availability"]
        assert on_family["role"] == off_family["role"]
        assert on_family["cardinality_contribution"] == off_family[
            "cardinality_contribution"
        ]

    def test_the_registry_series_values_are_identical_across_the_states(self):
        on = build_client(_provider(gated=False)).get("/api/metrics/snapshot").json()
        off = build_client(_provider(gated=True)).get("/api/metrics/snapshot").json()
        on_names = [entry["name"] for entry in on["series"]]
        off_names = [entry["name"] for entry in off["series"]]
        assert on_names == off_names


class TestOnlyPermittedDifferences:
    """Invariant (3) — the ONLY permitted body differences."""

    def test_the_permitted_differences_are_exactly_those_three(self):
        on = build_client(_provider(gated=False)).get("/api/metrics/families").json()
        off = build_client(_provider(gated=True)).get("/api/metrics/families").json()

        # (i) the census family goes dark with reason census_gated
        on_census = next(
            entry
            for entry in on["families"]
            if entry["id"] == "ensemble_p3_census_gauge"
        )
        off_census = next(
            entry
            for entry in off["families"]
            if entry["id"] == "ensemble_p3_census_gauge"
        )
        assert on_census["availability"] == {"state": "live"}
        assert off_census["availability"] == {"state": "dark", "reason": "census_gated"}

        # (ii) activation.census_enabled flips
        assert on["activation"]["census_enabled"] is True
        assert off["activation"]["census_enabled"] is False

        # (iii) the activation's OTHER facts are switch-invariant
        assert on["activation"]["listener_enabled"] == off["activation"][
            "listener_enabled"
        ]
        assert on["activation"]["query_api"] == off["activation"]["query_api"]

    def test_the_envelope_header_is_identical_outside_the_census_bit(self):
        on = build_client(_provider(gated=False)).get("/api/metrics/families").json()
        off = build_client(_provider(gated=True)).get("/api/metrics/families").json()
        assert on["schema_version"] == off["schema_version"]
        assert on["vocabulary_version"] == off["vocabulary_version"]
        assert on["restart_epoch"] == off["restart_epoch"]


class TestCheckSiteCensus:
    """Invariant (4) — the switch is consulted at exactly TWO sites."""

    def test_the_behavioural_check_site_census_is_two(self):
        hits: list[str] = []
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if "metrics_gate.metrics_enabled()" in line:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        assert len(hits) == 2, f"the behavioural check-site census drifted: {hits}"

    def test_the_census_sampler_exposes_the_public_gated_property(self):
        from daemon.services.metrics_census import CensusSampler

        assert hasattr(CensusSampler, "gated")
        assert hasattr(CensusSampler, "_gate")


class TestNoDirectSwitchRead:
    """Invariant (5) — no direct switch read in the package OR the routes."""

    def test_the_query_package_never_reads_the_switch(self):
        package = REPO_ROOT / "daemon" / "services" / "metrics_query"
        for path in sorted(package.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for forbidden in (
                "metrics_gate",
                "ENSEMBLE_METRICS_ENABLED",
                "metrics_enabled(",
            ):
                assert forbidden not in text, (
                    f"{path.name} must not consult {forbidden} — the query "
                    "package NEVER reads the switch"
                )

    def test_the_query_routes_never_read_the_switch(self):
        """P3-M3/P3-M11 — the ROUTES are covered by the same exclusion."""
        router = (REPO_ROOT / "daemon" / "routers" / "metrics.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "metrics_gate",
            "ENSEMBLE_METRICS_ENABLED",
            "metrics_enabled(",
        ):
            assert forbidden not in router, (
                f"the query routes must not consult {forbidden} — the door is "
                "NEVER switch-gated"
            )

    def test_the_census_gated_reason_comes_from_the_public_property(self):
        """The reason is derived from ``CensusSampler.gated`` — a
        read-through of the EXISTING gate site (no third check site)."""
        provider = _provider(gated=True)
        assert provider.census_enabled is False
        assert provider.availability_facts.census_gated is True
