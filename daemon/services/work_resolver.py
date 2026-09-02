"""Work resolver service — Virtual Job Management Surface.

Phase 1 (Batch 3, 2026-06-27) of
``feature/virtual-job-management-surface``. The single read API that
collapses the worker pool's ``task`` table and the dependency bus's
``job_queue_items`` table onto a unified ``WorkRecord`` view keyed by
the stable cross-system ``work_id`` (a UUID4 string assigned at Task /
JobItem creation).

The resolver is **read-only** — it never mutates either backing table.
The two-path architecture (WorkerPool task vs. JobQueue job) is hidden
behind one vocabulary (``work_status.canonicalize_status``) and one
identifier (``work_id``) so callers (HTTP routes, MCP tools, future UI
surfaces) don't need to branch on which table backs a given handle.

P-C(i) (2026-06-27): ``list_work`` additionally dedupes Task turns
whose ``instance_id`` matches a JobItem (the ``job_create`` flow emits
both rows for the same logical work unit). Single-record lookups via
``resolve_work`` are not affected.

Why a separate service
----------------------

* Keeps ``daemon/repositories`` dependency-free (the resolver sits on
  top of three repositories, none of which know about it).
* Mirrors the constructor-injection pattern used by the rest of
  ``daemon/services/`` (``JobQueueService``,
  ``JobQueueMgmtService``, ``JobRetryEngine``, …) so wiring it in
  ``daemon/api.py`` follows the same recipe.
* Centralises the ``task.result`` JSON-parse rule (currently
  duplicated in ``daemon/routers/messages.py:251-263``) so every
  resolver consumer agrees on the ``result_summary`` shape.

Read-side SQL posture
---------------------

The resolver runs through ``TaskRepository`` / ``JobRepository`` /
``SQLModelInstanceRepository`` (the public data-access layer) — no
private ``engine.begin()`` SQL. The lookup path is bounded by indexed
columns (``task.work_id`` is ``unique=True, index=True``; ``job_id`` is
the JobItem PK; ``task.instance_id`` and ``job_queue_items.instance_id``
carry indexes on their respective tables), so the cost is a constant
handful of indexed SELECTs per ``resolve_work`` call. Note that
``Task`` has no ``project_id`` column — the resolver pulls project
identity transitively from the matching ``instances`` row in
``_lookup_instance``, and the ``list_work(project_id=...)`` filter is
applied post-fetch on the Task side. ``JobItem`` carries ``project_id``
directly and keeps its SQL-level filter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import col, select
from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.task.models import Task

from .work_status import (
    _STATUS_CANONICAL_MAP,
    canonicalize_status,
)

if TYPE_CHECKING:
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.task.repository import TaskRepository

logger = logging.getLogger(__name__)


# ── Admission-state → representative status map ───────────────────────────
# Phase 4: the ``status`` column is frozen at the INSERT default. When
# the Instance is unavailable (job not yet dequeued or Instance row
# missing), we derive a representative legacy status from
# ``admission_state`` and pass it through ``canonicalize_status`` so
# the WorkRecord carries an accurate status instead of always
# reporting ``"pending"``.
# Phase 7b: the canonical admission→legacy-status map lives in
# ``daemon.repositories.job_queue.models._ADMISSION_TO_LEGACY_STATUS``.
# Import it (rather than re-declaring it here) so the work-resolver
# stays in sync with the canonical definition. The local
# Uses the module-level ``_ADMISSION_TO_LEGACY_STATUS`` map (Phase 7b).
# call sites at lines ~1138 / ~1148 so the migration is a pure
# rename.
from daemon.repositories.job_queue.models import _ADMISSION_TO_LEGACY_STATUS


# ── Kind discrimination within the Task side ─────────────────────────────
# The Task table carries a ``task_type`` column that distinguishes
# report tasks (child→parent completion report) from the now-removed
# message turns (which are JobItems in Phase 3+). The WorkRecord
# exposes this as ``kind="report"`` — the only remaining Task-side kind
# after Phase 4 partial collapse. ``kind="job"`` continues to model
# JobItem-backed records.
#
# Phase 4 partial collapse (2026-07-06): the previous turn/report
# split is gone. ``TURN_TASK_TYPES`` was deleted because message
# turns now flow through ``job_create`` and surface as JobItems
# (``kind="job"``); the Task table only carries report rows
# (``kind="report"``). ``_kind_from_task_type`` is retained for
# defensive use by ``_task_to_record`` (e.g. legacy data ingested
# before the collapse) but only the ``"report"`` branch is exercised
# by the live code path.

REPORT_TASK_TYPES: frozenset[str] = frozenset({
    "process_report",
    "send_report",
})


def _kind_from_task_type(task_type: str | None) -> str:
    """Map a ``Task.task_type`` to a WorkRecord ``kind`` value.

    Phase 4 partial collapse (2026-07-06): only ``"report"`` is
    produced for known TaskType values. The previous
    ``"process_message"`` → ``"turn"`` mapping is gone (turns are
    now JobItems). Unknown / ``None`` defensively maps to
    ``"report"`` so a stale row ingested before the collapse still
    surfaces as a Task-backed record rather than ``None`` (callers
    expect a non-empty kind).

    * ``"process_report"``, ``"send_report"`` → ``"report"``
    * Unknown / ``None`` → ``"report"`` (defensive fallback)

    Args:
        task_type: The Task.task_type string value.

    Returns:
        The corresponding WorkRecord ``kind`` value. Always
        ``"report"`` post-collapse (Task rows are report-only).
    """
    if task_type in REPORT_TASK_TYPES:
        return "report"
    return "report"


# ── WorkRecord ─────────────────────────────────────────────────────────────
# The unified view-model. Mirrors the fields the virtual job UI surface
# wants to display (work_id, kind, status, instance/project/agent
# identity, terminal-state payload, creation timestamp) — i.e. one row
# per logical work unit regardless of which backing table it lives on.


@dataclass
class WorkRecord:
    """Unified view of a Task or JobItem resolved by ``work_id``.

    Fields:

    * ``work_id`` — stable cross-system UUID4 handle (the same value
      callers use to look the row up).
    * ``kind`` — ``"report"`` (child completion report — the only
      Task-side kind after Phase 4 partial collapse; report rows
      ride the same delivery pipeline as the legacy turns) or
      ``"job"`` (job queue). The resolver normalises away which
      table backs the row. Phase 4 (2026-07-06) removed the
      previous ``"turn"`` / ``"task"`` values; message turns are
      now JobItems (``kind="job"``) by way of the
      ``job_create`` → JobItem enqueue path. Report Tasks
      (``process_report`` / ``send_report``) are retained as
      ``kind="report"`` per AD-6 (RF2).
    * ``status`` — canonical status string (see ``work_status``). Task
      ``"running"`` becomes ``"processing"``; JobItem ``"processing"``
      stays ``"processing"``; both ``"paused"`` map onto ``"paused"``;
      ``"dead_letter"`` only ever appears on the JobItem side.
    * ``instance_id`` / ``project_id`` / ``agent_id`` — the three
      identity columns the virtual job surface needs to route callers
      to the right instance view. All optional because a JobItem
      created before instance spawn (``instance_id IS NULL``) or a
      task that outlived its instance (very rare) can have None.
    * ``result_summary`` — task-side parse of ``Task.result`` JSON, or
      direct copy of ``JobItem.result_summary`` (string).
    * ``error`` — task-side copy of ``Task.error``, or JobItem copy of
      ``JobItem.error_message``.
    * ``created_at`` — parsed ``datetime`` for both backing tables
      (Task stores ``datetime``; JobItem stores an ISO-8601 string).
      Stored as ``datetime | None`` because the ``_normalize_created_at``
      helper returns ``None`` for unparseable JobItem strings rather
      than raise.
    * ``started_at`` / ``completed_at`` — execution-timing fields
      sourced from the joined ``Instance`` row when one is available
      (Phase 1, Job as Queue Proxy). ``Instance.last_activity_at`` is
      the best proxy for ``started_at`` (it advances the first time
      the worker thread takes the work unit). ``Instance.updated_at``
      is the best proxy for ``completed_at`` — for terminal instances
      the last write before reaching a terminal status flips
      ``updated_at`` to the transition time. ``Instance.created_at``
      (ISO string) is a tertiary fallback when ``last_activity_at``
      is not set. ISO-8601 strings on the wire — either parsed from
      the Instance columns or sourced directly from the JobItem
      mirror columns (``JobItem.started_at`` / ``JobItem.completed_at``)
      which are still populated during Phase 1's transition period.
    * ``job_type`` / ``mission_liveness`` — Fix C read-model split.
      ``job_type`` is the JobItem-side discriminator (``"task"`` for
      mission, ``"message"`` for mirror; ``None`` for Task-backed
      rows). ``mission_liveness`` is the canonical status of the
      linked instance, populated ONLY for mirror rows so the renderer
      can distinguish a completed message-receipt from a still-running
      mission (I3, proxy-per-kind; closes the 28c6421b
      false-"everything finished" read). See the field-level
      docstring below for the degradation contract.
    """

    work_id: str
    kind: str          # "report" | "job" (Phase 4 partial collapse)
    status: str        # canonical status (via work_status.canonicalize_status)
    instance_id: str | None
    project_id: str | None
    agent_id: str | None
    result_summary: str | None
    error: str | None
    created_at: datetime | None
    started_at: str | None = None
    completed_at: str | None = None
    # Cross-system correlation key (Bug F1 fix, Phase 1, 2026-06-30).
    # JobItems populate this from ``metadata.message_id`` (stamped by
    # ``JobRepository.stamp_message_id`` in Phase 1 of the defer-seam
    # bugfix); Tasks populate this from the native ``task.message_id``
    # column. Both sides are string UUIDs assigned by ``MessageQueue``
    # at message-send time. Used as the second half of the
    # ``(instance_id, message_id)`` dedup key so a standalone Task turn
    # on an instance that also has a JobItem is no longer incorrectly
    # dropped by the ``list_work`` dedup (Bug F1).
    message_id: str | None = None
    # Fix C — read-model split (mission vs mirror rendering). Two
    # additive fields that complete the read model without changing any
    # existing ``status`` semantics:
    #
    # * ``job_type`` — the JobItem-side discriminator (``"task"`` for
    #   mission / spawn-instance work, ``"message"`` for the
    #   per-message mirror receipts). ``None`` for Task-backed records
    #   (reports) where the concept does not apply. ``kind`` above
    #   collapses both ``task`` and ``message`` JobItems onto
    #   ``"job"``; ``job_type`` is the second discriminator that lets
    #   consumers tell the two JobItem kinds apart without consulting
    #   the raw ``JobItem`` row. Mirrors the ``JobItem.job_type``
    #   column directly so the wire value is the same string callers
    #   already see in the SQLModel.
    #
    # * ``mission_liveness`` — the canonical status of the linked
    #   ``Instance`` row, ONLY meaningful for mirror (message-type)
    #   JobItems. For mission (task-type) JobItems the row's own
    #   ``status`` IS the liveness signal (Phase 1, Job as Queue
    #   Proxy), so this field stays ``None``. For mirror rows the
    #   receipt (``status``) and the mission (``mission_liveness``)
    #   are two different answers (I3 — proxy-per-kind) and the
    #   renderer needs both to avoid the 28c6421b false-"everything
    #   finished" read. ``None`` when there is no linked instance
    #   (``instance_id IS NULL`` on a queue-stage row) or when the
    #   instance lookup degraded (degradation-safe contract —
    #   read fails soft per the message_metadata precedent).
    job_type: str | None = None
    mission_liveness: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize this :class:`WorkRecord` to a JSON-friendly dict.

        Canonical shape for the virtual job surface — used by both
        :mod:`daemon.routers.work` (GET /api/work) and the
        :mod:`daemon.tools.job_queue` MCP tools. Field naming follows
        the JobItem.to_dict() convention (``work_id`` instead of
        ``job_id``; ``error`` for JobItem's ``error_message``) so the
        same dict shape works regardless of which table backs the row;
        ``kind`` distinguishes the two.

        ``created_at`` is normalised to an ISO-8601 string. Naive
        datetimes are coerced to UTC first so the output always
        carries the ``+00:00`` offset — frontend code can rely on
        tz-awareness without parsing the string.

        ``started_at`` / ``completed_at`` are passed through as ISO-8601
        strings (already ISO on the JobItem side; ``_isoformat_or_none``
        in :meth:`_job_to_record` formats the ``Instance.last_activity_at``
        ``datetime`` value).

        Fix C: ``job_type`` and ``mission_liveness`` are emitted on
        every record (``None`` for Task-backed records, by design —
        the values are not meaningful there). The renderer's split
        semantics are encoded in these two fields so the read model
        finally answers "is the work done?" with one answer per
        question instead of one ambiguous field.

        Returns:
            A plain ``dict`` mirroring the WorkRecord fields, with
            ``created_at`` coerced to ISO-8601 (or ``None``).
        """
        return {
            "work_id": self.work_id,
            "kind": self.kind,
            "status": self.status,
            "instance_id": self.instance_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "result_summary": self.result_summary,
            "error": self.error,
            "created_at": _serialize_created_at(self.created_at),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            # Phase 1 F1: ``message_id`` is the cross-system correlation
            # key (JobItem → Task matching). Surface it so callers can
            # correlate a WorkRecord back to the original message that
            # drove it (matches the ``message_id`` field on
            # ``MessageQueue`` / ``Task`` / ``JobItem.job_metadata``).
            "message_id": self.message_id,
            # Fix C — read-model split (additive). See the field
            # docstring above. ``None`` for Task-backed records.
            "job_type": self.job_type,
            "mission_liveness": self.mission_liveness,
        }


def _serialize_created_at(value: datetime | None) -> str | None:
    """Return an ISO-8601 string for ``value``, or ``None``.

    Task rows give us a tz-aware or naive ``datetime``; JobItem rows
    give us a tz-aware parsed string already. JSON serialisation
    needs a string. Naive datetimes are coerced to UTC before
    formatting so the output always carries the ``+00:00`` offset —
    frontend code can rely on tz-awareness without parsing the string
    for missing-offset edge cases.

    Args:
        value: A ``datetime`` (tz-aware or naive) or ``None``.

    Returns:
        An ISO-8601 string, or ``None`` if ``value`` is ``None``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# ── Reverse canonical → source status map ─────────────────────────────────
# The DB stores the *original* per-table status strings (Task
# ``running``, JobItem ``processing``, JobItem ``dead_letter``); the
# resolver's public surface speaks the canonical vocabulary. The
# ``status`` filter on ``list_work`` therefore needs the reverse map
# so a caller passing canonical ``"processing"`` finds both Task rows
# with ``status='running'`` and JobItem rows with
# ``status='processing'``. We precompute the map once from the
# forward map defined in ``work_status`` so adding a new status is a
# one-line change in ``work_status._STATUS_CANONICAL_MAP`` (the
# reverse map is rebuilt automatically on import — Python evaluates
# the module body once per process).
#
# ``dead_letter`` is the only canonical status with a single source
# (JobItem) — tasks don't have a dead-letter state, so a status filter
# for ``"dead_letter"`` excludes the Task side of the union.


def _build_canonical_to_sources() -> dict[str, set[str]]:
    """Build reverse map: canonical status → set of source status strings.

    Phase 4 cleanup: the JobItem source set is no longer used by the
    JobItem-side query (``_query_jobs`` consults ``admission_state``
    directly via ``_JOB_CANONICAL_TO_ADMISSION`` below). The map is
    still built for completeness and is used by ``_query_tasks`` to
    translate canonical status filters into ``Task.status IN (...)``
    predicates. Phase 4 cleanup removed the JobItem-only source keys
    ``"processing"`` and ``"dead_letter"`` from
    ``_STATUS_CANONICAL_MAP`` (the legacy ``JobItem.status`` column
    is no longer written in Phase 4+), so a canonical filter for
    ``"processing"`` now resolves to ``{"running"}`` (Task-only) and
    ``"dead_letter"`` resolves to ``{"dead"}`` (JobItem
    ``admission_state='dead'``, surfaced via the new
    ``_JOB_CANONICAL_TO_ADMISSION`` map).
    """
    out: dict[str, set[str]] = {}
    for source, canon in _STATUS_CANONICAL_MAP.items():
        out.setdefault(canon, set()).add(source)
    return out


_CANONICAL_TO_SOURCES: dict[str, set[str]] = _build_canonical_to_sources()


# ── Canonical → JobItem admission_state map ──────────────────────────────
# Phase 4 cleanup (Job as Queue Proxy): the JobItem query path
# (``_query_jobs``) consults the canonical ``admission_state`` column
# instead of the legacy ``status`` column. ``_CANONICAL_TO_SOURCES``
# above is keyed on the legacy source-status spelling; this parallel
# map is keyed on the canonical vocabulary and produces the set of
# ``AdmissionState`` values to feed into ``WHERE
# job_queue_items.admission_state IN (...)``.
#
# Mapping rationale (mirrors the dual-write invariant that Phase 2
# established):
#   ``pending``        → ``{'queued'}``   (PENDING    → QUEUED)
#   ``processing``     → ``{'active'}``   (PROCESSING → ACTIVE)
#   ``paused``         → ``{'active'}``   (PAUSED     → ACTIVE — pause
#                                          is an Instance concern; the
#                                          JobItem's lock is still held
#                                          and admission_state stays
#                                          ACTIVE per Plan §8.1)
#   ``completed``      → ``{'done'}``    (COMPLETED  → DONE)
#   ``failed``         → ``{'done'}``    (FAILED     → DONE)
#   ``cancelled``      → ``{'done'}``    (CANCELLED  → DONE)
#   ``dead_letter``    → ``{'dead'}``    (DEAD_LETTER → DEAD)
#
# This map is the JobItem-side analog of ``_CANONICAL_TO_SOURCES``: the
# Task-side resolver path uses ``_CANONICAL_TO_SOURCES`` to build a
# ``Task.status IN (...)`` filter (Task keeps its ``status`` column),
# while the JobItem-side resolver path uses ``_JOB_CANONICAL_TO_ADMISSION``
# to build a ``JobItem.admission_state IN (...)`` filter.
_JOB_CANONICAL_TO_ADMISSION: Final[dict[str, set[str]]] = {
    "pending": {AdmissionState.QUEUED.value},
    "processing": {AdmissionState.ACTIVE.value},
    "paused": {AdmissionState.ACTIVE.value},
    "completed": {AdmissionState.DONE.value},
    "failed": {AdmissionState.DONE.value},
    "cancelled": {AdmissionState.DONE.value},
    "dead_letter": {AdmissionState.DEAD.value},
}


# ── Per-token JobItem status filter (Bug F3 fix) ──────────────────────────
# Phase 2 of the defer-seam bugfix (F3): the canonical→admission map
# above is lossy (``completed`` / ``failed`` / ``cancelled`` all collapse
# onto ``admission_state='done'``), so a ``list_work(status="failed")``
# filter currently returns ZERO rows (the SQL ``WHERE admission_state IN
# ('done')`` matches done rows but the resolver has no further way to
# disambiguate the cause). The :class:`JobStatusFilter` dataclass carries
# the admission_state + an OPTIONAL ``terminal_reason`` discriminator so
# the JobItem query can build a precise ``WHERE admission_state = ? AND
# terminal_reason = ?`` (or ``... OR terminal_reason IS NULL`` for the
# completed-semantics default) per canonical status token.
#
# ``terminal_reason_null_allowed=True`` is reserved for the
# ``completed`` semantic: pre-7c rows with NULL ``terminal_reason`` are
# treated as completed per the lossy ``done → completed`` legacy map.
# ``failed`` / ``cancelled`` filters are strict — only rows whose
# ``terminal_reason`` was explicitly written by the terminal-write
# boundary match (a NULL ``terminal_reason`` on a done row is no longer
# ambiguous once the backfill has run; pre-backfill rows simply surface
# under completed and that is acceptable).
#
# Callers passing raw ``admission_state`` strings (e.g. ``"active"``)
# fall through to the backward-compat branch which builds a
# ``JobStatusFilter(admission_state=<value>)`` with NO ``terminal_reason``
# predicate — preserving the pre-F3 behaviour for those callers.
@dataclass(frozen=True)
class JobStatusFilter:
    """A single predicate on the JobItem side of ``list_work``.

    Carries the ``admission_state`` value to filter on and an optional
    ``terminal_reason`` discriminator for the disambiguation done in
    F3 (see module docstring). Multiple filters are combined with OR
    semantics; each filter's two clauses are ANDed.

    Attributes:
        admission_state: The ``JobItem.admission_state`` value to
            filter on (one of ``"queued"``, ``"active"``, ``"done"``,
            ``"dead"``).
        terminal_reason: Optional ``JobItem.terminal_reason`` value to
            AND with ``admission_state``. ``None`` means "no
            terminal_reason filter" — used for non-terminal canonical
            statuses (``pending`` / ``processing`` / ``paused`` /
            ``dead_letter``) and for the backward-compat raw
            ``admission_state`` fallback.
        terminal_reason_null_allowed: When True, a NULL
            ``terminal_reason`` on the row also satisfies this filter
            (used only for the ``completed`` semantic so pre-7c
            backfill rows with NULL ``terminal_reason`` still surface
            under ``status="completed"``).
    """

    admission_state: str
    terminal_reason: str | None = None
    terminal_reason_null_allowed: bool = False


def _canonical_to_job_filters(canonical_statuses: list[str]) -> list[JobStatusFilter]:
    """Translate canonical-status tokens into :class:`JobStatusFilter` rows.

    Each canonical token maps to one (or more, for ``paused``) filter
    rows. The result is what :meth:`_query_jobs` consumes — replacing
    the pre-F3 plain ``admission_state`` set with a structured
    per-token predicate that carries the ``terminal_reason``
    discriminator.

    Backward compatibility: tokens that are NOT canonical statuses
    (e.g. a caller passing ``"active"`` or ``"done"`` directly) are
    treated as raw ``admission_state`` values and produce a filter
    row WITHOUT a ``terminal_reason`` predicate — matching the pre-F3
    behaviour for those call sites. This is the
    "Maintain backward compatibility: Callers that pass raw
    ``admission_state`` strings must continue to work." contract
    from the F3 spec.

    Args:
        canonical_statuses: The deduplicated, lowercased tokens from
            the caller-supplied ``status`` filter (already split on
            ``,`` and stripped by the caller in :meth:`list_work`).

    Returns:
        A list of :class:`JobStatusFilter` — empty when the input is
        empty (caller did not pass a status filter).
    """
    filters: list[JobStatusFilter] = []
    for token in canonical_statuses:
        if token == "completed":
            # F3: completed semantics — admission_state='done' AND
            # (terminal_reason='completed' OR terminal_reason IS NULL).
            # The OR-NULL branch is the legacy compatibility hedge:
            # pre-7c rows have NULL ``terminal_reason`` and are
            # treated as completed per the lossy ``done → completed``
            # mapping in ``_ADMISSION_TO_LEGACY_STATUS``.
            filters.append(JobStatusFilter(
                admission_state=AdmissionState.DONE.value,
                terminal_reason="completed",
                terminal_reason_null_allowed=True,
            ))
        elif token == "failed":
            # F3: failed semantics — admission_state='done' AND
            # terminal_reason='failed' (strict, no NULL).
            filters.append(JobStatusFilter(
                admission_state=AdmissionState.DONE.value,
                terminal_reason="failed",
                terminal_reason_null_allowed=False,
            ))
        elif token == "cancelled":
            # F3: cancelled semantics — admission_state='done' AND
            # terminal_reason='cancelled' (strict, no NULL).
            filters.append(JobStatusFilter(
                admission_state=AdmissionState.DONE.value,
                terminal_reason="cancelled",
                terminal_reason_null_allowed=False,
            ))
        elif token in _JOB_CANONICAL_TO_ADMISSION:
            # pending / processing / paused / dead_letter — no
            # terminal_reason filter (non-terminal canonical statuses).
            for adm in _JOB_CANONICAL_TO_ADMISSION[token]:
                filters.append(JobStatusFilter(admission_state=adm))
        elif token in {s.value for s in AdmissionState}:
            # Backward-compat: caller passed a raw admission_state
            # value (e.g. ``"active"``). No terminal_reason filter —
            # matches the pre-F3 behaviour for these callers.
            filters.append(JobStatusFilter(admission_state=token))
        else:
            # Unknown token — defensive fallback. Pre-F3 this was
            # passed straight through as an admission_state value
            # (which produced zero matches on a strict schema). Keep
            # the same behaviour here so unknown tokens don't silently
            # start matching more rows after the F3 fix.
            filters.append(JobStatusFilter(admission_state=token))
    return filters


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Parse a JobItem-style ISO-8601 string into a ``datetime``.

    JobItem stores ``created_at`` as ``datetime.now(timezone.utc).isoformat()``
    which yields strings like ``"2026-06-27T13:09:05.827319+00:00"``. We
    use ``datetime.fromisoformat`` (Python 3.11+ handles the
    microsecond + timezone-offset form natively). Returns ``None`` for
    ``None`` input or unparseable strings so callers can degrade
    gracefully instead of raising out of ``list_work``.

    Args:
        value: A value expected to be an ISO-8601 string (or None).

    Returns:
        Parsed ``datetime`` with tzinfo, or ``None`` if the input was
        None or unparseable.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        logger.debug("work_resolver: unparseable created_at value %r", value)
        return None


def _normalize_sort_key(value: datetime | None) -> datetime:
    """Return a tz-aware UTC ``datetime`` suitable as a sort key.

    The :class:`WorkRecord` ``created_at`` field mixes two storage
    shapes: Task rows give us a SQLAlchemy-loaded ``datetime`` (SQLite
    strips tz info on round-trip, so it comes back naive), while
    JobItem rows give us a parsed ISO string (always tz-aware). Python
    refuses to compare naive and aware datetimes, so we coerce every
    sort key to aware-UTC here. ``None`` sorts as
    ``datetime.min`` (tz-aware) so missing timestamps sink to the end
    on a newest-first sort.

    Args:
        value: A ``datetime`` (tz-aware or naive) or ``None``.

    Returns:
        A tz-aware UTC ``datetime``. Never raises.
    """
    floor = datetime.min.replace(tzinfo=timezone.utc)
    if value is None:
        return floor
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_task_result_summary(task: Task) -> str | None:
    """Convert ``Task.result`` (JSON string) into a ``result_summary`` string.

    Mirrors the rule in ``daemon/routers/messages.py:251-263`` so the
    virtual job surface and the legacy ``GET /messages/{id}/status``
    route agree on the ``result_summary`` shape:

    * If ``Task.result`` is empty/None → ``None``.
    * If ``Task.result`` is a valid JSON string already → keep it.
    * If ``Task.result`` parses to any other JSON value → ``json.dumps``
      it (so the frontend always receives a string).
    * If parsing fails → fall back to the raw ``Task.result`` string.

    Args:
        task: The Task row whose ``result`` field to parse.

    Returns:
        A string summary, or ``None`` if ``task.result`` is empty.
    """
    raw = task.result
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    return parsed if isinstance(parsed, str) else json.dumps(parsed)


def _serialize_instance_datetime(value: Any) -> str | None:
    """Return an ISO-8601 string for an Instance timestamp value, or ``None``.

    The Instance table mixes ``datetime`` (``last_activity_at``) and
    ``str`` (``created_at`` / ``updated_at`` / ``paused_at``) timestamp
    shapes. ``JobResponse.started_at`` / ``JobResponse.completed_at``
    expect ISO-8601 strings. ``datetime`` values are coerced to UTC
    then formatted; ISO-8601 strings are returned verbatim; ``None`` or
    other types return ``None``.

    Args:
        value: A ``datetime``, ISO-8601 string, or ``None``.

    Returns:
        An ISO-8601 string, or ``None``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, str):
        # ISO-8601 strings pass through verbatim. Already UTC for
        # ``created_at`` / ``updated_at`` (set via
        # ``datetime.now(timezone.utc).isoformat()`` in the model).
        return value
    return None


def _instance_started_at(
    instance: "Instance | None",
    job: JobItem,
) -> str | None:
    """Resolve the ``started_at`` value for a JobItem-backed WorkRecord.

    Phase 5 (Job as Queue Proxy) precedence — Instance-side authoritative:

    1. ``Instance.last_activity_at`` — the worker pool's first heartbeat
       on the instance; the closest analogue to "work began".
    2. ``Instance.created_at`` — the Instance row's creation time as a
       fallback when ``last_activity_at`` has not been set yet (e.g. a
       freshly-spawned instance that has not yet checked in).
    3. Fallback ``None`` — the previous ``job.started_at`` JobItem mirror
       column was dropped in Phase B (see phase5-breakage-inventory
       B10). The Instance-side sources above are now authoritative; if
       neither is populated, the function returns ``None`` rather than
       reading the deprecated column.

    Args:
        instance: Optional pre-fetched Instance row. ``None`` falls
            through to the ``None`` fallback (no JobItem mirror to
            consult under Phase 5).
        job: The JobItem row. Retained for API symmetry with the
            completed_at resolver below; no attributes on it are read
            directly after Phase 5.

    Returns:
        An ISO-8601 string, or ``None`` if no timing source is available.
    """
    if instance is not None:
        value = _serialize_instance_datetime(instance.last_activity_at)
        if value is not None:
            return value
        value = _serialize_instance_datetime(instance.created_at)
        if value is not None:
            return value
    # Phase 5: ``job.started_at`` mirror column was dropped from the
    # JobItem model in Phase B. The Instance-side sources above are
    # authoritative; fall through to ``None`` rather than reading the
    # deprecated column.
    return None


def _instance_completed_at(
    instance: "Instance | None",
    *,
    instance_status_canonical: str,
    job: JobItem,
) -> str | None:
    """Resolve the ``completed_at`` value for a JobItem-backed WorkRecord.

    Phase 5 (Job as Queue Proxy) precedence — Instance-side authoritative:

    1. ``Instance.updated_at`` — only meaningful when the Instance is
       in a terminal state. ``updated_at`` is the last write before the
       terminal transition (the transaction that flips ``status`` and
       the Instance mirrors is one DB write — see ``_finalize_job_db_sync``
       Step 2).
    2. Fallback ``None`` — the previous ``job.completed_at`` JobItem mirror
       column was dropped in Phase B (see phase5-breakage-inventory
       B10). The Instance-side sources above are now authoritative; if
       the Instance is non-terminal (or absent) the function returns
       ``None`` rather than reading the deprecated column.

    A non-terminal Instance is treated as "not yet completed" — we
    surface ``None`` rather than the Instance ``updated_at`` so the
    API contract matches the ``JobResponse`` semantics (a non-terminal
    job should never report a completion time).

    Args:
        instance: Optional pre-fetched Instance row.
        instance_status_canonical: The canonical status already
            computed for this WorkRecord (``"processing"``, ``"completed"``,
            ``"failed"``, ``"cancelled"``, ``"paused"``, etc.).
        job: The JobItem row whose mirror ``completed_at`` is the fallback.

    Returns:
        An ISO-8601 string, or ``None``.
    """
    terminal_canonical = {
        "completed",
        "failed",
        "cancelled",
        "dead_letter",
    }
    if (
        instance is not None
        and instance_status_canonical in terminal_canonical
    ):
        value = _serialize_instance_datetime(instance.updated_at)
        if value is not None:
            return value
    # Phase 5: ``job.completed_at`` mirror column was dropped from the
    # JobItem model in Phase B. The Instance-side sources above are
    # authoritative; fall through to ``None`` rather than reading the
    # deprecated column.
    return None


# ── WorkResolverService ────────────────────────────────────────────────────
# Constructor-injected service (mirrors ``JobQueueService`` /
# ``JobQueueMgmtService`` in this package). Wired in ``daemon/api.py``
# during application startup using the same ``manager.engine`` /
# ``create_job_repository`` plumbing the JobQueue services already use.


class WorkResolverService:
    """Resolve and list virtual-job work records across Task and JobItem tables.

    The resolver is the single read API for the virtual job management
    surface. It treats ``task`` and ``job_queue_items`` as one logical
    collection of "work units", each identified by a stable ``work_id``
    (a UUID4 string assigned at row creation), and exposes them through
    the :class:`WorkRecord` view-model that speaks the canonical status
    vocabulary.

    Construction pattern matches ``JobQueueService``: caller passes the
    repositories directly. The service does not hold any state — every
    method call hits the DB through the underlying repositories.
    """

    def __init__(
        self,
        task_repo: "TaskRepository",
        job_repo: "JobRepository",
        instance_repo: "SQLModelInstanceRepository",
    ) -> None:
        """Initialize the resolver with the three repositories it needs.

        Args:
            task_repo: ``TaskRepository`` for ``task`` table lookups
                (used for the ``task`` side of ``resolve_work`` and
                ``list_work``). Must already be wired against the same
                engine the rest of the services use — sharing the
                engine avoids SQLite WAL lock contention.
            job_repo: ``JobRepository`` for ``job_queue_items`` lookups
                (the ``job`` side of the resolver).
            instance_repo: ``SQLModelInstanceRepository`` for the
                ``agent_id`` lookup on Task rows (Task has no
                ``agent_id`` column — agent identity lives on the
                instance). JobItem rows carry ``agent_id`` directly, so
                this repo is only consulted on the Task branch.
        """
        self._task_repo = task_repo
        self._job_repo = job_repo
        self._instance_repo = instance_repo

    # ── Public API ────────────────────────────────────────────────────────

    def resolve_work(self, work_id: str) -> WorkRecord | None:
        """Resolve a ``work_id`` to its :class:`WorkRecord`, or ``None``.

        Lookup order is task-first, then job. The Task branch is
        retained in Phase 4 partial collapse for **report** work_ids
        (``process_report`` / ``send_report`` Task rows). The JobItem
        branch handles message-driven work (turns are JobItems
        post-collapse).

        Args:
            work_id: The UUID4 work identifier (Task.work_id or
                JobItem.job_id).

        Returns:
            A populated :class:`WorkRecord`, or ``None`` if neither
            table has a row with this identifier.
        """
        # Task branch: indexed unique lookup on Task.work_id.
        task = self._task_repo.get_by_work_id(work_id)
        if task is not None:
            return self._task_to_record(task)

        # Job branch: PK lookup on JobItem.job_id. The JobItem
        # primary key is itself a UUID4 string (``job_id: str =
        # Field(default_factory=..., primary_key=True)``), so the
        # caller-supplied ``work_id`` is compared against the JobItem
        # PK directly — no separate work_id column on the JobItem
        # side, the PK IS the work_id.
        job = self._job_repo.get(work_id)
        if job is not None:
            return self._job_to_record(job)

        return None

    def list_work(
        self,
        project_id: str | None = None,
        instance_id: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        root_only: bool = True,
    ) -> list[WorkRecord]:
        """List work records matching the supplied filters, newest first.

        The four filters compose with AND semantics; passing ``None``
        for any of them leaves that dimension unrestricted. The query
        is the UNION of one Task SELECT and one JobItem SELECT, each
        driven by the same parameters:

        * ``project_id`` — applied on each table's ``project_id``
          column. Both tables are indexed on ``project_id`` (see
          ``idx_task_instance_id``'s neighbour indexes in
          ``task.models`` / ``idx_job_queue_items_project_status_deleted``
          in ``job_queue.models``).
        * ``instance_id`` — applied on each table's ``instance_id``
          column. Both indexed.
        * ``status`` — canonical status. The resolver translates the
          canonical value back to the source-status set (e.g.
          ``"processing"`` → ``{"running", "processing"}``) and
          applies an ``IN`` clause per table. ``"dead_letter"``
          matches no Task rows because Task has no dead-letter
          status — the Task SELECT simply returns the empty set
          for that filter (handled automatically by the IN-clause
          containing only ``"dead_letter"`` finding no matches).
          The value may also be a comma-separated list of canonical
          statuses (e.g. ``"pending,processing"``) — matches the
          legacy ``GET /api/jobs`` behaviour. Each token is mapped
          through the reverse canonical map independently and the
          resulting source-status sets are unioned into one ``IN``
          clause. Tokens are stripped of surrounding whitespace and
          lowercased; duplicates are collapsed.
        * ``kind`` — Phase 4 partial collapse (2026-07-06). The
          filter is now **two-valued**:

          * ``"job"`` — query only the JobItem table. This is the
            message-turn path post-collapse: turns are JobItems.
          * ``"report"`` — query only the Task table, restricted to
            ``task_type IN ("process_report", "send_report")``. The
            only remaining Task-side kind. Report Task rows are
            parent-bound and surface on the jober's management view
            per AD-6 (RF2 review).
          * ``"turn"`` / ``"task"`` — **removed** in Phase 4. The
            router rejects these values with HTTP 400. Pass
            ``kind=None`` (or omit) to return the union of JobItems
            and report Tasks.

        * ``root_only`` — P-A (2026-06-27) root-instance scoping.
          When ``True`` (default), drop any work whose backing
          instance has a non-null ``parent_id``. The jober manages
          work it bound to a root instance; child turns/reports are
          internal mechanics of that root's job and have **no link
          back to the originating ``job_id``** — surfacing them as
          first-class work units is noise that breaks the "one
          virtual job per root" mental model. The filter is applied
          **before** any future pagination/limit so a capped page
          cannot shrink unpredictably after the slice (reviewer W1
          in ``docs/plans/virtual-job-tool-completeness.md``).

          **Note (reviewer S2):** ``process_report`` and
          ``send_report`` Task rows are *parent-bound* — see
          ``daemon/services/child_reports.py:649-656`` which sets
          ``Task.instance_id = instance.parent_id`` when the child
          files its completion report. So reports surface under the
          root instance and are **kept** by ``root_only=True`` — only
          child-instance ``process_message`` turns (the ones that
          actually drove the child) are excluded. This is the
          intended behaviour: a child turn is the child's private
          execution step; a report is the parent's inbound
          notification about that step.

          JobItem-side filtering does a single batched lookup
          (``SELECT instance_id FROM instances WHERE instance_id IN
          (...) AND parent_id IS NOT NULL``) so we don't N+1 the
          per-row ``_lookup_instance`` we use on the Task side.
          JobItems with ``instance_id IS NULL`` (queue-stage rows,
          not yet dispatched to an instance) are kept — they have no
          parent relationship and are part of the jober's
          management view.

        * **P-C(i) — REMOVED in Phase 4 partial collapse.**
          The previous Task-turn deduplication that dropped
          ``process_message`` Tasks whose ``instance_id`` matched a
          JobItem (the ``job_create`` flow emission pair) is
          GONE: turns no longer surface as Tasks at all — they
          are JobItems from the entry point on. Reports
          (``kind="report"``) were never deduped and that
          behaviour is preserved.

          Single-record lookups via :meth:`resolve_work` are
          unaffected by Phase 4 (the Task branch still resolves
          report work_ids).

        Sort order is ``created_at DESC`` across the merged result.
        JobItem ``created_at`` is a string and Task ``created_at``
        is a ``datetime``; we normalise both to ``datetime`` via
        :func:`_parse_iso_datetime` so the sort key is uniform.
        Records with an unparseable ``created_at`` sort to the
        end (treated as ``datetime.min``).

        Args:
            project_id: Optional project ID filter.
            instance_id: Optional instance ID filter.
            status: Optional canonical-status filter
                (``"pending"``, ``"processing"``, ``"paused"``,
                ``"completed"``, ``"failed"``, ``"cancelled"``,
                ``"dead_letter"``). May also be a comma-separated
                list of canonical statuses (e.g.
                ``"pending,processing"``) to match the legacy
                ``GET /api/jobs`` behaviour — each token is
                independently translated to source statuses and
                unioned.
            kind: Optional kind filter (``"job"`` or
                ``"report"``). The legacy ``"turn"`` / ``"task"``
                values are removed in Phase 4 and rejected by the
                router.
            root_only: When ``True`` (default), drop work whose
                backing instance has a non-null ``parent_id``.
                ``False`` returns the union of root + child work
                (the pre-P-A behaviour, useful as a debug escape
                hatch).

        Returns:
            A list of :class:`WorkRecord` ordered by ``created_at``
            descending (newest first). Empty list if nothing matches.
        """
        source_statuses: set[str] | None = None
        canonical_statuses: list[str] = []
        # Bug F3 fix (Phase 2 of defer-seam bugfix, 2026-06-30): the
        # JobItem-side query now uses a structured per-token filter
        # (``_canonical_to_job_filters`` → ``JobStatusFilter``) instead
        # of a plain admission_state set. This lets ``completed`` /
        # ``failed`` / ``cancelled`` (which all collapse onto
        # ``admission_state='done'``) be disambiguated by
        # ``terminal_reason`` at SQL level. The pre-F3 lossy behaviour
        # is preserved for raw ``admission_state`` callers via the
        # ``JobStatusFilter(admission_state=<value>)`` backward-compat
        # branch in ``_canonical_to_job_filters``.
        job_status_filters: list[JobStatusFilter] | None = None
        if status is not None:
            # Split comma-separated canonical statuses (matches the
            # legacy ``GET /api/jobs`` behaviour in
            # ``daemon/routers/jobs_crud.py``). Whitespace is
            # stripped, tokens are lowercased, empties are dropped,
            # and order-preserving dedupe collapses duplicates so
            # ``"pending,pending"`` and ``" pending , PENDING "``
            # behave identically.
            canonical_statuses = list(dict.fromkeys(
                token.strip().lower()
                for token in status.split(",")
                if token.strip()
            ))
            # Map each canonical status back to its source-status
            # set (e.g. ``"processing"`` → ``{"running",
            # "processing"}``) and union across all tokens. Unknown
            # canonical values fall back to ``{status}`` per-token
            # — same defensive rule the single-status path used
            # pre-change, just applied per element. The legacy
            # ``/api/jobs`` router validates the list and 400s on
            # unknowns, so in practice every token here is known.
            unioned: set[str] = set()
            for canonical in canonical_statuses:
                unioned.update(_CANONICAL_TO_SOURCES.get(canonical, {canonical}))
            source_statuses = unioned
            # F3: build the parallel per-token JobStatusFilter list.
            # Each token produces one filter row (or more for
            # ``paused`` which spans ``active``). The Task-side query
            # still uses the unioned ``source_statuses`` set above —
            # Task keeps its native ``status`` column so the
            # canonical-vocabulary filter is exact there.
            job_status_filters = _canonical_to_job_filters(canonical_statuses)

        records: list[WorkRecord] = []

        # Translate kind filter into per-table predicates.
        # Phase 4 partial collapse (2026-07-06): the only valid kind
        # values are ``"job"`` / ``"report"`` (and ``None`` for the
        # union). ``"turn"`` / ``"task"`` are rejected by the router
        # and should never reach this method; we still defend against
        # them by mapping them to "no records" so a buggy caller does
        # not silently fall back to the union.
        #
        # ``"job"`` excludes the Task side entirely; ``"report"``
        # excludes the JobItem side. ``None`` queries both.
        query_tasks_table = kind is None or kind == "report"
        query_jobs_table = kind is None or kind == "job"

        # Map kind filter to a task_type set for the Task SELECT.
        # Phase 4 partial collapse: the Task query is ALWAYS report-only.
        # ``kind="report"`` filters to report task types; ``kind=None``
        # (union path) returns JobItems + all report Tasks but never
        # process_message Tasks (which are now JobItems). Router
        # rejects ``kind="turn"`` / ``kind="task"`` with HTTP 400; if
        # they reach here defensively, no Task rows match them anyway.
        task_type_filter: set[str] = set(REPORT_TASK_TYPES)

        if query_tasks_table:
            # Task table has no ``project_id`` column — the filter is
            # applied here against the WorkRecord (which already pulled
            # project_id from the matching instance row in
            # ``_task_to_record``). JobItem keeps its SQL-level filter
            # in ``_query_jobs`` because it does carry the column.
            #
            # P-A: ``root_only`` is applied HERE, against the
            # already-fetched instance, so we don't pay a second
            # round-trip to evaluate the parent_id guard. The lookup
            # is reused by ``_task_to_record`` via the optional
            # ``instance=`` parameter.
            #
            # Phase 4 partial collapse: the previous Task fetch
            # broadening (include ``completed`` so the
            # active-orchestration promotion could flip eligible rows
            # to ``processing``) is GONE — promotion is deleted and
            # turns are JobItems, so the broadened fetch is no longer
            # needed. The DB Task fetch now applies the source-status
            # filter verbatim.
            tasks = self._query_tasks(instance_id, source_statuses, task_type_filter)
            for task in tasks:
                instance = self._lookup_instance(task.instance_id)
                # Reviewer S2: report tasks are parent-bound (their
                # ``Task.instance_id`` is set to the ROOT instance by
                # ``child_reports.py:649-656``), so this guard only
                # excludes child-instance ``process_report`` Tasks —
                # but in practice reports are always parent-bound so
                # they always survive ``root_only=True``.
                if root_only and instance is not None and instance.parent_id:
                    continue
                record = self._task_to_record(task, instance=instance)
                if project_id is not None and record.project_id != project_id:
                    continue
                records.append(record)

        if query_jobs_table:
            jobs = self._query_jobs(project_id, instance_id, job_status_filters)
            if jobs:
                # Phase 1 (Job as Queue Proxy): batch-fetch Instance rows
                # for all distinct ``job.instance_id`` values so
                # ``_job_to_record`` doesn't N+1 the lookup. Each JobItem
                # becomes a single ``SELECT … WHERE instance_id IN (...)``
                # against the instances table — same pattern used by
                # ``_batch_child_instance_ids`` below for the ``root_only``
                # guard.
                instance_ids = {
                    j.instance_id for j in jobs if j.instance_id is not None
                }
                instances_by_id = self._batch_instances(instance_ids)
            else:
                instances_by_id = {}
            if root_only and jobs:
                # Batch-resolve which of the JobItems' backing
                # instances are children (parent_id IS NOT NULL).
                # One SQL round-trip instead of N+1; the result is
                # the small set of child instance_ids which we then
                # use to drop the corresponding JobItem rows.
                # JobItems with ``instance_id IS NULL`` (queue-stage,
                # not yet dispatched) have no parent relationship and
                # are kept — they ARE the jober's management surface.
                child_instance_ids = self._batch_child_instance_ids(
                    {j.instance_id for j in jobs if j.instance_id is not None}
                )
                records.extend(
                    self._job_to_record(j, instance=instances_by_id.get(j.instance_id))
                    for j in jobs
                    if j.instance_id is None or j.instance_id not in child_instance_ids
                )
            else:
                records.extend(
                    self._job_to_record(j, instance=instances_by_id.get(j.instance_id))
                    for j in jobs
                )

        # Phase 4 partial collapse (2026-07-06): the previous
        # Task-turn deduplication, F10 status-drift warning, and
        # active-orchestration status promotion are ALL removed.
        # Turns are no longer Task rows (they are JobItems from the
        # entry point on), so:
        #
        # * **P-C(i) dedup** — gone. JobItems now surface as their
        #   own ``kind="job"`` records; there are no
        #   ``kind="turn"`` Task rows to suppress.
        # * **F10 drift warning** — gone. The previous code logged
        #   when a dropped turn's status disagreed with the
        #   shadowing JobItem's status. With no dedup there is no
        #   drift to detect; JobFeedbackObserver still keeps
        #   JobItem statuses authoritative (via
        #   ``_job_to_record``), but no warning is needed because
        #   the surface now shows only one row per logical work
        #   unit by construction.
        # * **Active-orchestration promotion** — gone. JobItem
        #   ``kind="job"`` records source their status from the
        #   joined Instance row in ``_job_to_record`` (Phase 1,
        #   Job as Queue Proxy). Report Tasks (``kind="report"``)
        #   keep their own ``Task.status`` (they are
        #   parent-bound delivery records, not active
        #   orchestrations), so promotion is unnecessary for them
        #   too.
        #
        # The single-record ``resolve_work`` path is unaffected by
        # any of these deletions — Task-side record conversion in
        # ``_task_to_record`` still works for any
        # ``process_report`` / ``send_report`` row a caller
        # resolves directly.

        # Sort newest-first. Use ``_normalize_sort_key`` to coerce
        # every key to tz-aware UTC — Task rows come back from SQLite
        # as naive datetimes (the sqlite3 default datetime adapter
        # drops tz info on round-trip) while JobItem rows come back
        # as parsed ISO strings that are tz-aware; Python refuses to
        # compare the two without normalisation.
        records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)
        return records

    # ── Private helpers ───────────────────────────────────────────────────

    def _task_to_record(
        self,
        task: Task,
        instance: "Instance | None" = None,
    ) -> WorkRecord:
        """Build a :class:`WorkRecord` from a Task row.

        Neither ``agent_id`` nor ``project_id`` lives on the Task table —
        both are looked up from the matching ``instances`` row via
        ``instance_id`` (Task only carries the FK). The single
        ``_lookup_instance`` call returns the full Instance so we can
        pull both fields without a second round-trip. If the instance
        has been deleted (rare; only on project purge) the lookup
        returns ``None`` and the WorkRecord's ``agent_id`` and
        ``project_id`` are both ``None`` — callers see the work unit as
        orphaned but the rest of the fields still resolve.

        Args:
            task: The Task row to convert.
            instance: Optional pre-fetched :class:`Instance` for the
                Task's ``instance_id``. When supplied, the resolver
                skips its own ``_lookup_instance`` call — this is the
                P-A optimisation path so ``list_work`` can do the
                lookup once, perform the ``root_only`` parent-id
                guard, and reuse the same row to build the record.
                Defaults to ``None`` (resolver performs the lookup),
                which keeps ``resolve_work`` (single-row call site)
                unaffected.
        """
        if instance is None:
            instance = self._lookup_instance(task.instance_id)
        return WorkRecord(
            work_id=task.work_id,
            kind=_kind_from_task_type(task.task_type),
            status=canonicalize_status(task.status),
            instance_id=task.instance_id,
            project_id=instance.project_id if instance is not None else None,
            agent_id=instance.agent_id if instance is not None else None,
            result_summary=_parse_task_result_summary(task),
            error=task.error,
            created_at=task.created_at,
            # Phase 1 F1: surface ``task.message_id`` so the WorkRecord
            # carries the same correlation key as JobItem-backed records.
            # Tasks have a native ``message_id`` column populated at
            # message-send time (``worker_pool`` enqueue flow).
            message_id=task.message_id,
            # Fix C — ``job_type`` / ``mission_liveness`` are
            # explicitly ``None`` for Task-backed records. The Task
            # table has no mission/mirror concept (reports are
            # delivery records, not lifecycle proxies) so surfacing
            # these fields would be misleading — consumers should
            # branch on ``kind == "report"`` instead.
            job_type=None,
            mission_liveness=None,
        )

    def _job_to_record(
        self,
        job: JobItem,
        instance: "Instance | None" = None,
    ) -> WorkRecord:
        """Build a :class:`WorkRecord` from a JobItem row.

        Phase 1 (Job as Queue Proxy): execution state (``status``) is
        read from the joined ``Instance`` row when ``job.instance_id``
        is set. The JobItem ``status`` column is a mirror — the
        ``Instance.status`` is the authority. When ``instance_id`` is
        ``None`` (job is still in the queue, not yet dequeued to an
        instance), the canonical status is ``"pending"``.

        **``dead_letter`` exception** — the canonical vocabulary
        carries a ``"dead_letter"`` source value, but ``Instance``
        has no equivalent (Phase 2 introduces ``admission_state='dead'``
        to model the DLQ outcome; Phase 4 will rewrite the canonical
        map entry). For Phase 1 the JobItem ``status`` mirror is the
        only source for this state: when the JobItem row reports
        ``dead_letter`` we surface ``"dead_letter"`` regardless of
        whether the Instance exists or what its status is. This is a
        documented exception to the "Instance-authoritative" rule
        that lands in Phase 4 when ``admission_state`` arrives.

        ``agent_id`` and ``project_id`` come off the JobItem row
        natively (they're queue-payload columns, populated at job
        creation). ``created_at`` also stays from the JobItem — it's a
        queue-creation timestamp, not execution state.

        ``result_summary`` / ``error`` are still sourced from the
        JobItem mirror columns in this phase. ``Instance`` does not
        currently model result/error columns; those storage locations
        will land with the column-drop work in Phase 5 (the JobItem
        columns are deprecated in Phase 4 and dropped in Phase 5 per
        the migration plan). Until then the JobItem mirror is the only
        available source. The exit criterion is satisfied for ``status``
        (the load-bearing execution-state field) — the mirror is still
        read for result/error only because there is no Instance column
        to source them from yet.

        Field-name mapping from JobItem → WorkRecord:

        * ``result_summary`` ← ``JobItem.result_summary`` (already a
          string in the DB — no JSON parse required).
        * ``error`` ← ``JobItem.error_message`` (the JobItem column is
          named ``error_message`` for historical reasons; the
          WorkRecord view-model calls it ``error`` because that's what
          the virtual job surface wants to display).
        * ``created_at`` ← ISO-8601 string, parsed to ``datetime``
          via :func:`_parse_iso_datetime` for sort compatibility.

        Args:
            job: The JobItem row to convert.
            instance: Optional pre-fetched :class:`Instance` for the
                job's ``instance_id``. When supplied, the resolver
                skips its own ``_lookup_instance`` call — this is the
                P-1 batching optimisation path so ``list_work`` and
                any other batched caller can do the lookup once and
                reuse the same row to build each record. Defaults to
                ``None`` (resolver performs the lookup), which keeps
                ``resolve_work`` (single-row call site) unaffected.
        """
        # Phase 4 cleanup: the frozen ``status`` column is no longer
        # written — ``admission_state`` is the sole authority. Dead
        # jobs have ``admission_state='dead'``.
        if job.admission_state == AdmissionState.DEAD.value:
            status = "dead_letter"
        elif job.admission_state == AdmissionState.DONE.value:
            # Phase 7c: terminal_reason discriminator. ``done`` rows
            # cover three legacy terminal states — completed / failed
            # / cancelled — and a fourth instance-cascade value
            # (``"aborted"``). The lossy ``_ADMISSION_TO_LEGACY_STATUS``
            # map collapses all of them onto ``"completed"``. The
            # ``Instance.status`` source (used below for non-DONE
            # states) is unreliable for ``done`` rows because the
            # instance may have completed naturally before a cancel
            # signal arrived (``Instance.status='completed'`` but the
            # job is semantically cancelled) or may have been
            # terminated by the instance-cascade (``Instance.status``
            # reflects the terminate action, not the cause). So for
            # ``done`` rows we source status from ``terminal_reason``
            # directly, with a backward-compat fallback to the lossy
            # legacy map for pre-7c rows where ``terminal_reason IS NULL``.
            terminal_reason = getattr(job, "terminal_reason", None)
            if terminal_reason:
                status = canonicalize_status(terminal_reason)
            else:
                # Pre-7c rows: ``terminal_reason`` is NULL. Fall back
                # to the lossy legacy ``done → completed`` mapping
                # so existing API consumers continue to see a valid
                # status string (the original Phase 5 behaviour).
                status = canonicalize_status(
                    _ADMISSION_TO_LEGACY_STATUS.get(
                        job.admission_state, "pending"
                    )
                )
        else:
            # Non-terminal admission states (``queued`` / ``active``):
            # source execution status from the joined Instance when
            # possible. ``_lookup_instance`` is defensive — returns
            # ``None`` for a missing instance row (e.g. instance was
            # deleted) or a transient DB error, in which case we fall
            # back to the JobItem mirror column so the read still
            # produces a valid status.
            #
            # Two cases funnel through the same fallback:
            #
            # * ``job.instance_id is None`` — job is still in the
            #   queue, not yet dequeued to an instance. The
            #   ``admission_state`` is the only signal we have; we
            #   canonicalize it instead of hardcoding ``"pending"``
            #   (the previous behaviour discarded the real status for
            #   already-terminal jobs that had not yet been dispatched
            #   to an instance — e.g. a JobItem seeded with
            #   ``status="completed"`` and ``instance_id=None`` would
            #   return ``"pending"`` and break the SSE terminal-status
            #   detection loop).
            # * ``job.instance_id is not None`` but the Instance row
            #   is missing (orphan / deleted) — same fallback to the
            #   ``admission_state`` map, canonicalized.
            # Only read the instance status when the job is ACTIVELY running.
            # A queued message job already has instance_id stamped (set at
            # enqueue time), but it is NOT running yet — it must show
            # "pending", not the target instance's status.
            if (
                job.admission_state == AdmissionState.ACTIVE.value
                and job.instance_id is not None
            ):
                if instance is None:
                    instance = self._lookup_instance(job.instance_id)
                if instance is not None:
                    status = canonicalize_status(instance.status)
                else:
                    status = canonicalize_status(
                        _ADMISSION_TO_LEGACY_STATUS.get(
                            job.admission_state, "pending"
                        )
                    )
            else:
                # When admission_state == "queued", fall through to the
                # admission-state mapping, which correctly returns "pending".
                status = canonicalize_status(
                    _ADMISSION_TO_LEGACY_STATUS.get(
                        job.admission_state, "pending"
                    )
                )

        # Fix C — read-model split (mission vs mirror). The
        # JobItem ``job_type`` column is the discriminator between the
        # two render kinds: ``"task"`` is a mission (the JobItem is the
        # lifecycle proxy of the spawned instance) and ``"message"`` is
        # a mirror (the JobItem is a per-message receipt — the
        # instance's lifecycle is independent of the mirror).
        #
        # Default to ``"task"`` for legacy JobItem rows that predate
        # the ``job_type`` column (defensive — ``getattr(..., "task")``
        # matches the historical SQLModel default in
        # ``daemon/repositories/job_queue/models.py``).
        job_type = getattr(job, "job_type", "task") or "task"

        # Fix C — ``mission_liveness`` consult (Part 1). For mirror
        # (message-type) JobItems the receipt (``status`` above) and
        # the mission (the linked instance) are TWO different answers
        # to "is the work done?" — I3, proxy-per-kind — and the renderer
        # needs both to avoid the 28c6421b false-"everything finished"
        # read where a completed mirror beside a still-running mission
        # collapsed to two ``completed`` rows.
        #
        # Consult the linked Instance's ``status`` regardless of the
        # mirror's own ``admission_state`` — a DONE mirror's mission
        # can still be active (the parent instance keeps living past
        # the receipt). For mission (task-type) JobItems the row's own
        # ``status`` IS the liveness signal (Phase 1, Job as Queue
        # Proxy) so this field stays ``None``.
        #
        # W4 hazard (DLQ-replay × instance-revive): a DEAD mission
        # row's derived ``status`` is hard-coded to ``"dead_letter"``
        # above — instance liveness does NOT override that for
        # mission rows. Mirrors in DEAD (``dead_letter`` status) still
        # get a ``mission_liveness`` because the receipt-vs-mission
        # split still applies (and the rare DEAD-mirror case is
        # orthogonal to the W4 guard).
        mission_liveness: str | None = None
        if job_type == "message" and job.instance_id is not None:
            # The batched list path (``list_work``) pre-fetches
            # ``instance`` via ``_batch_instances`` for every job in
            # the page, so ``instance`` is the authoritative source
            # here — no per-row lookup on the hot path. The single-row
            # ``resolve_work`` path passes ``instance=None``; for
            # mirror rows we fall back to ``_lookup_instance`` so the
            # field is populated consistently across both call sites.
            # ``_lookup_instance`` is degradation-safe (catches
            # ``SQLAlchemyError``, logs a warning, returns ``None``) —
            # the same pattern the existing active-state path already
            # uses (defensive contract; see the message_metadata repo
            # access precedent referenced in the Fix C spec).
            if instance is None:
                instance = self._lookup_instance(job.instance_id)
            if instance is not None:
                mission_liveness = canonicalize_status(instance.status)
            # else: lookup degraded; ``mission_liveness`` stays
            # ``None`` and the renderer falls back to the
            # receipt-only view (current behaviour stands).

        return WorkRecord(
            work_id=job.job_id,
            kind="job",
            status=status,
            instance_id=job.instance_id,
            project_id=job.project_id,
            agent_id=job.agent_id,
            # Phase 5: ``job.result_summary`` and ``job.error_message``
            # mirror columns were dropped from the JobItem model in
            # Phase B. Source from the Instance/WorkRecord resolver
            # path; pass ``None`` here so the caller can rely on the
            # resolver's derived values instead of stale mirror fields.
            result_summary=None,
            error=None,
            created_at=_parse_iso_datetime(job.created_at),
            # Phase 1 F1: surface ``message_id`` from
            # ``job.job_metadata['message_id']`` (stamped by
            # ``JobRepository.stamp_message_id`` at the end of the
            # ``job_create`` enqueue flow). The metadata dict is
            # accessed defensively — pre-Phase-1 rows may have an empty
            # ``metadata`` dict and we surface ``None`` instead of
            # raising ``KeyError``. JobItems created before Phase 1
            # will have ``message_id=None`` on the WorkRecord; the F1
            # dedup rule "Task turns with ``message_id=None`` are never
            # suppressed" means a Task turn on a pre-Phase-1 JobItem
            # instance is never deduped, which matches the
            # "never-orphan" intent of the F1 fix.
            message_id=(
                (job.job_metadata or {}).get("message_id")
                if isinstance(job.job_metadata, dict) else None
            ),
            # Timing: prefer the Instance columns when an Instance row
            # was provided (or just looked up above). ``last_activity_at``
            # is the worker's first heartbeat, which is the closest
            # analogue to "work began". ``updated_at`` is the last
            # write; for a terminal Instance that flips to terminal in
            # one transaction, ``updated_at`` IS the completion time.
            # Both fields degrade to the JobItem mirror when no
            # Instance is available — the mirror is still populated
            # during Phase 1's transition period.
            started_at=_instance_started_at(instance, job),
            completed_at=_instance_completed_at(instance, instance_status_canonical=status, job=job),
            # Fix C — additive read-model split fields. ``job_type``
            # is the JobItem-side discriminator; ``mission_liveness``
            # is the canonical status of the linked Instance for
            # mirror rows (and ``None`` for mission rows). Both
            # behaviour and degradation contract are documented at
            # the field declaration and at the top of this method.
            job_type=job_type,
            mission_liveness=mission_liveness,
        )

    def _lookup_instance(self, instance_id: str | None) -> "Instance | None":
        """Return the :class:`Instance` row for ``instance_id``, or ``None``.

        Task rows have no ``agent_id`` or ``project_id`` columns —
        both columns live on the ``instances`` table and are joined
        transitively through
        ``task.instance_id → instances.instance_id``. This helper
        makes the lookup explicit, returns the full Instance so
        callers can pull both fields without a second round-trip, and
        centralises the ``None``-handling (Task rows always have
        ``instance_id`` set in practice, but the helper defends
        against the ``None`` case for type-safety).
        """
        if instance_id is None:
            return None
        try:
            instance = self._instance_repo.get(instance_id)
        except SQLAlchemyError as exc:
            # Defensive: a transient DB error during the instance
            # lookup should not blow up the whole resolve_work call.
            # Log at warning so it's visible in ops but treat as
            # "instance unknown" for this row (both agent_id and
            # project_id degrade to None). Narrow catch — programmer
            # mistakes (AttributeError, TypeError, etc.) must propagate
            # so they aren't masked as DB outages.
            logger.warning(
                "work_resolver: instance lookup failed for %r: %s",
                instance_id,
                exc,
            )
            return None
        return instance

    def _query_tasks(
        self,
        instance_id: str | None,
        source_statuses: set[str] | None,
        task_types: set[str] | None = None,
    ) -> list[Task]:
        """Run the Task SELECT for ``list_work`` and return the rows.

        The Task table carries no ``project_id`` column (project
        identity lives transitively on the matching ``instances`` row,
        looked up per-row by ``_task_to_record``). The
        ``project_id`` filter is therefore applied post-fetch inside
        :meth:`list_work`, not at SQL level — see the comment there
        for why.

        The optional ``task_types`` parameter carries the Phase 4
        ``kind`` filter translated into Task-side values (e.g.
        ``"turn"`` → ``{"process_message"}``). ``None`` (or empty
        set) means no task_type filter — the SELECT returns all task
        rows. Phase 4 split ``kind="task"`` into ``turn`` /
        ``report`` so the SQL-level IN-clause is now the primary
        mechanism for separating message turns from child-completion
        reports at the WorkRecord boundary.

        Uses a single ``SQLModelSession`` so the read is consistent
        within the request (no torn reads across the task / job union
        — see the cross-table consistency note below).

        Cross-table consistency: the two SELECTs (Task and JobItem) run
        in **separate** sessions because each repository owns its own
        ``SQLModelSession(self.engine)``. SQLite WAL gives read
        snapshot isolation per transaction so the two SELECTs see
        consistent-but-not-simultaneous views. This is acceptable for
        the virtual job surface (the API is a UI feed, not a
        consistency-critical ledger) and matches the existing
        ``task_repo.get_by_message`` + ``job_repo.get`` patterns used
        in ``tools/job_queue.py`` and ``routers/messages.py``.
        """
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(self._task_repo.engine) as session:
            stmt = select(Task)
            if instance_id is not None:
                stmt = stmt.where(Task.instance_id == instance_id)
            if source_statuses is not None:
                stmt = stmt.where(Task.status.in_(source_statuses))
            if task_types is not None:
                stmt = stmt.where(Task.task_type.in_(task_types))
            stmt = stmt.order_by(col(Task.created_at).desc())
            return list(session.exec(stmt))

    def _query_jobs(
        self,
        project_id: str | None,
        instance_id: str | None,
        status_filters: list[JobStatusFilter] | None,
    ) -> list[JobItem]:
        """Run the JobItem SELECT for ``list_work`` and return the rows.

        ``JobItem.deleted_at IS NULL`` is enforced unconditionally —
        soft-deleted jobs are invisible to the virtual job surface
        (matches the default behaviour of ``JobRepository.list``).

        Phase 4 cleanup (Job as Queue Proxy): the SQL filter targets
        the canonical ``admission_state`` column instead of the
        legacy ``status`` column. The legacy ``status`` column is no
        longer written (Phase 5 drops it outright), so reads against
        it would only ever see the INSERT default — i.e. the column
        is effectively frozen. The resolver pre-translates each
        canonical status filter (``"processing"`` / ``"dead_letter"``
        / ...) into the corresponding ``AdmissionState`` value(s) via
        ``_JOB_CANONICAL_TO_ADMISSION`` and feeds the result here as
        ``status_filters``.

        Bug F3 fix (Phase 2 of defer-seam bugfix, 2026-06-30): the
        ``status_filters`` parameter is now a list of
        :class:`JobStatusFilter` rows instead of a plain
        ``admission_state`` set. Each filter row can carry an
        OPTIONAL ``terminal_reason`` discriminator so the lossy
        ``done → completed`` map can be disambiguated at SQL level
        for the ``completed`` / ``failed`` / ``cancelled`` canonical
        statuses. The Task-side query path is unchanged (Task keeps
        its ``status`` column).

        Each filter row is AND-combined (admission_state + optional
        terminal_reason) and the rows themselves are OR-combined —
        matching the ``list_work`` "match any of the requested
        statuses" semantics. ``None`` (or empty list) means no status
        filter — the SELECT returns all non-deleted rows.
        """
        from sqlalchemy import and_ as sql_and
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(self._job_repo.engine) as session:
            stmt = select(JobItem).where(JobItem.deleted_at.is_(None))
            if project_id is not None:
                stmt = stmt.where(JobItem.project_id == project_id)
            if instance_id is not None:
                stmt = stmt.where(JobItem.instance_id == instance_id)
            if status_filters:
                # Build the OR-of-ANDs predicate. Each filter row is
                # one branch of the outer OR; the row's clauses are
                # ANDed internally. The ``terminal_reason_null_allowed``
                # flag on a filter row expands the AND with an
                # ``OR terminal_reason IS NULL`` clause so
                # pre-7c rows (NULL ``terminal_reason``) still match
                # the ``completed`` semantic.
                branches = []
                for f in status_filters:
                    if f.terminal_reason is not None:
                        if f.terminal_reason_null_allowed:
                            branches.append(
                                sql_and(
                                    JobItem.admission_state == f.admission_state,
                                    or_(
                                        JobItem.terminal_reason == f.terminal_reason,
                                        JobItem.terminal_reason.is_(None),
                                    ),
                                )
                            )
                        else:
                            branches.append(
                                sql_and(
                                    JobItem.admission_state == f.admission_state,
                                    JobItem.terminal_reason == f.terminal_reason,
                                )
                            )
                    else:
                        # No terminal_reason filter — just the
                        # admission_state predicate.
                        branches.append(JobItem.admission_state == f.admission_state)
                if branches:
                    stmt = stmt.where(or_(*branches))
            stmt = stmt.order_by(col(JobItem.created_at).desc())
            return list(session.exec(stmt))

    def _batch_child_instance_ids(self, instance_ids: set[str]) -> set[str]:
        """Return the subset of ``instance_ids`` whose instances are children.

        P-A: ``list_work(root_only=True)`` needs to drop JobItem rows
        whose backing instance has a non-null ``parent_id``. JobItem
        carries ``instance_id`` directly so there is no per-row
        instance lookup in the JobItem branch of ``list_work`` — we
        resolve parentage in one batched SELECT against the
        ``instances`` table:

            SELECT instance_id FROM instances
             WHERE instance_id IN (...)
               AND parent_id IS NOT NULL

        The returned set is the child-instance ids, which the caller
        uses to filter out the corresponding JobItems. An empty input
        set short-circuits to an empty result without hitting the DB.

        Args:
            instance_ids: The distinct ``JobItem.instance_id`` values
                in the current page (already filtered to non-None).

        Returns:
            The subset of ``instance_ids`` whose ``Instance.parent_id``
            is non-null. An empty set means "no children in this set".
        """
        if not instance_ids:
            return set()
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(self._instance_repo.engine) as session:
            stmt = select(Instance.instance_id).where(
                Instance.instance_id.in_(instance_ids),
                Instance.parent_id.isnot(None),
            )
            return {row for row in session.exec(stmt)}

    def _batch_instances(
        self,
        instance_ids: set[str | None],
    ) -> dict[str, "Instance"]:
        """Return a ``{instance_id: Instance}`` map for the requested ids.

        Phase 1 (Job as Queue Proxy): one batched SELECT replaces the
        per-row ``_lookup_instance`` call that ``_job_to_record`` would
        otherwise make. Used by ``list_work`` so the JobItem branch of
        a 50-row page resolves all backing instances in a single
        round-trip instead of 50.

        ``None`` and empty inputs short-circuit to an empty dict
        without hitting the DB. The map only contains ids that
        actually exist in the database — missing ids are silently
        dropped (the caller treats them as "instance unknown" and
        falls back to the JobItem mirror status inside
        ``_job_to_record``).

        Args:
            instance_ids: The distinct ``JobItem.instance_id`` values
                to fetch. ``None`` entries are filtered out before the
                query (the Instance PK is never null).

        Returns:
            A dict mapping ``instance_id`` → ``Instance``. Missing ids
            are absent from the dict (caller treats them as unknown).
        """
        valid_ids = {iid for iid in instance_ids if iid is not None}
        if not valid_ids:
            return {}
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(self._instance_repo.engine) as session:
            stmt = select(Instance).where(Instance.instance_id.in_(valid_ids))
            return {row.instance_id: row for row in session.exec(stmt)}


__all__ = ["WorkRecord", "WorkResolverService", "JobStatusFilter"]