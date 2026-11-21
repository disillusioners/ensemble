"""Event registry v1 — the kind catalog (T5.1).

Phase 5 of the Engine Arc Phase-1 plan (``.agents/shared/planning/engine-arc-phase1/phase5-plan.md``).
The registry delivers Emperor ruling #3 literally: **a first-class registry of
event KINDS** — every emission site registers its kind centrally, and the
registry answers *what fired, how often, who consumes it, how far behind a
consumer is*. Phase-1 boundary: **REGISTRATION + OBSERVABILITY ONLY** —
nothing consumes a kind to trigger work.

Identity rule (F-17): kind identity is THIS catalog (code), never the stale
SQLite ``event.kind`` CHECK (which lists only 8 of the current ``EventKind``
members). The registry never relies on that CHECK and never claims a kind is
"persisted and validated" on SQLite.

The v1 catalog covers classes 1-4 over substrates that EXIST at this tree,
plus the class-5 ``turn_transition`` entry as an explicit ``deferred_v2``
pointer (F-18: the dead outbox is a HARD anti-target — the entry is a
pointer, not a plan; any future plant is a separate commission).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping

from daemon.repositories.event.models import EventKind

# ---------------------------------------------------------------------------
# Enums (D5.8: the per-class failure posture is LABELED, never inherited)
# ---------------------------------------------------------------------------


class EventClass(enum.IntEnum):
    """The five event classes of the census (§B.3 / phase5-plan.md §3.1)."""

    TERMINALIZATION = 1
    DURABLE_BUS = 2
    STATE_TABLE = 3
    ATTESTATION = 4
    TURN_TRANSITION = 5  # deferred_v2 — the dead outbox class


class FailurePosture(str, enum.Enum):
    """Per-kind failure posture — a LABEL a reviewer can read (D5.8).

    ``FAIL_MISS`` — a missed emission strands a waiter / loses an
    obligation (terminalization, state-table obligations).
    ``FAIL_OPEN`` — the emitter's own failure posture is fail-open; the
    registry must LABEL it, never silently inherit it (attestation).
    ``DROP_COUNTED`` — an in-proc fan-out whose overflow is a counted
    drop (the registry's own ring; the durable-bus subscriber queues).
    """

    FAIL_MISS = "FAIL_MISS"
    FAIL_OPEN = "FAIL_OPEN"
    DROP_COUNTED = "DROP_COUNTED"


# ---------------------------------------------------------------------------
# KindSpec (D5.9: self-describing exactly like JobSpec's ServiceMeta)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KindSpec:
    """One registered event kind — the self-describing catalog entry.

    Addendum parity with ``JobSpec``/``ServiceMeta`` (daemon/services/engine/spec.py):
    ``kind_id``, ``class``, ``service_tag`` (the emitting service),
    ``module_info``, ``description``, ``posture``, ``consumers``,
    ``metadata``. "A kind the maintenancer cannot attribute to a service
    is not queryable."
    """

    kind_id: str
    klass: EventClass
    service_tag: str
    description: str
    posture: FailurePosture
    consumers: tuple[str, ...] = ()
    module_info: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# v1 catalog — classes 1-4 (+ the deferred_v2 class-5 pointer)
# ---------------------------------------------------------------------------


_EVENT_BUS_FAMILY_NOTE = (
    "kind-parameterized durable bus family: one registry kind per "
    "EventKind emission (event_bus:<EventKind.value>). The durable event "
    "row IS the truth; the in-proc subscriber fan-out is drop-counted "
    "(queue maxsize, event_bus.py:251/:289-303)."
)


def _event_bus_family() -> tuple[KindSpec, ...]:
    """Derive one KindSpec per EventKind member (auto-tracking, no drift).

    The family is DERIVED from the code enum at import time (F-17: the
    catalog is the identity; deriving from the enum keeps the two from
    drifting — a new EventKind member is automatically a registered kind).
    """
    return tuple(
        KindSpec(
            kind_id=f"event_bus:{ek.value}",
            klass=EventClass.DURABLE_BUS,
            service_tag="event_bus",
            module_info="daemon/services/event_bus.py (create_event :155; "
            "kind identity from daemon/repositories/event/models.py)",
            description=f"Durable bus event kind '{ek.value}' "
            f"({ek.name}) — persisted as an ``event`` row, then "
            f"fan-out to global subscribers. {_EVENT_BUS_FAMILY_NOTE}",
            posture=FailurePosture.FAIL_MISS,
            consumers=("job_feedback_observer",),
            metadata={
                "substrate": "event table + EventBus._global_subscribers",
                "live_source": "event_bus",
                "lag": {
                    "measurable": True,
                    "reason": "the durable row carries created_at; the "
                    "subscriber queue has an enqueue time (D5.12)",
                },
                "fanout_posture": FailurePosture.DROP_COUNTED.value,
                "kind_parameterized": True,
            },
        )
        for ek in EventKind
    )


CATALOG: tuple[KindSpec, ...] = (
    # ── Class 1 — TERMINALIZATION (FAIL-MISS: a missed fire strands a
    #    waiter — the S1 defect class, closed at source v0.31.22) ──
    KindSpec(
        kind_id="instance_terminalized",
        klass=EventClass.TERMINALIZATION,
        service_tag="child_reports",
        module_info="daemon/services/child_reports.py "
        "(_fire_terminal_child_and_deliver, def :741) — the S1 fire+deliver "
        "wrapper ALL terminalization lanes converge on "
        "(child_reports :6444/:6643/:6945 + error_reporting :693); "
        "substrate daemon/services/dependency_bus.py:1921",
        description="A child instance reached a terminal transition and "
        "the target-agnostic child-keyed watcher fire ran (durable row + "
        "guarded CAS transition_state). Observed AFTER the fire's CAS "
        "commit, never inside it (F-19: the bridge adds no second fire, "
        "no second CAS, no parallel probe).",
        posture=FailurePosture.FAIL_MISS,
        consumers=(
            "dependency_bus:child_keyed_waiters",
            "report_lane:structural_parent",
        ),
        metadata={
            "substrate": "dependency_watchers via "
            "DependencyBus.fire_for_terminal_child",
            "cas": "guarded WHERE state='PENDING' UPDATE (exactly-once)",
            "lag": {
                "measurable": False,
                "reason": "no timestamp pair exists on this substrate at "
                "this tree — terminalization lag is NEVER fabricated "
                "(D5.12 / AC7; the boot-telemetry wrong-clock-frame trap)",
            },
            "f19_fence": "return-value observation only (list[FollowUp])",
        },
    ),
    # ── Class 2 — DURABLE BUS EVENTS (derived family; see above) ──
    *_event_bus_family(),
    # A NON-enum durable kind written via EventRepository directly (not
    # through EventBus.create_event) — bridged at its single producer,
    # stale_task_recovery._log_recovery_event (the row kind is dynamic:
    # ``task_recovery_<action>``; the variant rides the ``action`` dim).
    KindSpec(
        kind_id="event_bus:task_recovery",
        klass=EventClass.DURABLE_BUS,
        service_tag="stale_task_recovery",
        module_info="daemon/services/stale_task_recovery.py "
        "(_log_recovery_event :934 — EventRepository.create_event direct, "
        "no EventBus)",
        description="Repo-direct durable recovery-audit row "
        "(kind=task_recovery_<action>; the recovered task's ids + "
        "retry metadata). NOT an EventKind enum member — catalogued so "
        "its single producer is observable like the event_bus family.",
        posture=FailurePosture.FAIL_MISS,
        consumers=(),
        metadata={
            "substrate": "event table, written via EventRepository",
            "non_enum": True,
            "kind_variant_dim": "action",
            "lag": {
                "measurable": True,
                "reason": "the durable row carries created_at (D5.12 "
                "event_bus frame)",
            },
            "fanout_posture": FailurePosture.DROP_COUNTED.value,
        },
    ),
    # ── Class 3 — STATE-TABLE TRIGGERS (FAIL-MISS: a durable obligation
    #    nobody observes IS the RDRS failure class) ──
    KindSpec(
        kind_id="report_injection_obligation",
        klass=EventClass.STATE_TABLE,
        service_tag="report_delivery_recovery",
        module_info="daemon/services/report_delivery_recovery.py "
        "(RDRS sweep lane reads: _process_deferred_rows lanes 1/5, "
        "_run_pending_age_lane lanes 3/4); substrate "
        "daemon/repositories/report_injection/models.py:124 "
        "(states :83-90, partial unique uq_report_injections_oblig_triple)",
        description="A report-delivery obligation row (PENDING/DEFERRED) "
        "was OBSERVED by an RDRS sweep lane read. Observation-only: no "
        "write is added to the state machine (T5.5).",
        posture=FailurePosture.FAIL_MISS,
        consumers=("rdrs:sweep_lanes_1_3_4_5",),
        metadata={
            "substrate": "report_injections table",
            "states": ("PENDING", "DEFERRED"),
            "lag": {
                "measurable": True,
                "reason": "row created_at vs the observation clock at the "
                "lane-read seam (D5.12 state-table frame)",
            },
        },
    ),
    KindSpec(
        kind_id="mission_commission_transition",
        klass=EventClass.STATE_TABLE,
        service_tag="report_delivery_recovery",
        module_info="daemon/services/report_delivery_recovery.py "
        "(lane-7 mutation seam: _abandon_commission / "
        "_satisfy_commission_on_evidence, observed AFTER the guarded "
        "service write returns); substrate "
        "daemon/repositories/mission_record/models.py:372",
        description="An outstanding mission_commissions row transitioned "
        "(abandoned / satisfied-on-evidence) by the RDRS lane-7 guarded "
        "writer. Observation-only, after the mutation commits (T5.5).",
        posture=FailurePosture.FAIL_MISS,
        consumers=("rdrs:lane_7_mission_reconciliation",),
        metadata={
            "substrate": "mission_commissions table",
            "transitions": (
                "abandoned",
                "satisfied_on_evidence",
            ),
            "lag": {
                "measurable": True,
                "reason": "row created_at vs the observation clock at the "
                "mutation seam (D5.12 state-table frame)",
            },
        },
    ),
    # ── Class 4 — ATTESTATION EPOCHS (the ledger's own posture is
    #    fail-open; the registry LABELS it, never inherits it) ──
    KindSpec(
        kind_id="attestation_epoch",
        klass=EventClass.ATTESTATION,
        service_tag="attestation_ledger",
        module_info="daemon/services/attestation_ledger.py "
        "(AttestationLedger Protocol :101-131; _log_db_error :137+ C3 "
        "fail-open) — OBSERVED AT CALLERS, the ledger itself is NOT "
        "touched: daemon/graph.py completion gate "
        "(safe_increment/safe_set_escalated_and_reset/safe_reset) + "
        "daemon/services/child_reports.py FP4-L6 sites",
        description="An attestation epoch mutated the denial ledger: "
        "deny_increment / escalated_and_reset / reset. The ledger's own "
        "posture is fail-OPEN (a DB error never blocks the gate) — the "
        "label is data, not inherited behavior (D5.8).",
        posture=FailurePosture.FAIL_OPEN,
        consumers=(),
        metadata={
            "substrate": "instances denial-epoch columns + JSONB epochs",
            "fail_open": True,
            "epoch_actions": (
                "deny_increment",
                "escalated_and_reset",
                "reset",
            ),
            "lag": {
                "measurable": False,
                "reason": "no consumer lag concept on this substrate "
                "(observability-only kind; no consumer exists in v1)",
            },
        },
    ),
    # ── Class 5 — TURN TRANSITION (deferred_v2 — a POINTER, not a plan) ──
    KindSpec(
        kind_id="turn_transition",
        klass=EventClass.TURN_TRANSITION,
        service_tag="instance_lifecycle",
        module_info="daemon/services/turn_transitions.py:32-42 "
        "(payloads :135-755); consumers still DEBUG-only at HEAD: "
        "daemon/services/instance_lifecycle.py:5976 (pause) and :6589 "
        "(resume) — GAP-13 verified intact",
        description="DEFERRED_V2 (the coding councilor's split ruling): "
        "the turn-transition outbox is DEAD — it produces named "
        "transition payloads with no transport (GAP-13). This entry is a "
        "pointer so the next phase starts from a named position; F-18 "
        "forbids reviving the outbox as part of Phase 5 and record() "
        "REFUSES this kind while its metadata is deferred_v2.",
        posture=FailurePosture.FAIL_MISS,
        consumers=(),
        metadata={
            "deferred_v2": True,
            "gap": "GAP-13",
            "anchors": (
                "daemon/services/instance_lifecycle.py:5976",
                "daemon/services/instance_lifecycle.py:6589",
            ),
            "split_ruling": "the hottest path in the daemon; v2 only",
            "lag": {
                "measurable": False,
                "reason": "substrate not built (deferred_v2)",
            },
        },
    ),
)

# Kind-parameterized family helpers (event_bus:<EventKind>)
EVENT_BUS_PREFIX = "event_bus:"


def is_event_bus_kind(kind_id: str) -> bool:
    return kind_id.startswith(EVENT_BUS_PREFIX)


def event_bus_kind_id(event_kind_value: str) -> str:
    """The registry kind id for a durable EventBus EventKind emission."""
    return f"{EVENT_BUS_PREFIX}{event_kind_value}"


# ---------------------------------------------------------------------------
# D5.10 — the queue-depth seam is NAMED, not built
# ---------------------------------------------------------------------------

SEAM_DEPTH_TRIGGERS: str = (
    "GAP-14: queue-depth triggers are NOT deliverable in v1 — no depth "
    "plumbing exists anywhere in the tree; every depth signal is a "
    "pull-only SQL count (census Card 8: resources.py:935-951, "
    "queue_repository.py:322, manager.py:13596). The named future PLANT "
    "POINT is the durable enqueue seam EV-24 "
    "(daemon/services/instance_messaging.py:2980 — MessageQueue+Task+"
    "JobItem creation; the depth-increment moment that fires nothing "
    "today). Build a depth trigger ONLY when a consumer exists (the "
    "study's anti-pattern pricing); Phase 5 names the seam and builds "
    "nothing on it."
)

# ---------------------------------------------------------------------------
# Validation (AC1: the catalog refuses duplicate kind_id / missing posture)
# ---------------------------------------------------------------------------


def validate_catalog(catalog: tuple[KindSpec, ...] = CATALOG) -> list[str]:
    """Validate a catalog tuple; return the list of defects (empty = clean).

    Refuses: a duplicate ``kind_id``; a missing/invalid ``posture``; an
    entry missing any self-describing field (D5.9 addendum parity); a
    class-5 entry without an explicit ``deferred_v2`` marker (AC9: the
    deferral must be loud).
    """
    defects: list[str] = []
    seen: set[str] = set()
    required_fields = (
        "kind_id",
        "klass",
        "service_tag",
        "module_info",
        "description",
        "posture",
        "consumers",
        "metadata",
    )
    for spec in catalog:
        if spec.kind_id in seen:
            defects.append(f"duplicate kind_id: {spec.kind_id!r}")
            continue
        seen.add(spec.kind_id)
        if not isinstance(spec.posture, FailurePosture):
            defects.append(
                f"kind {spec.kind_id!r}: missing/invalid posture "
                f"({spec.posture!r})"
            )
        for name in required_fields:
            if getattr(spec, name, None) is None:
                defects.append(f"kind {spec.kind_id!r}: missing {name}")
        if not spec.description:
            defects.append(f"kind {spec.kind_id!r}: empty description")
        lag_meta = spec.metadata.get("lag")
        if not isinstance(lag_meta, Mapping) or "measurable" not in lag_meta:
            defects.append(
                f"kind {spec.kind_id!r}: metadata.lag must declare "
                f"measurable (D5.12 lag honesty)"
            )
        if spec.klass is EventClass.TURN_TRANSITION and not spec.metadata.get(
            "deferred_v2"
        ):
            defects.append(
                f"kind {spec.kind_id!r}: class-5 entries must carry the "
                f"deferred_v2 marker (F-18)"
            )
    return defects


def assert_catalog() -> None:
    """Raise if the shipped catalog is invalid (import-time contract)."""
    defects = validate_catalog()
    if defects:
        raise ValueError(
            "event_registry catalog invalid: " + "; ".join(defects)
        )


KIND_INDEX: dict[str, KindSpec] = {spec.kind_id: spec for spec in CATALOG}

# Kinds whose substrate does not exist yet — record() refuses them while
# the deferred_v2 marker stands (F-18: the outbox is a hard anti-target).
DEFERRED_V2_KINDS: frozenset[str] = frozenset(
    spec.kind_id
    for spec in CATALOG
    if spec.metadata.get("deferred_v2")
)

# Kinds with NO bridge — every entry carries its precise reason. After
# leader rulings L-10 (bridge the producer-outside-map kinds) and L-11
# (the zero-producer kinds stay DECLARED-SILENT), the register holds
# exactly ONE class:
#
#  (1) ZERO-PRODUCER enum members (L-11) — declared in EventKind but
#      nothing at this tree writes an event row of that kind. The
#      registry cannot bridge a site that does not exist; a synthetic
#      emission would fabricate telemetry (the same honesty discipline
#      as D5.12's lag rule). Enum retirement is a SEPARATE future
#      commission — not P5's.
#
# The AC2 census leg asserts: every bridged kind is NOT in this register,
# and every catalog kind is either bridged or present here with the
# L-11-cited reason (LOCKSTEP both ways — a bridge for a declared-silent
# kind REDs).
_ZERO_PRODUCER_REASON = (
    "L-11 declared-silent: no daemon emission site exists at this tree "
    "(the EventKind member is declared but nothing writes it); a "
    "synthetic emission would fabricate telemetry (enum retirement is a "
    "separate future commission)"
)

BRIDGE_PENDING: dict[str, str] = {
    "event_bus:processing_started": _ZERO_PRODUCER_REASON,
    "event_bus:processing_completed": _ZERO_PRODUCER_REASON,
    "event_bus:processing_failed": _ZERO_PRODUCER_REASON,
    "event_bus:child_failed": (
        _ZERO_PRODUCER_REASON
        + " (error_reporting's 'child_failed' is an SSE "
        "stream_lifecycle event_type, not an event-row kind)"
    ),
    "event_bus:message_completed": _ZERO_PRODUCER_REASON,
    "event_bus:room_post": (
        _ZERO_PRODUCER_REASON
        + " (room posts ride room_tag_delivery / room_proposal rows; "
        "ROOM_POST has no row producer)"
    ),
}

# The declared-silent subset of the register (L-11). Kept as a distinct
# class so the census and snapshot() can label it.
DECLARED_SILENT_KINDS: frozenset[str] = frozenset(
    kind for kind, reason in BRIDGE_PENDING.items()
    if reason.startswith("L-11 declared-silent:")
)

# Back-compat alias (the pre-L-11 name; same set).
ZERO_PRODUCER_KINDS: frozenset[str] = DECLARED_SILENT_KINDS


assert_catalog()
