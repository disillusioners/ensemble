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
from sqlalchemy.exc import SQLAlchemyError

from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task

from .work_status import _STATUS_CANONICAL_MAP, canonicalize_status

if TYPE_CHECKING:
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.task.repository import TaskRepository

logger = logging.getLogger(__name__)


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
    * ``kind`` — ``"task"`` (worker pool) or ``"job"`` (job queue). The
      resolver normalises away which table backs the row.
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
    """

    work_id: str
    kind: str          # "task" or "job"
    status: str        # canonical status (via work_status.canonicalize_status)
    instance_id: str | None
    project_id: str | None
    agent_id: str | None
    result_summary: str | None
    error: str | None
    created_at: datetime | None


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
    """Build reverse map: canonical status → set of source status strings."""
    out: dict[str, set[str]] = {}
    for source, canon in _STATUS_CANONICAL_MAP.items():
        out.setdefault(canon, set()).add(source)
    return out


_CANONICAL_TO_SOURCES: dict[str, set[str]] = _build_canonical_to_sources()


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

        Lookup order is task-first, then job — this matches the order
        used by the unified dispatcher (messages create Task rows; only
        legacy dispatch-queue jobs create JobItem rows). The task-first
        order means a Task row's ``work_id`` shadows any JobItem row
        that happens to share the value (which shouldn't happen because
        ``task.work_id`` is ``unique=True`` and JobItem ``job_id`` is
        its own UUID4, but defending the order is cheaper than
        reasoning about UUID collisions).

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
        * ``kind`` — ``"task"`` queries only the Task table;
          ``"job"`` queries only JobItem; ``None`` (default)
          queries both.

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
                ``"dead_letter"``).
            kind: Optional kind filter (``"task"``, ``"job"``).

        Returns:
            A list of :class:`WorkRecord` ordered by ``created_at``
            descending (newest first). Empty list if nothing matches.
        """
        source_statuses: set[str] | None = None
        if status is not None:
            # Unknown canonical values (forward map returned the input
            # unchanged) collapse to ``{status}`` — the SQL IN-clause
            # then matches zero rows because the per-table status
            # strings never equal the canonical string for statuses
            # the resolver knows about. This is intentional: an
            # unknown status filter is a programmer error, not user
            # input, and silently returning [] is the safe default.
            source_statuses = _CANONICAL_TO_SOURCES.get(status, {status})

        records: list[WorkRecord] = []

        if kind != "job":
            # Task table has no ``project_id`` column — the filter is
            # applied here against the WorkRecord (which already pulled
            # project_id from the matching instance row in
            # ``_task_to_record``). JobItem keeps its SQL-level filter
            # in ``_query_jobs`` because it does carry the column.
            tasks = self._query_tasks(instance_id, source_statuses)
            records.extend(
                record
                for record in (self._task_to_record(t) for t in tasks)
                if project_id is None or record.project_id == project_id
            )

        if kind != "task":
            jobs = self._query_jobs(project_id, instance_id, source_statuses)
            records.extend(self._job_to_record(j) for j in jobs)

        # Sort newest-first. Use ``_normalize_sort_key`` to coerce
        # every key to tz-aware UTC — Task rows come back from SQLite
        # as naive datetimes (the sqlite3 default datetime adapter
        # drops tz info on round-trip) while JobItem rows come back
        # as parsed ISO strings that are tz-aware; Python refuses to
        # compare the two without normalisation.
        records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)
        return records

    # ── Private helpers ───────────────────────────────────────────────────

    def _task_to_record(self, task: Task) -> WorkRecord:
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
        """
        instance = self._lookup_instance(task.instance_id)
        return WorkRecord(
            work_id=task.work_id,
            kind="task",
            status=canonicalize_status(task.status),
            instance_id=task.instance_id,
            project_id=instance.project_id if instance is not None else None,
            agent_id=instance.agent_id if instance is not None else None,
            result_summary=_parse_task_result_summary(task),
            error=task.error,
            created_at=task.created_at,
        )

    def _job_to_record(self, job: JobItem) -> WorkRecord:
        """Build a :class:`WorkRecord` from a JobItem row.

        JobItem stores ``agent_id`` directly (the column is required,
        populated at job creation from the originating agent), so no
        instance lookup is needed on this branch. Field-name mapping
        from JobItem → WorkRecord:

        * ``result_summary`` ← ``JobItem.result_summary`` (already a
          string in the DB — no JSON parse required).
        * ``error`` ← ``JobItem.error_message`` (the JobItem column is
          named ``error_message`` for historical reasons; the
          WorkRecord view-model calls it ``error`` because that's what
          the virtual job surface wants to display).
        * ``created_at`` ← ISO-8601 string, parsed to ``datetime``
          via :func:`_parse_iso_datetime` for sort compatibility.
        """
        return WorkRecord(
            work_id=job.job_id,
            kind="job",
            status=canonicalize_status(job.status),
            instance_id=job.instance_id,
            project_id=job.project_id,
            agent_id=job.agent_id,
            result_summary=job.result_summary,
            error=job.error_message,
            created_at=_parse_iso_datetime(job.created_at),
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
    ) -> list[Task]:
        """Run the Task SELECT for ``list_work`` and return the rows.

        The Task table carries no ``project_id`` column (project
        identity lives transitively on the matching ``instances`` row,
        looked up per-row by ``_task_to_record``). The
        ``project_id`` filter is therefore applied post-fetch inside
        :meth:`list_work`, not at SQL level — see the comment there
        for why.

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
            stmt = stmt.order_by(col(Task.created_at).desc())
            return list(session.exec(stmt))

    def _query_jobs(
        self,
        project_id: str | None,
        instance_id: str | None,
        source_statuses: set[str] | None,
    ) -> list[JobItem]:
        """Run the JobItem SELECT for ``list_work`` and return the rows.

        ``JobItem.deleted_at IS NULL`` is enforced unconditionally —
        soft-deleted jobs are invisible to the virtual job surface
        (matches the default behaviour of ``JobRepository.list``).
        """
        from sqlmodel import Session as SQLModelSession

        with SQLModelSession(self._job_repo.engine) as session:
            stmt = select(JobItem).where(JobItem.deleted_at.is_(None))
            if project_id is not None:
                stmt = stmt.where(JobItem.project_id == project_id)
            if instance_id is not None:
                stmt = stmt.where(JobItem.instance_id == instance_id)
            if source_statuses is not None:
                stmt = stmt.where(JobItem.status.in_(source_statuses))
            stmt = stmt.order_by(col(JobItem.created_at).desc())
            return list(session.exec(stmt))


__all__ = ["WorkRecord", "WorkResolverService"]