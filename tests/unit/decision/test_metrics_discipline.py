"""The decision module's metric discipline (L1 / L2 / L3).

The registrar's canonical name rule is ``^ensemble_[a-z0-9_]+$``
(``daemon/checkpoint_metrics.py:110``) and the validator RAISES on a
violation — so every series is ``ensemble_decision_overlay_*`` and the
family id is the single string ``decision_overlay``.

Because the decision lane takes the L3 FALLBACK (the raw
``cm.register_*`` path — the wiring registrar would require an
adjudication-pack census amendment that the pack's own test pins by
EXACT set equality over the 27 ``F*`` families, and that test file is
not the decision lane's), the AM-2 discipline is enforced HERE instead:
every label key carries an explicit ``allowed_values`` set.
"""

from __future__ import annotations

import re

from daemon import checkpoint_metrics as cm
from daemon.services.decision import metrics
from daemon.services.decision.types import FailureClass, SuppressionReason

CANONICAL_NAME = re.compile(r"^ensemble_[a-z0-9_]+$")


def test_every_series_carries_the_canonical_name() -> None:
    """L1 — the registrar rejects a non-canonical name."""
    for handle in metrics.decision_metric_handles().values():
        assert CANONICAL_NAME.match(handle.name), handle.name


def test_every_series_carries_the_one_family_id() -> None:
    """L1 — the family id is ``decision_overlay`` for every series."""
    for handle in metrics.decision_metric_handles().values():
        assert handle.family == "decision_overlay", handle.name


def test_every_label_key_declares_its_allowed_values() -> None:
    """AM-2 — enforced locally (the L3 fallback's substitute)."""
    for handle in metrics.decision_metric_handles().values():
        for key in handle.label_keys:
            assert key in handle.allowed_values, (handle.name, key)
            assert handle.allowed_values[key], (handle.name, key)


def test_the_label_vocabularies_are_bounded() -> None:
    """L3 — the closed vocabularies are the ones the design names."""
    assert len(metrics.FAILURE_CLASSES) == 19
    assert len(metrics.SUPPRESSION_REASONS) == 10
    assert set(metrics.MODES) == {"shadow", "deciding"}
    assert set(metrics.VERDICTS) == {"agree", "disagree", "no_overlay"}
    assert set(metrics.REVERIFY_OUTCOMES) == {
        "verified_off",
        "verified_on",
        "unreadable",
    }
    assert set(metrics.PROVISIONING_STATES) == {
        "ok",
        "provisioning_missing",
        "allow_list_excluded",
    }
    assert set(metrics.COST_TRIP_REASONS) == {
        "global_ceiling",
        "per_seam_share",
    }


def test_no_series_carries_a_question_id_label() -> None:
    """L2 — the per-question signal lives in the SHADOW LOG.

    An unbounded per-question series would blow the cardinality cap and
    its excess increments are silently DROPPED.
    """
    for handle in metrics.decision_metric_handles().values():
        assert "question_id" not in handle.label_keys, handle.name


def test_the_families_are_not_chart_primary() -> None:
    """They are OPERATIONAL — they do NOT join the chart roster."""
    for handle in metrics.decision_metric_handles().values():
        assert handle.chart_role is None, handle.name


def test_the_suppression_vocabulary_carries_pricing_unconfigured() -> None:
    """L8 — the visible degradation joins the closed vocabulary."""
    assert "pricing_unconfigured" in metrics.SUPPRESSION_REASONS
    assert SuppressionReason.PRICING_UNCONFIGURED.value in metrics.SUPPRESSION_REASONS


def test_the_two_vocabularies_stay_disjoint() -> None:
    assert set(metrics.FAILURE_CLASSES) & set(metrics.SUPPRESSION_REASONS) == set()
    assert FailureClass.SEAM_FAULT.value in metrics.FAILURE_CLASSES


def test_the_families_are_registered_in_the_canonical_registry() -> None:
    """They ARE real registry entries (the raw registrar's output)."""
    names = {m.name for m in cm.registered_metrics()}
    for handle in metrics.decision_metric_handles().values():
        assert handle.name in names


def test_the_family_is_registered_through_the_wiring_registrar() -> None:
    """L3 — the PRIMARY path: the families live in the WIRING registry."""
    from daemon.services.metrics_query.wiring import registry as wiring_registry

    assert metrics.FAMILY_ID in wiring_registry.registered_families()
    registered = {
        m.name for m in wiring_registry.registered_family_metrics()
        if m.family == metrics.FAMILY_ID
    }
    mine = {m.name for m in metrics.decision_metric_handles().values()}
    assert registered == mine, "the census must hold exactly this lane's families"


def test_the_family_is_in_the_adjudication_census() -> None:
    """L3 — the pack's census admits the family (verdict + band)."""
    from daemon.services.metrics_query.wiring import adjudication

    assert metrics.FAMILY_ID in adjudication.CATALOG_FAMILY_VERDICTS
    assert metrics.FAMILY_ID in adjudication.REGISTERED_FAMILIES
    assert metrics.FAMILY_ID in adjudication.FAMILY_SERIES_BANDS
    verdict, seam = adjudication.CATALOG_FAMILY_VERDICTS[metrics.FAMILY_ID]
    assert verdict in adjudication.VALID_VERDICTS
    assert seam and seam.strip()


def test_the_closed_label_space_stays_inside_the_declared_band() -> None:
    """The band is a CEILING — the measured space never exceeds it."""
    from daemon.services.metrics_query.wiring import adjudication
    from daemon.services.metrics_query.wiring import registry as wiring_registry

    family_metrics = [
        m
        for m in wiring_registry.registered_family_metrics()
        if m.family == metrics.FAMILY_ID
    ]
    measured = 0
    for metric in family_metrics:
        combinations = 1
        for key in metric.label_keys:
            allowed = metric.allowed_values.get(key)
            combinations *= len(allowed) if allowed is not None else 1
        if metric.kind == "histogram":
            combinations *= len(metric.buckets or ()) + 2
        measured += combinations
    assert measured <= adjudication.FAMILY_SERIES_BANDS[metrics.FAMILY_ID]
