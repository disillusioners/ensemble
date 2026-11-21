"""Frozen registration contract for the engine substrate (Engine Arc P1).

Owner: T1.7 + T1.10 of
``.agents/shared/planning/engine-arc-phase1/phase1-plan.md``.

This module is the CONTRACT surface P2 (execution core), P3 (guardrails +
dual-path) and P5 (event registry) build against. It is deliberately
data-only:

* :class:`ServiceMeta` — the Emperor addendum's self-describing metadata
  (2026-11-21): ``service_tag`` (stable ops id; metrics label),
  ``service_name``, ``category`` (open vocabulary), ``module_info``
  (origin module path), ``description``, and an extensible ``metadata``
  mapping. It travels into the durable ``engine_jobs`` row and out
  through every observability projection downstream.
* :class:`IntervalTrigger` / :class:`EventTrigger` — the two trigger
  shapes. EXACTLY ONE per job (``validate_spec`` enforces it).
* :class:`JobSpec` — the frozen registration record (frozen design:
  ``central-polling-engine-study/decision-report.md`` section B.2,
  lines 110-131).
* :class:`EngineSpecError` — the typed validation error; ONE code per
  rule so a rejected registration names its own rule.
* :func:`validate_spec` — the 9 validation rules from D1.12.

**T1.10 — the TriggerSpec seam (why this is frozen now).** P2 and P5 must
be able to run in parallel on disjoint trees
(``daemon/services/engine/`` vs ``daemon/services/event_registry/``).
The split is:

* P1 (here) freezes :class:`EventTrigger` as a pure DATA spec — a
  ``(kind, filter)`` pair. It carries no callback, no import of anything
  from the event registry, and no import of anything from the execution
  core.
* P2's execution core exposes the registration seam
  ``on_event_kind(kind: str) -> callback`` — i.e. it maintains a mapping
  from an event-kind string to the callable the engine should run when
  that kind is observed. P5 registers kinds through the event registry's
  own ``set_event_sink(cb)`` (phase5 T5.8) and never imports P2; the
  wiring in P4 composes the two.

**Nothing here executes anything.** There is no ``execute_fn`` field on
:class:`JobSpec` in Phase 1: an observer registration with no executable
makes dispatch structurally impossible by construction (AMD-1(a)) — the
strongest form of the dormancy gate. P2 adds the callable where the
engine's dispatch expects it; the P1 record deliberately has no such
field.

**Fence F-3 reminder:** this module declares no dormant control column of
the resource-cost-control family and no string here pairs the
subprocess-registry table name with a control token — the audit's
word-boundary fence forbids naming those columns anywhere in ``daemon/``
outside their model module. Terminal state is expressed as
``last_outcome`` stamped by ``EngineRepository.stamp_job_outcome``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "CATEGORY_STARTER_VOCABULARY",
    "CONCURRENCY_CLASSES",
    "EngineSpecError",
    "EventTrigger",
    "IntervalTrigger",
    "JobSpec",
    "ServiceMeta",
    "TIMEOUT_POLICIES",
    "TIMER_FIRST_TICK_KINDS",
    "TRIGGER_KINDS",
    "TriggerSpec",
    "validate_spec",
]

# ── closed value sets (validation rule inputs) ──────────────────────────

#: The concurrency classes from the frozen design (section B.2).
CONCURRENCY_CLASSES: frozenset[str] = frozenset(
    {"MAIN_LOOP", "DB_OFFLOAD", "DEDICATED_EXECUTOR"}
)

#: Timeout policies from the frozen design (section B.2).
TIMEOUT_POLICIES: frozenset[str] = frozenset({"cancel", "extend"})

#: Trigger kinds — exactly one per job.
TRIGGER_KINDS: frozenset[str] = frozenset({"interval", "event"})

#: First-tick kinds for an interval trigger.
TIMER_FIRST_TICK_KINDS: frozenset[str] = frozenset({"immediate", "delayed"})

#: The DOCUMENTED STARTER vocabulary for ``ServiceMeta.category`` (D1.10).
#:
#: This is a LABEL DISCIPLINE, not a gate: ``category`` is deliberately an
#: OPEN string so the next service class the estate grows needs no code
#: change and no migration. The starter set exists so metrics labels have
#: bounded cardinality in practice and so operators have a default to
#: reach for; ``validate_spec`` therefore does NOT reject an unknown
#: category.
CATEGORY_STARTER_VOCABULARY: tuple[str, ...] = (
    "reconciler",
    "recovery_sweep",
    "probe",
    "telemetry",
    "realtime_guard",
    "event_consumer",
    "engine_self",
    "housekeeping",
)


# ── the typed validation error ──────────────────────────────────────────


class EngineSpecError(ValueError):
    """A registration spec violated one of the substrate's rules.

    Attributes:
        code: One stable code per rule (see :func:`validate_spec`). Callers
            branch on the CODE, never on the message text.
        job_id: The offending spec's id (``None`` when the id itself is the
            violation).
    """

    def __init__(self, code: str, message: str, job_id: str | None = None) -> None:
        self.code = code
        self.job_id = job_id
        super().__init__(f"[{code}] {message}")

    def __reduce__(self):  # pragma: no cover — pickling parity for dataclass use
        return (_rebuild_engine_spec_error, (self.code, str(self), self.job_id))


def _rebuild_engine_spec_error(code: str, message: str, job_id: str | None):
    return EngineSpecError(code, message, job_id)


# ── service metadata (Emperor addendum) ─────────────────────────────────


@dataclass(frozen=True)
class ServiceMeta:
    """Self-describing registration metadata (Emperor addendum, 2026-11-21).

    "each service bring it info/meta when register … use for metric,
    management, debug easier."

    ``metadata`` is the EXTENSIBLE half: free-form keys, free-form values.
    P6 fixes the READ shape as a closed top-level key set
    (``owner`` / ``docs`` / ``slo`` / ``notes``) with free-form values —
    the column and this dataclass stay open so adding a key is never a
    schema change.
    """

    service_tag: str
    category: str
    service_name: str | None = None
    module_info: str | None = None
    description: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_row_dict(self) -> dict[str, Any]:
        """The durable-column projection of this metadata.

        Column names are the ``engine_jobs`` contract; ``metadata`` keeps
        its DB name (the model's Python attribute is ``job_metadata``).
        """
        # NOTE the key is ``job_metadata`` (the model ATTRIBUTE), not
        # ``metadata`` — the declarative base owns ``.metadata``, so the
        # column is named ``metadata`` in DDL while the Python side is
        # ``job_metadata`` (the job_queue / dependency_bus / instance
        # precedent). Emitting ``metadata`` here would be silently
        # ignored by pydantic (extra=ignore) and the JSON blob would
        # never persist.
        return {
            "service_tag": self.service_tag,
            "service_name": self.service_name,
            "category": self.category,
            "module_info": self.module_info,
            "description": self.description,
            "job_metadata": dict(self.metadata),
        }


# ── triggers ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntervalTrigger:
    """Fixed-cadence trigger.

    Attributes:
        seconds: Cadence in seconds (``>= 1`` — validation rule R1).
        jitter_seconds: Optional per-job first-tick jitter (``>= 0`` —
            validation rule R4). ``None`` means no jitter.
        first_tick_kind: ``immediate`` | ``delayed`` | ``None``.
        first_tick_offset_seconds: Offset for the ``delayed`` kind
            (default 0.0). This is the first-class replacement for the
            ``+30s`` drift-reconcile offset (frozen design section B.2).
    """

    seconds: float
    jitter_seconds: float | None = None
    first_tick_kind: str | None = None
    first_tick_offset_seconds: float | None = None

    @property
    def trigger_kind(self) -> str:
        """The trigger KIND discriminator (``interval``)."""
        return "interval"


@dataclass(frozen=True)
class EventTrigger:
    """Event-kind trigger — a pure DATA spec (T1.10 seam freeze).

    Carries no callback and imports nothing from the event registry or
    the execution core. P2's execution core owns
    ``on_event_kind(kind) -> callback``; P5's registry publishes kinds
    through its own sink. P4 composes them.

    Attributes:
        kind: The event-registry kind string (an ``EventKind`` value or a
            catalog ``kind_id``). NOT a FK/CHECK into ``event.kind``
            (fence F-6 — that SQLite CHECK lists only 8 of 24 members).
        filter: Optional JSON partition for the kind (``None`` = all
            occurrences).
    """

    kind: str
    filter: Mapping[str, Any] | None = None

    @property
    def trigger_kind(self) -> str:
        """The trigger KIND discriminator (``event``).

        NOTE: the dataclass FIELD ``kind`` is the EVENT kind string; this
        property is the TRIGGER kind used for the ``trigger_kind`` column.
        """
        return "event"


#: The trigger union. Both members are frozen dataclasses, so a
#: ``JobSpec`` carrying one is hashable/immutable from registration time.
TriggerSpec = IntervalTrigger | EventTrigger


# ── the registration record ─────────────────────────────────────────────


@dataclass(frozen=True)
class JobSpec:
    """The frozen registration record (frozen design section B.2).

    Identity: ``id`` is the stable ops SLUG (``orphan-watcher``,
    ``sources-gc``, …), never a uuid — re-registration upserts BY id
    (D1.4). A rename is a re-registration with an explicit migration
    note, never a silent second row.

    NOTE — no ``execute_fn`` field. Phase 1 registers OBSERVERS only: a
    spec with no executable makes dispatch structurally impossible
    (AMD-1(a)), which is the strongest form of the dormancy gate. P2's
    engine accepts the callable at its own dispatch seam.
    """

    id: str
    meta: ServiceMeta
    trigger: TriggerSpec
    max_runtime_s: float
    timeout_policy: str = "cancel"
    concurrency_class: str = "DB_OFFLOAD"
    priority: int = 5
    retry_max: int = 0
    retry_backoff_base_s: float | None = None
    retry_backoff_cap_s: float | None = None
    kill_switch_key: str | None = None

    # ── convenience projections (never a second source of truth) ──────

    @property
    def service_tag(self) -> str:
        return self.meta.service_tag

    @property
    def category(self) -> str:
        return self.meta.category

    @property
    def metrics_labels(self) -> dict[str, str]:
        """The metrics label pair the Emperor addendum mandates.

        ``service_tag`` + ``category`` — and nothing else, so label
        cardinality stays bounded by the registered service count.
        """
        return {"service_tag": self.meta.service_tag, "category": self.meta.category}

    def to_row_dict(self) -> dict[str, Any]:
        """Project this spec onto the ``engine_jobs`` column set.

        The registry adds ``version`` / ``registered_at`` / ``updated_at``;
        everything else is fully determined by the spec.
        """
        row: dict[str, Any] = {
            "id": self.id,
            "trigger_kind": self.trigger.trigger_kind,
            "max_runtime_s": self.max_runtime_s,
            "timeout_policy": self.timeout_policy,
            "retry_max": self.retry_max,
            "retry_backoff_base_s": self.retry_backoff_base_s,
            "retry_backoff_cap_s": self.retry_backoff_cap_s,
            "priority": self.priority,
            "concurrency_class": self.concurrency_class,
            "kill_switch_key": self.kill_switch_key,
            "enabled": 1,
        }
        row.update(self.meta.to_row_dict())
        if isinstance(self.trigger, IntervalTrigger):
            row["interval_seconds"] = self.trigger.seconds
            row["jitter_seconds"] = self.trigger.jitter_seconds
            row["first_tick_kind"] = self.trigger.first_tick_kind
            row["first_tick_offset_seconds"] = (
                self.trigger.first_tick_offset_seconds
            )
        else:  # EventTrigger
            row["event_kind"] = self.trigger.kind
            row["event_filter"] = (
                dict(self.trigger.filter) if self.trigger.filter else None
            )
        return row

    def content_fingerprint(self) -> tuple[Any, ...]:
        """A comparable content tuple — the re-registration no-op test.

        Excludes nothing: every field of the spec is part of the identity
        of the registration, so an identical re-registration is a no-op
        echo and ANY change bumps the version (AC5). ``metadata`` is
        flattened to sorted items so mapping order cannot fake a change.
        """
        meta = self.meta
        trigger_event_kind = (
            self.trigger.kind if isinstance(self.trigger, EventTrigger) else None
        )
        return (
            self.id,
            meta.service_tag,
            meta.service_name,
            meta.category,
            meta.module_info,
            meta.description,
            tuple(sorted(meta.metadata.items())),
            self.trigger.trigger_kind,
            getattr(self.trigger, "seconds", None),
            getattr(self.trigger, "jitter_seconds", None),
            getattr(self.trigger, "first_tick_kind", None),
            getattr(self.trigger, "first_tick_offset_seconds", None),
            (trigger_event_kind if trigger_event_kind is not None else None),
            (
                tuple(sorted(self.trigger.filter.items()))
                if isinstance(self.trigger, EventTrigger)
                and self.trigger.filter
                else None
            ),
            self.max_runtime_s,
            self.timeout_policy,
            self.concurrency_class,
            self.priority,
            self.retry_max,
            self.retry_backoff_base_s,
            self.retry_backoff_cap_s,
            self.kill_switch_key,
        )


# ── validation (the 9 rules from D1.12) ─────────────────────────────────


def validate_spec(spec: JobSpec) -> None:
    """Validate a registration spec against the substrate's 9 rules.

    Every rule raises :class:`EngineSpecError` with its OWN stable code so
    a rejected registration names exactly which rule it broke.

    ============  ====================================================
    Rule code      Rule
    ============  ====================================================
    ``SPEC_ID``             ``id`` is a non-empty string with no whitespace
    ``SPEC_SERVICE_TAG``    ``service_tag`` is a non-empty string
    ``SPEC_CATEGORY``       ``category`` is a non-empty string
    ``SPEC_INTERVAL``       interval trigger: ``seconds >= 1`` (R1)
    ``SPEC_MAX_RUNTIME``    ``max_runtime_s > 0`` (R2)
    ``SPEC_PRIORITY``       ``priority`` in 0..9 (R3)
    ``SPEC_JITTER``         ``jitter_seconds is None or >= 0`` (R4)
    ``SPEC_TRIGGER_COUNT``  exactly ONE trigger, of a known kind (R5)
    ``SPEC_TIMEOUT_POLICY`` ``timeout_policy`` in {cancel, extend} (R6)
    ``SPEC_CONCURRENCY``    ``concurrency_class`` in the 3 known classes (R7)

    Two further rules ride the trigger shape rather than a separate code:

    * an interval trigger's ``first_tick_kind``, when set, must be one of
      ``immediate`` / ``delayed`` → ``SPEC_INTERVAL``;
    * an event trigger's ``kind`` must be a non-empty string →
      ``SPEC_INTERVAL`` is NOT reused for this: see ``SPEC_EVENT``.

    ============  ====================================================
    ``SPEC_EVENT``          event trigger: ``kind`` is a non-empty string
    ============  ====================================================

    Args:
        spec: The candidate registration.

    Raises:
        EngineSpecError: On the first rule violated (fail-fast, matching
            the house ``ServicesConfig`` Field(ge=…) posture).
    """
    job_id = spec.id if isinstance(spec.id, str) else None

    # R-id: the slug must be usable as an ops identity.
    if not isinstance(spec.id, str) or not spec.id.strip():
        raise EngineSpecError("SPEC_ID", "id must be a non-empty string", job_id)
    if any(ch.isspace() for ch in spec.id):
        raise EngineSpecError(
            "SPEC_ID", f"id {spec.id!r} must not contain whitespace", job_id
        )

    # The addendum's two mandatory labels (AC6: NON-NULL on every row).
    if not isinstance(spec.meta.service_tag, str) or not spec.meta.service_tag.strip():
        raise EngineSpecError(
            "SPEC_SERVICE_TAG",
            "meta.service_tag must be a non-empty string (the stable ops id)",
            job_id,
        )
    if not isinstance(spec.meta.category, str) or not spec.meta.category.strip():
        raise EngineSpecError(
            "SPEC_CATEGORY",
            "meta.category must be a non-empty string (open vocabulary)",
            job_id,
        )

    # R5: exactly one trigger, and it must be one of the two known kinds.
    trigger = spec.trigger
    if not isinstance(trigger, (IntervalTrigger, EventTrigger)):
        raise EngineSpecError(
            "SPEC_TRIGGER_COUNT",
            "trigger must be exactly one IntervalTrigger or EventTrigger "
            f"(got {type(trigger).__name__})",
            job_id,
        )

    # R1 / R4 + first-tick shape.
    if isinstance(trigger, IntervalTrigger):
        if trigger.seconds < 1:
            raise EngineSpecError(
                "SPEC_INTERVAL",
                f"interval_seconds must be >= 1 (got {trigger.seconds!r})",
                job_id,
            )
        if trigger.jitter_seconds is not None and trigger.jitter_seconds < 0:
            raise EngineSpecError(
                "SPEC_JITTER",
                f"jitter_seconds must be >= 0 (got {trigger.jitter_seconds!r})",
                job_id,
            )
        if (
            trigger.first_tick_kind is not None
            and trigger.first_tick_kind not in TIMER_FIRST_TICK_KINDS
        ):
            raise EngineSpecError(
                "SPEC_INTERVAL",
                "first_tick_kind must be one of "
                f"{sorted(TIMER_FIRST_TICK_KINDS)} (got "
                f"{trigger.first_tick_kind!r})",
                job_id,
            )
        if (
            trigger.first_tick_offset_seconds is not None
            and trigger.first_tick_offset_seconds < 0
        ):
            raise EngineSpecError(
                "SPEC_INTERVAL",
                "first_tick_offset_seconds must be >= 0 (got "
                f"{trigger.first_tick_offset_seconds!r})",
                job_id,
            )
    else:  # EventTrigger
        if not isinstance(trigger.kind, str) or not trigger.kind.strip():
            raise EngineSpecError(
                "SPEC_EVENT",
                "event trigger kind must be a non-empty string",
                job_id,
            )
        # Jitter is meaningless on an event trigger but harmless; if the
        # spec ALSO carries the interval-only fields there is no way to
        # express that through the union — no check needed.

    # R2.
    if not (spec.max_runtime_s > 0):
        raise EngineSpecError(
            "SPEC_MAX_RUNTIME",
            f"max_runtime_s must be > 0 (got {spec.max_runtime_s!r})",
            job_id,
        )

    # R3.
    if not isinstance(spec.priority, int) or isinstance(spec.priority, bool):
        raise EngineSpecError(
            "SPEC_PRIORITY",
            f"priority must be an integer (got {spec.priority!r})",
            job_id,
        )
    if not (0 <= spec.priority <= 9):
        raise EngineSpecError(
            "SPEC_PRIORITY",
            f"priority must be in 0..9 (got {spec.priority!r})",
            job_id,
        )

    # R6.
    if spec.timeout_policy not in TIMEOUT_POLICIES:
        raise EngineSpecError(
            "SPEC_TIMEOUT_POLICY",
            "timeout_policy must be one of "
            f"{sorted(TIMEOUT_POLICIES)} (got {spec.timeout_policy!r})",
            job_id,
        )

    # R7.
    if spec.concurrency_class not in CONCURRENCY_CLASSES:
        raise EngineSpecError(
            "SPEC_CONCURRENCY",
            "concurrency_class must be one of "
            f"{sorted(CONCURRENCY_CLASSES)} (got {spec.concurrency_class!r})",
            job_id,
        )
