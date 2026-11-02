"""The F7 op-enum narrowing pack (registered pack ``metrics_registry_narrowing``).

Plan flag D-7 (ruled by the architect): the checkpoint ``op`` enum is
narrowed to the adjudicated OBSERVABLE set ``{aget, other}``.

* ``aget`` is the only observed producer (the write path bypasses the
  saver-op seam; ``alist`` is migration-only);
* ``other`` is the RESERVED FOLD TARGET and must stay declarable — a
  disallowed value folds INTO it, so dropping it would push the fold
  target itself out of the vocabulary;
* the legacy ``aput`` / ``adelete`` / ``alist`` values have no producer
  today, so they are not declarable — an observation with one of them
  folds to ``other`` and is counted as an invalid label.

Mutation table: ``P2-M7`` (the op enum re-widened to the 5-value set).
"""

from __future__ import annotations

import pytest

from daemon import checkpoint_metrics as cm


class TestNarrowedOpEnum:
    def test_the_frozen_op_vocabulary_is_the_adjudicated_set(self) -> None:
        """P2-M7 — the narrowed set, not the legacy 5-value set."""
        assert cm.CHECKPOINT_OP_VOCABULARY == frozenset({"aget", "other"})

    def test_the_registered_metric_declares_the_narrowed_set(self) -> None:
        metric = cm.ensemble_checkpoint_op_seconds
        assert metric.allowed_values["op"] == frozenset({"aget", "other"})

    def test_the_legacy_values_are_not_declarable(self) -> None:
        allowed = cm.ensemble_checkpoint_op_seconds.allowed_values["op"]
        for legacy in ("aput", "adelete", "alist"):
            assert legacy not in allowed

    def test_the_fold_target_stays_declarable(self) -> None:
        assert cm.RESERVED_LABEL_VALUE in cm.CHECKPOINT_OP_VOCABULARY

    def test_the_help_text_names_the_narrowed_ops(self) -> None:
        help_text = cm.ensemble_checkpoint_op_seconds.help
        assert "aget" in help_text
        assert "other" in help_text
        for legacy in ("aput", "adelete", "alist"):
            assert legacy not in help_text

    def test_an_observation_with_a_legacy_op_folds_to_other(self) -> None:
        """A disallowed value folds to ``other`` and is counted."""
        metric = cm.Metric(
            "ensemble_narrowing_probe",
            "histogram",
            ("op",),
            {"op": cm.CHECKPOINT_OP_VOCABULARY},
            "",
            (1.0, 2.0),
            False,
        )
        metric.observe(0.5, {"op": "aput"})
        metric.publish(force=True)
        snapshot = metric.published_snapshot()
        assert snapshot is not None
        labels = [dict(key) for key in snapshot]
        assert labels == [{"op": "other"}]

    def test_an_observation_with_the_observed_op_is_kept(self) -> None:
        metric = cm.Metric(
            "ensemble_narrowing_probe_kept",
            "histogram",
            ("op",),
            {"op": cm.CHECKPOINT_OP_VOCABULARY},
            "",
            (1.0, 2.0),
            False,
        )
        metric.observe(0.5, {"op": "aget"})
        metric.publish(force=True)
        snapshot = metric.published_snapshot()
        assert snapshot is not None
        labels = [dict(key) for key in snapshot]
        assert labels == [{"op": "aget"}]

    def test_the_narrowing_does_not_touch_the_read_family_enums(self) -> None:
        """The read family's ``op`` enum is a DIFFERENT vocabulary."""
        read_ops = cm.checkpoint_read_ops_total.allowed_values.get("op")
        assert read_ops == frozenset({"aget", "aget_tuple"})


class TestRegistrationMetadata:
    """AM-3 — the registration-time family metadata kwargs."""

    def test_the_kwargs_are_accepted_by_every_registration_helper(self) -> None:
        counter = cm.register_counter(
            "ensemble_narrowing_meta_counter",
            family="F-meta",
            source_idiom="registry",
            durability="process",
            cardinality_weight=2,
            chart_role="step",
        )
        gauge = cm.register_gauge(
            "ensemble_narrowing_meta_gauge",
            family="F-meta",
            chart_role="line",
        )
        histogram = cm.register_histogram(
            "ensemble_narrowing_meta_histogram",
            family="F-meta",
            chart_role="line",
        )
        for metric in (counter, gauge, histogram):
            assert metric.family == "F-meta"
            assert metric.source_idiom == "registry"
            assert metric.durability == "process"
        assert counter.cardinality_weight == 2
        assert counter.chart_role == "step"

    def test_the_pre_existing_registrations_carry_the_derived_defaults(self) -> None:
        """The 18 pre-existing registrations are untouched (AM-3).

        SCOPED (central-metrics-v1 P4 — a disclosed test edit): the
        registry now ALSO carries the P4 extension families, and those
        registrations DECLARE their metadata (AM-3) — the derived
        defaults are the PRE-P4 registrations' premise, not the
        extension subset's.  The scope is the P4 family set, so a new
        pre-existing registration with an explicit family would still
        be caught.
        """
        p4_families = set()
        try:
            from daemon.services.metrics_query.wiring.adjudication import (
                REGISTERED_FAMILIES,
            )

            p4_families = set(REGISTERED_FAMILIES)
        except Exception:  # noqa: BLE001 — the wiring module may be absent
            p4_families = set()
        for metric in cm.registered_metrics():
            if metric.name.startswith("ensemble_narrowing_meta_"):
                continue
            if metric.family in p4_families:
                continue
            assert metric.source_idiom == "registry"
            assert metric.durability == "process"
            assert metric.cardinality_weight == 1
            assert metric.chart_role is None

    def test_the_publish_age_accessor_is_public(self) -> None:
        """AM-13 — the reader asks the metric, never the private field."""
        metric = cm.Metric("ensemble_narrowing_age_probe", "gauge")
        assert metric.last_publish_monotonic() == 0.0
        metric.publish(force=True)
        assert metric.last_publish_monotonic() > 0.0

    def test_the_label_key_serializer_is_public(self) -> None:
        """AM-1 — one canonical series-identity serialization."""
        assert cm.label_key({"b": "2", "a": "1"}) == (("a", "1"), ("b", "2"))
