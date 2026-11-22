"""P5 pack — the kind catalog (T5.1, AC1 / AC5 / AC9 / F-17 / D5.9-D5.10).

Mutation legs in this file (each asserts the INVARIANT whose violation is
the named mutation, so flipping the code turns the leg RED):

- ``test_validate_refuses_duplicate_kind_id`` — a duplicated kind_id is
  flagged (mutation: remove the duplicate check ⇒ RED).
- ``test_validate_refuses_missing_posture`` — a missing posture is
  flagged (mutation: remove the posture check ⇒ RED).
- ``test_posture_labels_are_exact`` (AC5) — the shipped posture map is
  pinned field-by-field (mutation: flip one label in catalog.py ⇒ RED).
- ``test_record_refuses_unregistered_kind`` (emit.py) — also here in
  spirit; the emit-side legs live in test_emit.py.
"""

from __future__ import annotations

import dataclasses

from daemon.repositories.event.models import EventKind
from daemon.services.event_registry import (
    BRIDGE_PENDING,
    CATALOG,
    DEFERRED_V2_KINDS,
    KIND_INDEX,
    RETIRED_EVENT_KINDS,
    SEAM_DEPTH_TRIGGERS,
    EventClass,
    FailurePosture,
    KindSpec,
    validate_catalog,
)


# ── AC1 — catalog completeness ──────────────────────────────────────────


def test_shipped_catalog_is_valid():
    assert validate_catalog() == []


def test_every_v1_kind_carries_the_self_describing_fields():
    # D5.9 addendum parity with JobSpec/ServiceMeta.
    required = (
        "kind_id",
        "klass",
        "service_tag",
        "module_info",
        "description",
        "posture",
        "consumers",
        "metadata",
    )
    for spec in CATALOG:
        for name in required:
            assert getattr(spec, name) is not None, (spec.kind_id, name)
        assert isinstance(spec.kind_id, str) and spec.kind_id
        assert isinstance(spec.consumers, tuple)
        assert isinstance(spec.metadata, dict)


def test_catalog_is_a_frozen_tuple_of_frozen_specs():
    assert isinstance(CATALOG, tuple)
    for spec in CATALOG:
        assert dataclasses.is_dataclass(spec)
        # frozen dataclass: mutation must raise.
        try:
            spec.kind_id = "mutated"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError(f"KindSpec is not frozen: {spec.kind_id}")


def test_event_bus_family_is_kind_parameterized_and_derived():
    # One registry kind per EventKind member, auto-derived (F-17: the
    # catalog — not the stale SQLite CHECK — is the identity), PLUS the
    # one non-enum repo-direct durable family member (task_recovery).
    enum_family = [
        k for k in CATALOG
        if k.kind_id.startswith("event_bus:")
        and not k.metadata.get("non_enum")
    ]
    assert len(enum_family) == len(EventKind)
    expected = {f"event_bus:{ek.value}" for ek in EventKind}
    got = {k.kind_id for k in enum_family}
    assert got == expected
    for spec in enum_family:
        assert spec.klass is EventClass.DURABLE_BUS
        assert spec.service_tag == "event_bus"
    non_enum = [k for k in CATALOG if k.metadata.get("non_enum")]
    assert [k.kind_id for k in non_enum] == ["event_bus:task_recovery"]
    assert non_enum[0].klass is EventClass.DURABLE_BUS


# ── Mutation leg: duplicate kind_id ⇒ RED ───────────────────────────────


def test_validate_refuses_duplicate_kind_id():
    dup = dataclasses.replace(CATALOG[0])
    bad_catalog = CATALOG + (dup,)
    defects = validate_catalog(bad_catalog)
    assert defects, "validate_catalog missed a duplicate kind_id"
    assert any("duplicate kind_id" in d for d in defects)
    assert any(CATALOG[0].kind_id in d for d in defects)


# ── Mutation leg: missing posture ⇒ RED ─────────────────────────────────


def test_validate_refuses_missing_posture():
    broken = KindSpec(
        kind_id="broken_kind",
        klass=EventClass.TERMINALIZATION,
        service_tag="s",
        description="d",
        posture=None,  # type: ignore[arg-type]
    )
    defects = validate_catalog((broken,))
    assert defects, "validate_catalog missed a missing posture"
    assert any("posture" in d for d in defects)


def test_validate_refuses_class5_entry_without_deferred_marker():
    broken = KindSpec(
        kind_id="turn_transition_rogue",
        klass=EventClass.TURN_TRANSITION,
        service_tag="s",
        description="d",
        posture=FailurePosture.FAIL_MISS,
        metadata={"lag": {"measurable": False}},
    )
    defects = validate_catalog((broken,))
    assert any("deferred_v2" in d for d in defects)


def test_every_kind_declares_the_lag_honesty_metadata():
    for spec in CATALOG:
        lag = spec.metadata.get("lag")
        assert isinstance(lag, dict) and "measurable" in lag, spec.kind_id


# ── AC5 — posture labels are exact (mutation: flip one ⇒ RED) ───────────


def test_posture_labels_are_exact():
    expected = {
        "instance_terminalized": FailurePosture.FAIL_MISS,
        "report_injection_obligation": FailurePosture.FAIL_MISS,
        "mission_commission_transition": FailurePosture.FAIL_MISS,
        "attestation_epoch": FailurePosture.FAIL_OPEN,
    }
    got = {
        kind: KIND_INDEX[kind].posture
        for kind in expected
    }
    assert got == expected
    # The event_bus family: the DURABLE row is fail-miss truth; the
    # in-proc fan-out facet is labeled drop-counted in metadata (AC5's
    # "in-proc fan-out is DROP_COUNTED").
    for spec in CATALOG:
        if spec.kind_id.startswith("event_bus:"):
            assert spec.posture is FailurePosture.FAIL_MISS
            assert spec.metadata["fanout_posture"] == "DROP_COUNTED"


# ── AC9 — the class-5 deferral is loud and honest ────────────────────────


def test_class5_turn_transition_entry_is_deferred_v2():
    spec = KIND_INDEX["turn_transition"]
    assert spec.metadata["deferred_v2"] is True
    assert spec.metadata["gap"] == "GAP-13"
    anchors = " ".join(spec.metadata["anchors"])
    assert "instance_lifecycle.py" in anchors
    assert "turn_transition" in DEFERRED_V2_KINDS
    assert spec.klass is EventClass.TURN_TRANSITION


def test_seam_depth_triggers_note_names_gap14_and_ev24():
    # D5.10: the queue-depth seam is NAMED, not built.
    assert "GAP-14" in SEAM_DEPTH_TRIGGERS
    assert "EV-24" in SEAM_DEPTH_TRIGGERS
    assert "instance_messaging" in SEAM_DEPTH_TRIGGERS


def test_no_bridge_pending_kind_is_actually_bridged():
    # BRIDGE_PENDING may only hold kinds with a pinned L-11 reason
    # (declared-silent). A kind that IS in the census must never sit in
    # the register (lockstep both ways is enforced in test_bridges.py;
    # this leg pins the reason vocabulary + the terminal-state counts).
    from daemon.services.event_registry import DECLARED_SILENT_KINDS

    for kind_id, reason in BRIDGE_PENDING.items():
        assert kind_id in KIND_INDEX, kind_id
        # L-11: the ONLY surviving register class is declared-silent;
        # every reason cites the ruling.
        assert reason.startswith("L-11 declared-silent:"), (kind_id, reason)
    from daemon.services.event_registry import DECLARED_SILENT_KINDS

    assert DECLARED_SILENT_KINDS == {
        k for k, r in BRIDGE_PENDING.items()
        if r.startswith("L-11 declared-silent:")
    }
    # The declared-silent set is empty after L-11 retirement.
    assert DECLARED_SILENT_KINDS == set()
    # Terminal-state contract: 24 kinds = 23 bridged + 0 declared-silent
    # + 1 deferred_v2 (L-11 retirement).
    assert len(CATALOG) == 24
    assert len(BRIDGE_PENDING) == 0
    assert len(DEFERRED_V2_KINDS) == 1


# ── L-11 retirement ledger ───────────────────────────────────────────────


_RETIRED_SIX = {
    "event_bus:processing_started",
    "event_bus:processing_completed",
    "event_bus:processing_failed",
    "event_bus:child_failed",
    "event_bus:message_completed",
    "event_bus:room_post",
}


def test_retired_ledger_carries_exactly_the_six_retired_kinds():
    assert set(RETIRED_EVENT_KINDS) == _RETIRED_SIX


def test_retired_ledger_rows_are_well_formed():
    for kind_id, row in RETIRED_EVENT_KINDS.items():
        assert isinstance(row, dict), kind_id
        for field_name in ("reason", "retired_at", "revive_procedure"):
            assert field_name in row, (kind_id, field_name)
            assert isinstance(row[field_name], str) and row[field_name].strip(), (
                kind_id, field_name
            )


def test_retired_kinds_are_not_simultaneously_registered():
    assert set(RETIRED_EVENT_KINDS) & set(KIND_INDEX) == set()
