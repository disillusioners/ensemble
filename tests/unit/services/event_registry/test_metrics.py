"""P5 pack — the observability projection (T5.7, AC6 / D5.4 / D5.5 / D5.12)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

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
            "declared_silent",
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


def test_kinds_with_no_live_source_report_all_consumers_unverifiable():
    # Kinds whose declared/namespace sources are not implemented (the
    # class-3 rdrs:* substrate; attestation_epoch; the deferred_v2 entry)
    # keep every consumer at live=None ("cannot verify" != "absent").
    # instance_terminalized used to be in this set; it now carries the
    # dependency_bus source (see
    # test_instance_terminalized_verifies_its_dependency_bus_lane).
    entry = snapshot(bus=_StubBus(("job_feedback_observer",)))[
        "kinds"]["report_injection_obligation"]
    assert entry["consumers_live"] == [
        {"consumer": "rdrs:sweep_lanes_1_3_4_5", "live": None}
    ]


def test_live_source_dispatch_covers_exactly_the_event_bus_source():
    # D5.4 dispatch pin (P7 item-9): the IMPLEMENTED live-source
    # dispatch set is EXACTLY {"event_bus"} — the only extractor is
    # _live_subscriber_ids duck-typed on the bus's _global_subscribers,
    # so any OTHER source routed through it would be a LIE (a dispatch
    # branch without a real extractor). The catalog's second substrate
    # (dependency-watchers, e.g. dependency_bus:child_keyed_waiters) has
    # NO live source today — landing it is the DELIBERATE, NAMED
    # follow-up (D5.4's second live source) and must re-point this pin
    # AND test_non_bus_kinds_have_no_live_source together.
    #
    # Leg 1 (behavioral, probe spec): a kind declaring a non-bus source
    # must be reported UNVERIFIED (live=None) even with a bus in hand.
    # A silent half-implementation — `or live_source ==
    # "dependency_watchers"` in the dispatch, or any branch that
    # "verifies" a non-bus source against the bus's subscriber set —
    # yields a real verdict (True/False) instead of None ⇒ RED.
    from daemon.services.event_registry.metrics import _consumers_live

    probe = SimpleNamespace(
        kind_id="event_registry:test_probe_non_bus_source",
        consumers=("dependency_bus:child_keyed_waiters",),
        metadata={"live_source": "dependency_watchers"},
    )
    entries, verified, _ = _consumers_live(
        probe, _StubBus(("job_feedback_observer",))
    )
    assert verified is False, (
        "the dispatch verified a non-event_bus live source against the "
        "bus's subscriber set — a dispatch branch without a real "
        "extractor (D5.4 lie); re-point deliberately when the real "
        "second live source lands"
    )
    assert entries == [
        {"consumer": "dependency_bus:child_keyed_waiters", "live": None}
    ]
    # Leg 2 (catalog-declaration fence): the catalog must not declare a
    # live source the dispatch does not implement — declaration without
    # implementation is the other half-implementation shape. The IMPLEMENTED
    # dispatch set is now exactly {"event_bus", "dependency_bus"} (the
    # second live source — D5.4 "the dependency bus's lane names" — landed
    # this round: daemon/services/event_registry/metrics.py
    # `_live_dependency_bus_lanes`). The catalog declares exactly those
    # two (event_bus family + instance_terminalized); a THIRD declared
    # source REDs here (the fence guards the frontier, not the status
    # quo).
    from daemon.services.event_registry import CATALOG, LIVE_SOURCES

    declared = {
        (spec.metadata or {}).get("live_source") for spec in CATALOG
    } - {None}
    assert declared == set(LIVE_SOURCES), (
        "the catalog's declared live sources do not match the "
        "implemented dispatch set — the second live source has been "
        f"mis-declared or a third source slipped in: declared={sorted(declared)} "
        f"implemented={sorted(LIVE_SOURCES)}"
    )
    assert set(LIVE_SOURCES) == {"event_bus", "dependency_bus"}, (
        "the IMPLEMENTED dispatch set drifted from the declared shape: "
        "a third source must NOT be added without a real extractor"
    )


# ── D5.4 SECOND LIVE SOURCE — extractor + per-kind verification ────────


def test_live_dependency_bus_lanes_extracts_armed_arm_lanes():
    # The dependency bus's live LANE names = the arm-lane tags of the
    # currently ARMED watchers (the bus's in-RAM `_pending` cache — the
    # same RAM-only read the event-bus extractor does). The two
    # production arm lanes stamp metadata["kind"] = "child_complete"
    # (spawn/send_message) / "nudge_complete" (nudge) — bus docstring
    # names both arm lanes (dependency_bus.py:719-724).
    from daemon.services.event_registry.metrics import (
        _live_dependency_bus_lanes,
    )

    class _Bus:
        _pending = {
            "src-a": [SimpleNamespace(metadata={"kind": "child_complete"})],
            "src-b": [
                SimpleNamespace(metadata={"kind": "nudge_complete"}),
                SimpleNamespace(metadata={"kind": "child_complete"}),
            ],
        }

    assert _live_dependency_bus_lanes(_Bus()) == {
        "child_complete", "nudge_complete"
    }
    # An empty (but real) armed set is a VERIFIED empty lane population
    # — the extractor returns the empty set, NOT None.
    class _EmptyBus:
        _pending = {}

    assert _live_dependency_bus_lanes(_EmptyBus()) == set()
    # No lane surface at all => "cannot verify" (None), never "absent".
    assert _live_dependency_bus_lanes(None) is None
    assert _live_dependency_bus_lanes(SimpleNamespace()) is None
    # A duck-typed explicit lane-name surface wins over the derived one.
    class _ExplicitBus:
        _lane_names = ("alpha", "beta")

    assert _live_dependency_bus_lanes(_ExplicitBus()) == {"alpha", "beta"}


def test_live_dependency_bus_lanes_survives_stubs_without_metadata():
    from daemon.services.event_registry.metrics import (
        _live_dependency_bus_lanes,
    )

    class _Bus:
        _pending = {"src": [SimpleNamespace(metadata=None), None]}

    assert _live_dependency_bus_lanes(_Bus()) == set()


def test_instance_terminalized_verifies_its_dependency_bus_lane(caplog):
    class _StubDependencyBus:
        _pending = {
            "src-1": [SimpleNamespace(metadata={"kind": "child_complete"})],
        }

    # With a dependency bus, the dependency_bus arm lanes verify; the
    # report_lane consumer has no extractor -> live=None ("cannot
    # verify", honest). Reset before ANY snapshot so the warning-count
    # assertion below is not polluted by an earlier snapshot's record.
    reset_warning_state_for_tests()
    import logging as _logging

    with caplog.at_level(_logging.WARNING):
        entry = snapshot(
            bus=_StubBus(()), dependency_bus=_StubDependencyBus()
        )["kinds"]["instance_terminalized"]
        assert entry["consumers_live"] == [
            {"consumer": "dependency_bus:child_complete", "live": True},
            {"consumer": "dependency_bus:nudge_complete", "live": False},
            {"consumer": "report_lane:structural_parent", "live": None},
        ]
        warnings = [
            r for r in caplog.records
            if "ABSENT from the live subscriber set" in r.getMessage()
            and "instance_terminalized" in r.getMessage()
        ]
        assert len(warnings) == 1


def test_third_unimplemented_source_still_unverifiable():
    # The fence keeps guarding the frontier, not the status quo: a kind
    # declaring a THIRD (unimplemented) source must report UNVERIFIED
    # (live=None, verified=False) even with a bus in hand.
    from daemon.services.event_registry.metrics import _consumers_live

    probe = SimpleNamespace(
        kind_id="event_registry:test_probe_third_source",
        consumers=("rdrs:some_lane",),
        metadata={"live_source": "rdrs"},
    )
    entries, verified, _ = _consumers_live(
        probe, _StubBus(("job_feedback_observer",))
    )
    assert verified is False
    assert entries == [{"consumer": "rdrs:some_lane", "live": None}]



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


def test_bridge_pending_disclosed_only_for_registered_pending_kinds():
    from daemon.services.event_registry import BRIDGE_PENDING

    snap = snapshot()
    for kind_id, entry in snap["kinds"].items():
        if kind_id in BRIDGE_PENDING:
            assert entry["bridge_pending"] == BRIDGE_PENDING[kind_id], kind_id
        else:
            assert entry["bridge_pending"] is None, kind_id
    # Every bridged kind reports no pending marker.
    for bridged in (
        "instance_terminalized",
        "report_injection_obligation",
        "mission_commission_transition",
        "attestation_epoch",
        "event_bus:job_completed",
        "event_bus:question_answered",
        "event_bus:task_recovery",
    ):
        assert snap["kinds"][bridged]["bridge_pending"] is None, bridged


# ── Snapshot never raises ────────────────────────────────────────────────


def test_snapshot_survives_a_hostile_bus():
    class HostileBus:
        @property
        def _global_subscribers(self):
            raise RuntimeError("boom")

    snap = snapshot(bus=HostileBus())
    assert "kinds" in snap  # the duck-type guard degraded to unverifiable
