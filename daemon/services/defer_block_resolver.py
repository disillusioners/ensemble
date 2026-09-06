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

Pure read-model: WS2 ``2 + len(dedup'd_holders)`` SELECTs per
resolve (the no-carve-out witness SELECT + the defer-lane pending
count + one WS1 carve-out EXISTS per dedup'd holder), zero DML, no
JobItem creation, no admission-state writes —
``KNOWN_ADMISSION_STATE_WRITERS`` stays frozen at 23
(``daemon/job_state/constitution.py``). The scanner in
``constitution.py`` keys on ``SET admission_state`` strings,
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
* ``kind`` (WS2, three values):
  - ``"paused"`` when the witness instance's status is ``paused``
    (the W2 invariant: pause is suspended-but-occupying — the AMBER
    severity). Paused takes precedence over stalled by construction:
    a paused instance's actionable unblock is always the operator
    action (resume / terminate), never the mirrors-only ``stalled``
    action (force-complete), so the operator-actionable status wins.
  - ``"stalled"`` for a non-paused witness whose gate-busy state is
    EXCLUSIVELY its OWN settled message mirrors (the WS1 carve-out
    test — ``has_active_non_deferred_work(None,
    requester_instance_id=<holder>)`` returns ``False``: the carve-out
    excludes the holder's own settled mirrors; if no OTHER busy
    witness remains, every row in the no-carve-out busy-set was the
    holder's own mirrors — the gate is held by mirrors pinning the
    gate against an instance with no live work running). Stalled is
    actionable via force-complete of the holder's settled mirrors
    (WS4 will ship the cleanup mechanic — the kind surfaces the
    remediation shape WITHOUT taking the action; docs §8.5 by-design
    note about cleanup blindness is appended by WS4).
  - ``"live"`` for every other witness (a non-paused instance with
    genuine non-mirror busy work, OR a legacy-clause witness with no
    instance row at all — instance-less JobItems have no
    ``instance_id`` to feed the carve-out bind, so they fall through
    as ``live`` by construction).
* ``since`` (normalized ISO-8601, naive → UTC — the
  ``_parse_job_created_at`` pattern in ``job_recovery_service.py``:
  ``instances.last_activity_at`` is TEXT on PG (tz-naive) vs SQLite
  (tz-aware)): paused holders report ``paused_at`` (falling back to
  ``updated_at``/``created_at``); live AND stalled holders report
  ``last_activity_at`` (falling back through ``updated_at``/
  ``created_at`` — stalled holders have no live task to bump
  ``last_activity_at`` recently, so the timestamp is the most-recent
  prior activity stamp); instance-less witnesses report the JobItem's
  own ``created_at``.
* Ordering: paused holders first (the operator-priority AMBER
  witnesses), then stalled (the operator-actionable mirrors-only
  witnesses), then live — each ascending by ``instance_id`` —
  deterministic for tests and UI.

WS2 query budget — the stall check adds ONE additional EXISTS query
per dedup'd holder instance (``2 + len(holders)`` SELECTs total per
``resolve()`` call). Bounded by dedup'd holder count, which is small
in practice (every holder is per-DISTINCT-instance — multiple busy
JobItems on the SAME instance dedupe to one holder in
``_project_holders``); NOT a witness-row N+1. The bounded-query test
pin in ``tests/unit/routers/test_defer_blocked_api.py::TestBoundedQueryCount``
documents the new ``2 + N`` budget and the dedup invariant that keeps
``N`` small. The carve-out bodies are byte-derived from the same
``_idle_predicate_sql`` gate constants (the WS1 seam), so the stall
classification cannot drift from the gate's own decision.

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

#: Holder kind for a non-paused witness whose gate-busy state is
#: EXCLUSIVELY its own settled message mirrors — the WS2 mirrors-only
#: shape (no live ACTIVE task on the instance, no OTHER-instance
#: witnesses). Detected by re-evaluating the gate predicate with the
#: WS1 requester-instance carve-out
#: (``has_active_non_deferred_work(None, requester_instance_id=<holder>)``):
#: carve-out ⇒ busy-set excludes the holder's own settled mirrors; if
#: the result is empty, every remaining witness was the holder's OWN
#: mirrors — the holder is "stalled" (AMBER severity, actionable via
#: force-complete of the holder's mirrors; the
#: ``defer_blocked`` ``True`` reading stays the gate's own decision
#: and is unaffected by the kind). Distinguished from ``paused`` at
#: the FE tooltip layer (paused = suspended-by-operator; stalled =
#: nothing-running-but-mirrors-pinning-the-gate).
HOLDER_KIND_STALLED: Final[str] = "stalled"

#: Holder kind for a witness that is NEITHER ``paused`` NOR
#: ``stalled`` — a live instance with a genuine non-mirror busy row
#: (an ACTIVE foreground turn, or OTHER-instance mirrors still
#: witnessing after the carve-out). Also the kind for a legacy-clause
#: witness with no instance row at all (instance-less JobItems have no
#: instance_id to feed the carve-out bind — they fall through as
#: ``live`` by construction).
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
        kind: :data:`HOLDER_KIND_PAUSED`, :data:`HOLDER_KIND_STALLED`,
            or :data:`HOLDER_KIND_LIVE` (see module docstring "Holder
            semantics" for the WS2 three-way classification).
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

    **WS2 query budget** — :meth:`resolve` issues exactly
    ``2 + len(dedup'd_holders)`` SELECTs:

    1. the shared-composition witness SELECT (system-wide no-carve-out
       body — the same body the gate evaluates for ``project_id=None``);
    2. the defer-lane pending count;
    3. for each DEDUP'D holder instance, one EXISTS query evaluating
       the WS1 carve-out gate body with the holder's instance as the
       requester (the stall classification — see module docstring).

    Dedup'd holders are per-DISTINCT-instance (multiple busy JobItems
    on the SAME instance collapse to ONE holder in
    :func:`_project_holders`); N stays small in practice — bounded by
    the number of distinct non-terminal instances with non-defer
    busy rows. NOT a witness-row N+1. The ``TestBoundedQueryCount``
    pin in ``tests/unit/routers/test_defer_blocked_api.py`` documents
    the ``2 + N`` budget and the dedup invariant that keeps ``N``
    small. On an empty busy-set the budget collapses to the original
    2 (no stall checks needed — no holders to classify).
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

        WS2: the stall classification is computed by re-evaluating the
        WS1 carve-out gate body (``has_active_non_deferred_work``
        semantics — byte-derived from the same
        ``_idle_predicate_sql`` gate constants) with each DEDUP'D
        holder's instance as the requester. A holder whose carve-out
        busy-set is empty is EXCLUSIVELY holding the gate with its
        OWN settled mirrors — ``stalled``. The carve-out bodies are
        the same SELECTs the gate evaluates; the WS2 stall check is
        a re-evaluation of the gate, not a re-implementation.

        Returns:
            :class:`DeferBlockSnapshot` — ``defer_blocked`` mirrored
            from the witness rows, ``pending_count`` from the defer
            lane, ``holders`` enumerated (paused > stalled > live,
            each ascending by ``instance_id``).

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

            # WS2 stall classification: collect the dedup'd instance_ids
            # of non-paused holders and probe each with the WS1
            # carve-out gate body. A holder whose carve-out busy-set
            # is empty is mirrors-only (its own settled mirrors were
            # the ONLY busy rows; the carve-out excluded them). The
            # probe uses the canonical gate entry point — same body
            # the gate evaluates, fail-CLOSED on DB error (the
            # gate's own posture is propagated: a probe that cannot
            # complete ⇒ the holder is conservatively ``live``,
            # because the alternative (``stalled``) implies a
            # force-complete action whose safety we cannot prove
            # without a clean probe result). Bounded by dedup'd
            # holder count, NOT by raw witness-row count.
            stalled_instance_ids = _classify_stalled_holders(
                job_repo=self._job_repo,
                witness_rows=witness_rows,
            )

        return DeferBlockSnapshot(
            defer_blocked=len(witness_rows) > 0,
            pending_count=int(pending_count),
            holders=_project_holders(witness_rows, stalled_instance_ids),
        )


# ── Holder projection ──────────────────────────────────────────────────────


def _classify_stalled_holders(
    *,
    job_repo: "JobRepository",
    witness_rows: Any,
) -> frozenset[str]:
    """Return the dedup'd set of non-paused instance_ids whose gate-busy
    state is EXCLUSIVELY their OWN settled message mirrors.

    For each dedup'd non-paused instance-backed holder, evaluate the
    WS1 carve-out gate body with the holder's instance as the
    requester. The carve-out excludes the holder's OWN settled
    mirrors from the busy-set; if the carve-out busy-set is empty,
    every row in the no-carve-out busy-set was the holder's own
    mirrors — the holder is ``stalled`` (WS2 AMBER severity, the
    mirrors-only actionable shape).

    Implementation notes:

    * Re-uses :meth:`JobRepository.has_active_non_deferred_work` —
      the canonical gate entry point — so the carve-out body cannot
      drift from the gate's own evaluation (derive-don't-reimplement;
      same pattern as :meth:`resolve`'s witness query, which itself
      composes from the same ``_idle_predicate_sql`` constants).
      Each probe opens its own ``engine.begin()`` round-trip; the
      query budget is one probe per dedup'd holder, total
      ``2 + len(dedup'd_holders)`` SELECTs for the resolve call.
    * Bounded by DEDUP'D holder count (the dedup happens here, not
      in the caller's witness-row iteration): ``set()`` over the
      seen instance_ids, one ``has_active_non_deferred_work`` call
      per UNIQUE non-paused instance.
    * Fail-CLOSED on probe error: the gate's own posture propagates.
      A probe that returns ``True`` (busy after carve-out) is a
      non-stalled holder; a probe that returns ``False`` (empty after
      carve-out) is stalled. The gate method itself returns ``True``
      on its own DB error (the W3 hotfix fail-CLOSED posture), so a
      transient probe error conservatively classifies the holder as
      ``live`` — the alternative (classifying as ``stalled`` and
      triggering force-complete) is unsafe to commit on a probe whose
      result we cannot verify.
    * Paused holders are excluded from the probe entirely: paused
      always wins over stalled (see :func:`_holder_kind_for`), so
      probing a paused instance's carve-out status is wasted work —
      and a paused holder's busy state is irrelevant to the kind
      (paused is its own kind regardless of mirrors-vs-live).
    * Instance-less witnesses (``j.instance_id IS NULL``) cannot be
      classified as stalled (no instance_id to feed the carve-out
      bind). They fall through as ``live`` by construction; the
      instance-less branch of :func:`_holder_from_row` ignores the
      stalled flag.
    """
    stalled: set[str] = set()
    seen: set[str] = set()
    for row in witness_rows:
        instance_id = row["instance_id"]
        if not instance_id or instance_id in seen:
            # Skip: instance-less (no probe possible) or already
            # classified.
            continue
        status = row["instance_status"] or ""
        if status == InstanceStatus.PAUSED.value:
            # Paused always wins; no probe needed.
            seen.add(instance_id)
            continue
        seen.add(instance_id)
        # WS1 carve-out probe: gate predicate with this holder as the
        # requester. ``project_id=None`` selects the system-wide body
        # (same scope as :meth:`resolve`).
        if not job_repo.has_active_non_deferred_work(
            project_id=None,
            requester_instance_id=instance_id,
        ):
            stalled.add(instance_id)
    return frozenset(stalled)


def _holder_kind_for(status: str | None, stalled: bool = False) -> str:
    """Map a witness instance's raw status (and the WS2 stalled flag)
    to the holder kind.

    ``paused`` is the AMBER kind and ALWAYS wins over ``stalled``
    (a paused instance is actionable by the operator regardless of
    whether its busy rows are exclusively mirrors — the actionable
    unblock is resume/terminate, never force-complete). ``stalled`` is
    the AMBER kind for a non-paused witness whose gate-busy state is
    EXCLUSIVELY its own settled message mirrors (the WS1 carve-out
    test, computed upstream by :meth:`DeferBlockResolver.resolve`).
    Every other status (and a missing instance) is ``live``. The
    gate's own truthmaker is ``Instance.status NOT IN terminal`` —
    ``paused`` is deliberately non-terminal (W2 invariant), which is
    exactly why a paused instance holds the gate and why it deserves
    its own kind here; ``stalled`` derives from the gate predicate
    composition itself, not from ``Instance.status``.
    """
    if status == InstanceStatus.PAUSED.value:
        return HOLDER_KIND_PAUSED
    if stalled:
        return HOLDER_KIND_STALLED
    return HOLDER_KIND_LIVE


def _holder_from_row(row: Any, stalled: bool = False) -> DeferBlockHolder:
    """Project one witness row (``sqlalchemy.RowMapping``) onto a holder.

    Field-sourcing rules per the module docstring ("Holder
    semantics"): instance-backed witnesses pull identity/status/
    timestamps from the joined ``instances`` columns; instance-less
    legacy-clause witnesses fall back to the JobItem's own
    ``agent_id`` / ``created_at`` and surface with
    ``instance_id=""`` / ``status=""`` / ``kind="live"``.

    The ``stalled`` flag is the WS2 carve-out result (see
    :func:`_holder_kind_for`): ``True`` when the gate predicate with
    the holder's instance as requester returns ``False`` (every row
    in the no-carve-out busy-set was the holder's own mirrors). Only
    non-paused instance-backed witnesses can be stalled — instance-
    less holders fall through as ``live`` regardless of the flag.
    """
    instance_id = row["instance_id"] or NO_INSTANCE_ID
    if instance_id:
        status = row["instance_status"] or ""
        kind = _holder_kind_for(row["instance_status"], stalled=stalled)
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
            # falling back through the row's own update stamps. Used
            # for both live and stalled holders (stalled holders have
            # no live task to bump last_activity_at recently, so the
            # value is the most-recent prior activity stamp — still
            # the cheapest honest timestamp).
            since_raw = (
                row["instance_last_activity_at"]
                or row["instance_updated_at"]
                or row["instance_created_at"]
            )
    else:
        # No instance row (legacy clause: active JobItem with
        # instance_id IS NULL). The JobItem itself is the witness.
        # Instance-less holders have no instance_id to feed the
        # carve-out bind — they fall through as ``live`` regardless
        # of any stalled flag (the stalled flag is irrelevant when
        # there is no instance to test).
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


def _project_holders(
    witness_rows: Any,
    stalled_instance_ids: frozenset[str] = frozenset(),
) -> list[DeferBlockHolder]:
    """Deduplicate + order the witness rows into the holders list.

    One holder per DISTINCT witness instance (several busy JobItems on
    the same instance describe one holder); instance-less witnesses
    are keyed per-JobItem (``__job__:<job_id>``) so none is dropped —
    dropping any witness would break the holders-non-empty ==
    gate-blocked invariant.

    WS2: the WS1 carve-out result (``stalled_instance_ids`` — the
    dedup'd set of holder instances whose gate-busy state is
    EXCLUSIVELY their own settled mirrors) reclassifies non-paused
    instance-backed holders to ``"stalled"`` at projection time. The
    flag is irrelevant for instance-less holders (no instance to
    test) and for paused holders (paused always wins).

    Ordering: paused holders first (AMBER operator-priority), then
    stalled (AMBER mirrors-only actionable), then live — each
    ascending by ``(instance_id, agent)`` — deterministic.
    """
    merged: dict[str, DeferBlockHolder] = {}
    for row in witness_rows:
        instance_id = row["instance_id"] or NO_INSTANCE_ID
        stalled = bool(instance_id) and instance_id in stalled_instance_ids
        holder = _holder_from_row(row, stalled=stalled)
        key = (
            holder.instance_id
            if holder.instance_id
            else f"__job__:{row['job_id']}"
        )
        if key not in merged:
            merged[key] = holder
    return sorted(
        merged.values(),
        # paused > stalled > live; within each kind: instance_id, agent.
        key=lambda h: (
            h.kind != HOLDER_KIND_PAUSED,
            h.kind != HOLDER_KIND_STALLED,
            h.instance_id,
            h.agent,
        ),
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
