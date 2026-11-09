"""A2 — the CENSUS POLARITY pack (registered pack
``metrics_census_polarity_unit_test``).

THE DEFECT THIS PACK PINS (R-ii).  ``CensusSampler.gated`` is documented
as *"whether the census sampler's DB work is currently gated OFF"*, but
it returned ``self._gate()`` — the ENABLED sense.  Downstream that
double-inverted through two negations in the provider
(``census_enabled = not gated``, then ``census_gated = not census_enabled``)
to the net effect: **F6 reported ``dark / census_gated`` exactly while
the census was running and publishing 15 live series.**

WHY THE FIX IS AT THE FACT OWNER.  ``availability.py`` labels; the FACT
owner is ``CensusSampler.gated``.  Fixing the label would leave the
wrong value on the wire for every other consumer, and fixing the
provider would leave the lying property in place *and* rewrite every
existing fake — the fakes already encode the CORRECTED contract
(``gated=True`` means gated OFF), so the production class was the only
thing out of contract.  The fix therefore adds NO check site: the
property still calls the existing ``_gate()``, so the behavioural
check-site census over ``daemon/`` stays exactly 2.

Mutation table:

* ``M-A2a`` — revert ``metrics_census.py`` to ``return self._gate()``
  ⇒ the enabled+running family reports ``dark/census_gated`` again;
* ``M-A2b`` — "fix" it at the consumer (``provider.py`` uses
  ``bool(self._census.gated)``) ⇒ the resolution-layer pins red, because
  they assert the FACT (``gated`` is the gated-off sense), not the
  provider expression;
* ``M-A2c`` — swap the R-i/R-ii labels back in ``availability.py`` ⇒
  the label-agreement pin reds.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from daemon import checkpoint_metrics as cm
from daemon.services.metrics_census import CensusSampler
from daemon.services.metrics_query.availability import (
    AVAILABILITY_REASONS,
    SOURCE_IDIOM_POSTGRES_CENSUS,
)
from daemon.services.metrics_query.provider import RegistryMetricsQueryProvider

REPO_ROOT = Path(__file__).resolve().parents[3]


def _published_census_metric(name: str = "ensemble_a2_census_gauge") -> cm.Metric:
    """A PUBLISHED postgres-census-idiom metric (the F6 family shape)."""
    metric = cm.Metric(
        name,
        "gauge",
        (),
        None,
        "the census-polarity fixture",
        None,
        False,
        family=name,
        source_idiom=SOURCE_IDIOM_POSTGRES_CENSUS,
    )
    metric.set(15.0, ())
    metric.publish(force=True)
    return metric


def _sampler(*, gate: bool, running: bool) -> CensusSampler:
    """A REAL ``CensusSampler`` whose gate is injected, not env-read."""
    sampler = CensusSampler(gate_enabled=lambda: gate, dsn=None)
    if running:
        # The ``running`` property is a plain boolean set by ``start()``;
        # marking it directly keeps the thread out of the test.
        sampler._started = True
    return sampler


def _provider(sampler: CensusSampler, metric: cm.Metric | None = None):
    fixture = metric or _published_census_metric()
    return RegistryMetricsQueryProvider(
        census=sampler, metrics_provider=lambda: [fixture]
    )


def _gated_property_source() -> str:
    """The ``CensusSampler.gated`` property's own source."""
    return inspect.getsource(CensusSampler.gated.fget)


class TestTheResolutionLayerCarriesTheGatedOffSense:
    """The FACT owner: ``gated`` is True exactly when the census is OFF."""

    def test_an_enabled_census_is_not_gated(self) -> None:
        """M-A2a — reverting the negation reds this pin."""
        assert _sampler(gate=True, running=True).gated is False

    def test_a_disabled_census_is_gated(self) -> None:
        """The refusal direction: a real gate is still reported as gated."""
        assert _sampler(gate=False, running=True).gated is True

    def test_the_property_calls_the_existing_gate_and_adds_no_check_site(self) -> None:
        """The fix must NOT introduce a second switch read in the class."""
        source = _gated_property_source()
        assert "self._gate()" in source
        assert "not self._gate()" in source, (
            "the property must NEGATE the enabled-sense _gate() — that "
            "negation IS the R-ii fix"
        )


class TestF6IsLiveWhenTheCensusIsEnabledAndPublishing:
    """A2 acceptance 1 — the headline: no more false ``census_gated``."""

    def test_an_enabled_running_publishing_census_reports_live(self) -> None:
        provider = _provider(_sampler(gate=True, running=True))
        availability = provider.families()[0]["availability"]
        assert availability == {"state": "live"}, availability

    def test_the_activation_block_agrees(self) -> None:
        """``activation.census_enabled`` is the honest switch-derived bit."""
        provider = _provider(_sampler(gate=True, running=True))
        assert provider.census_enabled is True
        assert provider.availability_facts.census_gated is False

        provider_off = _provider(_sampler(gate=False, running=True))
        assert provider_off.census_enabled is False
        assert provider_off.availability_facts.census_gated is True


class TestTheRefusalDirectionIsPreserved:
    """A2 acceptance 2 — a genuinely gated census still reports dark."""

    def test_a_disabled_census_reports_census_gated(self) -> None:
        provider = _provider(_sampler(gate=False, running=True))
        assert provider.families()[0]["availability"] == {
            "state": "dark",
            "reason": "census_gated",
        }

    def test_a_non_running_census_still_wins_the_order(self) -> None:
        """The ORDER property is untouched by the polarity fix."""
        provider = _provider(_sampler(gate=True, running=False))
        assert provider.families()[0]["availability"] == {
            "state": "dark",
            "reason": "census_not_running",
        }

    def test_an_enabled_census_with_no_series_is_series_absent(self) -> None:
        metric = cm.Metric(
            "ensemble_a2_census_gauge_unpublished",
            "gauge",
            (),
            None,
            "no observation",
            None,
            False,
            family="ensemble_a2_census_gauge_unpublished",
            source_idiom=SOURCE_IDIOM_POSTGRES_CENSUS,
        )
        provider = _provider(_sampler(gate=True, running=True), metric)
        assert provider.families()[0]["availability"] == {
            "state": "dark",
            "reason": "series_absent",
        }

    def test_every_emitted_reason_stays_inside_the_closed_set(self) -> None:
        emitted = {
            _provider(_sampler(gate=g, running=r)).families()[0]["availability"].get(
                "reason"
            )
            for g, r in ((True, True), (False, True), (True, False))
        }
        emitted.discard(None)
        assert emitted <= set(AVAILABILITY_REASONS), emitted - set(AVAILABILITY_REASONS)


class TestTheCheckSiteCensusStaysAtTwo:
    """A2 acceptance 3 — the fix adds no third check site."""

    def test_the_behavioural_check_site_census_is_two(self) -> None:
        hits: list[str] = []
        for path in sorted((REPO_ROOT / "daemon").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for number, line in enumerate(text.splitlines(), start=1):
                if "metrics_gate.metrics_enabled()" in line:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{number}")
        assert len(hits) == 2, f"the behavioural check-site census drifted: {hits}"


class TestTheLabelsAgreeWithTheDisclosure:
    """A2 acceptance 4 — R-i is the wiring, R-ii is the polarity.

    ``M-A2c`` — swapping the labels back in ``availability.py`` reds
    this pin.  The DISCLOSURE half is asserted read-only (the file is
    outside this lane's edit fence): the pin only requires that the two
    artefacts use the SAME R-id for the SAME defect.
    """

    def test_availability_labels_the_polarity_r_ii(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "services" / "metrics_query" / "availability.py"
        ).read_text(encoding="utf-8")
        # The polarity sentence must carry R-ii…
        polarity_lines = [
            line for line in source.splitlines() if "census.gated" in line
        ]
        assert polarity_lines, "the polarity comment vanished"
        joined = "\n".join(polarity_lines)
        assert "R-ii" in joined, (
            "the census-polarity defect is R-ii (the disclosure's "
            "assignment) — the code comment must agree"
        )
        # …and R-i must be attributed to the capture_active WIRING.
        assert "R-i" in source
        capture_lines = [line for line in source.splitlines() if "capture_active" in line]
        assert any("R-i" in line for line in capture_lines), (
            "R-i (the unwired capture_active) must not be attributed to the "
            "census polarity"
        )

    def test_the_disclosure_assigns_the_same_r_ids(self) -> None:
        disclosure = (
            REPO_ROOT / "docs" / "architecture" / "metrics-promote-disclosure.md"
        ).read_text(encoding="utf-8")
        assert "R-i" in disclosure and "R-ii" in disclosure
        # The disclosure's own assignment: R-i = capture_active wiring.
        r1_lines = [line for line in disclosure.splitlines() if "R-i:" in line]
        assert any("capture_active" in line for line in r1_lines), (
            "the disclosure assigns R-i to the capture_active wiring"
        )
        # …and R-ii = the census polarity.
        assert "R-ii" in disclosure and "polarity" in disclosure
