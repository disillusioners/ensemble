"""DeferBlockResolver — read-only enumeration of the defer gate's busy-set.

The transparency half of the defer-gate observability fix (2026-09-04,
``feature/queue-status-missions-badge``): the defer gate can hold
indefinitely on a witness operators cannot see — the live case was a
paused instance (``8d8a5591``) sitting in the gate's busy-set with zero
visibility on any surface. :class:`DeferBlockResolver` enumerates the
gate's actual witnesses so ``GET /api/queues/defer-blocked``
(``daemon/routers/queues.py``, docs/job-task-system.md §8.5) can show
exactly what the gate sees.

Gate-truth == display-truth, BY CONSTRUCTION
--------------------------------------------

The witness enumeration is composed from the SAME exported statement
builders in ``daemon/repositories/job_queue/_idle_predicate_sql.py``
that the gate path
(``JobRepository.has_active_non_deferred_work`` ~repository.py:706-870)
composes — :func:`_idle_predicate_sql.defer_busy_witness_statement` is
DERIVED from the gate body constants by unwrapping ``SELECT EXISTS (
SELECT 1 …)``, so the FROM/JOIN/WHERE busy-set text is byte-shared and
cannot drift. Re-implementing the predicate independently here is the
defect this design forbids; there is no second copy of the predicate
SQL anywhere in this file. ``defer_blocked`` is computed from the
enumerated witness rows (``len(witnesses) > 0``) — the same rows the
gate's ``EXISTS`` ranges over — so the boolean agrees with the gate's
admission logic by construction, not by discipline. Behavioral
equivalence (gate decision == witnesses non-empty across fixture sets)
is pinned in ``tests/unit/routers/test_defer_blocked_api.py``.

Purity (census contract)
------------------------

Pure read-model: two SELECTs per resolve, zero DML, no JobItem
creation, no admission-state writes — ``KNOWN_ADMISSION_STATE_WRITERS``
stays frozen at 23 (``daemon/job_state/constitution.py``). The scanner
in ``constitution.py`` keys on ``SET admission_state`` strings,
``admission_state=`` keyword writes and ``"admission_state"`` dict keys;
none appear here (the module's only ``admission_state`` occurrences are
column REFERENCES inside the shared gate SQL imported from
``_idle_predicate_sql``).

Holder semantics (docs §8.5)
----------------------------

* One holder per DISTINCT witness instance; witness JobItems whose
  ``instance_id`` is NULL (the legacy clause's instance-less active
  rows) each surface as their OWN holder with ``instance_id=""``,
  ``agent`` from the JobItem row, ``status=""`` and ``kind="live"`` —
  dropping them would break the holders-non-empty == gate-blocked pin.
* ``kind``: ``"paused"`` when the witness instance's status is
  ``paused`` (the W2 invariant: pause is suspended-but-occupying — the
  AMBER severity), ``"live"`` otherwise.
* ``since`` (normalized ISO-8601, naive → UTC — the
  ``_parse_job_created_at`` pattern in ``job_recovery_service.py``:
  ``instances.last_activity_at`` is TEXT on PG (tz-naive) vs SQLite
  (tz-aware)): paused holders report ``paused_at`` (falling back to
  ``updated_at``/``created_at``); live holders report
  ``last_activity_at`` (falling back to ``updated_at``/
  ``created_at``); instance-less witnesses report the JobItem's own
  ``created_at``.
* Ordering: paused holders first (the operator-priority AMBER
  witnesses), then live, each ascending by ``instance_id`` —
  deterministic for tests and UI.

Degradation posture: none. A DB error propagates (⇒ HTTP 500 on the
route) — queues-family convention (no §8.2-style degrade shape on this
router family), and the honest choice: the gate itself fails CLOSED on
DB error, and a surface that served no body at all never falsely
claims the gate is open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text
from sqlalchemy import TextClause

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.job_queue import _idle_predicate_sql
from daemon.routers.schemas import DeferBlockHolderResponse

if TYPE_CHECKING:
    from daemon.repositories.job_queue.repository import JobRepository


# ── Vocabulary ─────────────────────────────────────────────────────────────

#: Holder kind for a witness whose instance row is currently ``paused``
#: (the AMBER severity shape — the operator-actionable case).
HOLDER_KIND_PAUSED: Final[str] = "paused"

#: Holder kind for every non-paused witness (live instance, or a
#: legacy-clause witness with no instance row at all).
HOLDER_KIND_LIVE: Final[str] = "live"

#: ``instance_id`` value for a busy-set witness JobItem that has NO
#: instance row (``j.instance_id IS NULL`` — the legacy clause's
#: instance-less shape). The wire contract types ``instance_id`` as a
#: string; the empty string is the honest "no instance" encoding.
NO_INSTANCE_ID: Final[str] = ""

#: Pending-defer count SQL — count of PENDING (``admission_state='queued'``)
#: non-deleted JobItems on defer-type queues (``queue_type='defer'`` —
#: the ``system_defer_queue`` lane, one per project). System-wide: the
#: endpoint is unscoped. Composed from literal status/type values with
#: no parameters — nothing user-shaped reaches this string.
_DEFER_PENDING_COUNT_SQL: Final[TextClause] = text(
    "SELECT count(*) FROM job_queue_items j"
    " LEFT JOIN job_queues q ON j.queue_id = q.queue_id"
    " WHERE j.deleted_at IS NULL"
    " AND j.admission_state = 'queued'"
    " AND q.queue_type = 'defer'"
)


# ── Projection records ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeferBlockHolder:
    """One busy-set witness, projected for the wire (docs §8.5).

    Attributes:
        instance_id: The witness instance's id, or ``""`` for a
            legacy-clause witness with no instance row.
        agent: ``Instance.agent_id`` when an instance row exists,
            else the JobItem's own ``agent_id``.
        status: The witness instance's raw ``Instance.status`` (the
            gate's truthmaker — shown raw, not canonicalized), or
            ``""`` when there is no instance row.
        since: Normalized ISO-8601 timestamp (naive → UTC) per the
            holder-typed fallback chain in the module docstring;
            ``None`` only when the source columns are all NULL.
        kind: :data:`HOLDER_KIND_PAUSED` or :data:`HOLDER_KIND_LIVE`.
    """

    instance_id: str
    agent: str
    status: str
    since: str | None
    kind: str


@dataclass(frozen=True)
class DeferBlockSnapshot:
    """The ``GET /api/queues/defer-blocked`` payload (docs §8.5).

    Attributes:
        defer_blocked: True iff the defer gate's busy predicate is
            currently satisfied — computed from the enumerated witness
            rows, which share the gate's predicate composition by
            construction (module docstring).
        pending_count: Count of PENDING (``admission_state='queued'``)
            non-deleted JobItems on defer-type queues (the
            ``system_defer_queue`` lane), system-wide.
        holders: The busy-set witnesses, enumerated. Empty iff the gate
            is NOT blocked (the RED-anomaly shape is
            ``pending_count > 0`` AND ``holders == []`` — the surface
            exposes the inputs; the severity conjunction is a
            display-side read).
    """

    defer_blocked: bool
    pending_count: int
    holders: list[DeferBlockHolder] = field(default_factory=list)


# ── Normalization helpers ──────────────────────────────────────────────────


def _parse_timestamp(value: Any) -> datetime | None:
    """Defensive parse of an instance/job timestamp into UTC-aware
    ``datetime`` — the ``_parse_job_created_at`` pattern
    (``job_recovery_service.py:2997``): ``instances.last_activity_at``
    is TEXT, tz-naive on PG and tz-aware on SQLite; naive values are
    assumed UTC. Returns ``None`` for NULL/unparseable values.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_since(value: Any) -> str | None:
    """Normalize a raw timestamp column value to an ISO-8601 string.

    NULL → ``None``; unparseable → pass-through ``str(value)`` (the
    surface is a transparency mirror — it must not crash on, nor
    silently drop, a malformed column value).
    """
    parsed = _parse_timestamp(value)
    if parsed is not None:
        return parsed.isoformat()
    if value is None:
        return None
    return str(value)


# ── The resolver ───────────────────────────────────────────────────────────


class DeferBlockResolver:
    """Enumerate the defer gate's busy-set witnesses (READ-ONLY).

    Mirrors the leaf-service pattern of
    ``daemon/services/mission_resolver.py:MissionResolver``: the caller
    wires the repository at construction time, the service holds no
    state, and every public method reads through the underlying engine.
    Two SELECTs per :meth:`resolve` — the shared-composition witness
    SELECT + the pending-defer count — regardless of witness count (no
    N+1; pinned by an engine event listener in the test file).
    """

    def __init__(self, job_repo: "JobRepository") -> None:
        """Initialize with the READ-only ``JobRepository``.

        Args:
            job_repo: ``JobRepository`` whose engine serves the
                witness + count SELECTs. READ-ONLY access — no writes,
                no JobItem creation (census stays frozen at 23).
        """
        self._job_repo = job_repo

    def resolve(self) -> DeferBlockSnapshot:
        """Enumerate the defer busy-set (system-wide scope).

        The endpoint is unscoped (``GET /api/queues/defer-blocked``),
        so the system-wide defer busy body is selected — the same body
        selection rule the gate applies for ``project_id=None`` (the
        maintenance ``_is_idle`` scope).

        Returns:
            :class:`DeferBlockSnapshot` — ``defer_blocked`` mirrored
            from the witness rows, ``pending_count`` from the defer
            lane, ``holders`` enumerated (paused first).

        Raises:
            SQLAlchemyError: propagated — no degrade shape (module
                docstring, "Degradation posture").
        """
        with self._job_repo.engine.connect() as conn:
            witness_rows = (
                conn.execute(
                    _idle_predicate_sql.defer_busy_witness_statement(None),
                    _idle_predicate_sql.defer_busy_witness_binds(None),
                )
                .mappings()
                .all()
            )
            pending_count = conn.execute(_DEFER_PENDING_COUNT_SQL).scalar_one()

        return DeferBlockSnapshot(
            defer_blocked=len(witness_rows) > 0,
            pending_count=int(pending_count),
            holders=_project_holders(witness_rows),
        )


# ── Holder projection ──────────────────────────────────────────────────────


def _holder_kind_for(status: str | None) -> str:
    """Map a witness instance's raw status to the holder kind.

    ``paused`` is the AMBER kind; every other status (and a missing
    instance) is ``live``. The gate's own truthmaker is
    ``Instance.status NOT IN terminal`` — ``paused`` is deliberately
    non-terminal (W2 invariant), which is exactly why a paused
    instance holds the gate and why it deserves its own kind here.
    """
    if status == InstanceStatus.PAUSED.value:
        return HOLDER_KIND_PAUSED
    return HOLDER_KIND_LIVE


def _holder_from_row(row: Any) -> DeferBlockHolder:
    """Project one witness row (``sqlalchemy.RowMapping``) onto a holder.

    Field-sourcing rules per the module docstring ("Holder
    semantics"): instance-backed witnesses pull identity/status/
    timestamps from the joined ``instances`` columns; instance-less
    legacy-clause witnesses fall back to the JobItem's own
    ``agent_id`` / ``created_at`` and surface with
    ``instance_id=""`` / ``status=""`` / ``kind="live"``.
    """
    instance_id = row["instance_id"] or NO_INSTANCE_ID
    if instance_id:
        status = row["instance_status"] or ""
        kind = _holder_kind_for(row["instance_status"])
        agent = row["instance_agent_id"] or ""
        if kind == HOLDER_KIND_PAUSED:
            # Status-change timestamp for the paused case: paused_at
            # is the pause transition stamp; updated_at is the cheap
            # fallback (the ORM before_update listener keeps it
            # fresh); created_at is the last-resort.
            since_raw = (
                row["instance_paused_at"]
                or row["instance_updated_at"]
                or row["instance_created_at"]
            )
        else:
            # Cheapest honest liveness timestamp: last_activity_at,
            # falling back through the row's own update stamps.
            since_raw = (
                row["instance_last_activity_at"]
                or row["instance_updated_at"]
                or row["instance_created_at"]
            )
    else:
        # No instance row (legacy clause: active JobItem with
        # instance_id IS NULL). The JobItem itself is the witness.
        status = ""
        kind = HOLDER_KIND_LIVE
        agent = row["job_agent_id"] or ""
        since_raw = row["job_created_at"]
    return DeferBlockHolder(
        instance_id=instance_id,
        agent=agent,
        status=status,
        since=_normalize_since(since_raw),
        kind=kind,
    )


def _project_holders(witness_rows: Any) -> list[DeferBlockHolder]:
    """Deduplicate + order the witness rows into the holders list.

    One holder per DISTINCT witness instance (several busy JobItems on
    the same instance describe one holder); instance-less witnesses
    are keyed per-JobItem (``__job__:<job_id>``) so none is dropped —
    dropping any witness would break the holders-non-empty ==
    gate-blocked invariant. Paused holders sort first (AMBER
    operator-priority), then live, each ascending by
    ``(instance_id, agent)`` — deterministic.
    """
    merged: dict[str, DeferBlockHolder] = {}
    for row in witness_rows:
        holder = _holder_from_row(row)
        key = (
            holder.instance_id
            if holder.instance_id
            else f"__job__:{row['job_id']}"
        )
        if key not in merged:
            merged[key] = holder
    return sorted(
        merged.values(),
        key=lambda h: (h.kind != HOLDER_KIND_PAUSED, h.instance_id, h.agent),
    )


# ── Holder → wire-schema projection ────────────────────────────────────────


def _holder_to_response(
    holder: "DeferBlockHolder",
) -> "DeferBlockHolderResponse":
    """Map a :class:`DeferBlockHolder` onto the wire schema (explicit).

    Mirrors the :func:`daemon.routers.missions._mission_record_to_response`
    precedent: explicit field-by-field construction (greppable on
    either side) rather than a dict-literal comprehension or
    ``model_validate(from_attributes=True)``. The dict-literal shape
    previously lived inline in the ``GET /api/queues/defer-blocked``
    handler — the helper makes the conversion a single source so the
    schema contract and the resolver dataclass drift cannot both
    change without a conscious edit here.
    """
    return DeferBlockHolderResponse(
        instance_id=holder.instance_id,
        agent=holder.agent,
        status=holder.status,
        since=holder.since,
        kind=holder.kind,
    )
