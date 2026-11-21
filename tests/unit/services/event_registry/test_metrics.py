"""P5 pack — the observability projection (T5.7, AC6 / D5.4 / D5.5 / D5.12)."""

from __future__ import annotations

import logging

import pytest

from daemon.services.event_registry import (
    KIND_INDEX,
    counters,
    record,
    reset_warning_state_for_tests,
    snapshot,
)


class _StubBus:
    """Duck-typed EventBus: only ``_global_subscribers`` is consulted."""

    def __init__(self, subscriber_ids: tuple[str, ...] = ()):
        self._global_subscribers = {
            sid: object() for sid in subscriber_ids
        }


# ── Snapshot shape (D5.5) ────────────────────────────────────────────────


def test_snapshot_shape_carries_every_declared_dim():
    snap = snapshot()
    assert set(snap) == {"kinds", "counters", "generated_at"}
    for kind_id, entry in snap["kinds"].items():
        assert set(entry) == {
            "class",
            "service_tag",
            "posture",
            "fired_total",
            "last_fired_at",
            "consumers",
            "consumers_live",
            "lag_seconds",
            "lag_reason",
            "lag_measurable",
            "bridge_pending",
            "deferred_v2",
        }, (kind_id, set(entry))
        assert entry["class"] == KIND_INDEX[kind_id].klass.name
        assert entry["posture"] == KIND_INDEX[kind_id].posture.value


def test_snapshot_counts_a_fired_kind():
    record("instance_terminalized", instance_id="i1", outcome="completed",
           fired_count=1)
    record("instance_terminalized", instance_id="i2", outcome="completed",
           fired_count=3)
    entry = snapshot()["kinds"]["instance_terminalized"]
    assert entry["fired_total"] == 2
    assert entry["last_fired_at"] is not None


def test_snapshot_reports_the_registry_counters():
    record("bogus_kind")
    record("turn_transition")
    snap = snapshot()
    assert snap["counters"]["record_refused_unknown_kind_total"] == 1
    assert snap["counters"]["record_refused_deferred_kind_total"] == 1
    assert counters()["record_refused_unknown_kind_total"] == 1


# ── AC6 — consumer liveness (D5.4) ───────────────────────────────────────


def test_event_bus_consumer_live_when_subscribed():
    bus = _StubBus(("job_feedback_observer",))
    entry = snapshot(bus=bus)["kinds"]["event_bus:job_completed"]
    assert entry["consumers"] == ["job_feedback_observer"]
    assert entry["consumers_live"] == [
        {"consumer": "job_feedback_observer", "live": True}
    ]


def _absent_warnings(caplog, kind_id: str) -> list:
    return [
        r for r in caplog.records
        if "ABSENT from the live subscriber set" in r.getMessage()
        and f"of kind '{kind_id}'" in r.getMessage()
    ]


def test_declared_consumer_absent_reports_false_and_warns_once(caplog):
    bus = _StubBus(())  # NO subscriber — the "dead consumer" shape
    kind_id = "event_bus:job_completed"
    with caplog.at_level(logging.WARNING, logger="daemon.services.event_registry.metrics"):
        entry = snapshot(bus=bus)["kinds"][kind_id]
        assert entry["consumers_live"] == [
            {"consumer": "job_feedback_observer", "live": False}
        ]
        warnings = _absent_warnings(caplog, kind_id)
        assert len(warnings) == 1, "the absent consumer must warn exactly once"
        # A SECOND snapshot must NOT warn again (the one-WARNING contract).
        snapshot(bus=bus)
        warnings = _absent_warnings(caplog, kind_id)
        assert len(warnings) == 1


def test_no_bus_passed_reports_unverifiable_not_absent():
    # Honesty: "registry could not see it" ≠ "no consumer".
    entry = snapshot(bus=None)["kinds"]["event_bus:job_completed"]
    assert entry["consumers_live"] == [
        {"consumer": "job_feedback_observer", "live": None}
    ]


def test_non_bus_kinds_have_no_live_source():
    entry = snapshot(bus=_StubBus(("job_feedback_observer",)))[
        "kinds"]["instance_terminalized"]
    assert all(c["live"] is None for c in entry["consumers_live"])
    assert entry["consumers_live"][0]["consumer"] == "dependency_bus:child_keyed_waiters"


def test_warning_state_reset_re_warns(caplog):
    bus = _StubBus(())
    kind_id = "event_bus:job_completed"
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        snapshot(bus=bus)
        first = len(_absent_warnings(caplog, kind_id))
        assert first == 1
    reset_warning_state_for_tests()
    with caplog.at_level(_logging.WARNING):
        snapshot(bus=bus)
        second = len(_absent_warnings(caplog, kind_id))
        assert second == 2, "after a reset the warning must fire again"


# ── D5.12 — lag honesty in the projection ────────────────────────────────


def test_terminalization_lag_is_none_with_the_documented_reason():
    record("instance_terminalized", instance_id="i1")
    entry = snapshot()["kinds"]["instance_terminalized"]
    assert entry["lag_seconds"] is None
    assert entry["lag_measurable"] is False
    assert entry["lag_reason"]
    assert "fabricat" in entry["lag_reason"].lower() or "no timestamp" in entry["lag_reason"].lower()


def test_attestation_lag_is_none_observability_only_kind():
    record("attestation_epoch", epoch_action="deny_increment")
    entry = snapshot()["kinds"]["attestation_epoch"]
    assert entry["lag_seconds"] is None
    assert entry["lag_measurable"] is False


def test_deferred_kind_snapshot_entry_is_labeled():
    entry = snapshot()["kinds"]["turn_transition"]
    assert entry["deferred_v2"] is True
    assert entry["fired_total"] == 0


def test_bridge_pending_disclosed_only_for_the_event_bus_family():
    snap = snapshot()
    for kind_id, entry in snap["kinds"].items():
        if kind_id.startswith("event_bus:"):
            assert entry["bridge_pending"], kind_id
            assert "ownership map" in entry["bridge_pending"]
        else:
            assert entry["bridge_pending"] is None, kind_id


# ── Snapshot never raises ────────────────────────────────────────────────


def test_snapshot_survives_a_hostile_bus():
    class HostileBus:
        @property
        def _global_subscribers(self):
            raise RuntimeError("boom")

    snap = snapshot(bus=HostileBus())
    assert "kinds" in snap  # the duck-type guard degraded to unverifiable
