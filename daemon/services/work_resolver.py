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

from daemon.repositories.instance.models import Instance
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.task.models import Task

from .work_status import _STATUS_CANONICAL_MAP, canonicalize_status

if TYPE_CHECKING:
    from daemon.repositories.instance.repository import SQLModelInstanceRepository
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.task.repository import TaskRepository

logger = logging.getLogger(__name__)


# ── Kind discrimination within the Task side ─────────────────────────────
# The Task table carries a ``task_type`` column that distinguishes
# message turns (user→agent) from report tasks (child→parent completion
# report). The WorkRecord exposes this as ``kind="turn"`` vs
# ``kind="report"``. ``kind="task"`` is kept as a backward-compatible
# alias for clients that haven't migrated to the split vocabulary and
# matches BOTH process_message AND process_report rows.
#
# Phase 4 (2026-06-27): split kind into turn/report based on
# ``Task.task_type``. See ``daemon/repositories/task/models.py``
# (TaskType enum, lines 19-34).

TURN_TASK_TYPES: frozenset[str] = frozenset({"process_message"})

REPORT_TASK_TYPES: frozenset[str] = frozenset({
    "process_report",
    "send_report",
})


def _kind_from_task_type(task_type: str | None) -> str:
    """Map a ``Task.task_type`` to a WorkRecord ``kind`` value.

    Phase 4 (2026-06-27): the resolver splits what was previously a
    single ``kind="task"`` into ``"turn"`` (message turn) and
    ``"report"`` (child completion report) so the frontend can render
    queue badges only on jobs.

    * ``"process_message"`` → ``"turn"``
    * ``"process_report"``, ``"send_report"`` → ``"report"``
    * Unknown / ``None`` → ``"turn"`` (safe default; ``process_message``
      is the dominant TaskType in production today).

    Args:
        task_type: The Task.task_type string value.

    Returns:
        The corresponding WorkRecord ``kind`` value.
    """
    if task_type in REPORT_TASK_TYPES:
        return "report"
    return "turn"


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
    * ``kind`` — ``"turn"`` (user message turn on an instance),
      ``"report"`` (child completion report riding the same delivery
      pipeline), or ``"job"`` (job queue). The resolver normalises
      away which table backs the row. Phase 4 (2026-06-27) split the
      previous ``"task"`` value into ``"turn"`` vs ``"report"`` based
      on ``Task.task_type`` so the frontend can distinguish message
      turns from report-only records (queue badges show only on jobs).
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
    kind: str          # "turn" | "report" | "job"
    status: str        # canonical status (via work_status.canonicalize_status)
    instance_id: str | None
    project_id: str | None
    agent_id: str | None
    result_summary: str | None
    error: str | None
    created_at: datetime | None

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
        * ``kind`` — Phase 4 (2026-06-27): the filter is now
          five-valued:

          * ``"job"`` — query only the JobItem table.
          * ``"turn"`` — query only the Task table, restricted to
            ``task_type IN ("process_message")``.
          * ``"report"`` — query only the Task table, restricted to
            ``task_type IN ("process_report", "send_report")``.
          * ``"task"`` — query only the Task table, no ``task_type``
            filter. Backward-compatible alias for the union of
            ``"turn"`` + ``"report"`` (the previous single-kind
            behaviour). New clients should prefer the split
            vocabulary.
          * ``None`` (default) — query both tables, no
            ``task_type`` filter.

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
            kind: Optional kind filter (``"job"``, ``"turn"``,
                ``"report"``, ``"task"``).
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

        records: list[WorkRecord] = []

        # Translate kind filter into per-table predicates.
        # ``"job"`` excludes the Task side, ``"task"``/``"turn"``/
        # ``"report"`` exclude the JobItem side, ``None`` includes
        # both. ``"task"`` is the backward-compat alias meaning
        # "all task rows" (turn + report).
        query_tasks_table = kind != "job"
        query_jobs_table = kind != "task" and kind != "turn" and kind != "report"

        # Map kind filter to a task_type set for the Task SELECT.
        # ``None`` means no task_type filter (query all task rows).
        task_type_filter: set[str] | None = None
        if kind == "turn":
            task_type_filter = set(TURN_TASK_TYPES)
        elif kind == "report":
            task_type_filter = set(REPORT_TASK_TYPES)
        elif kind == "task":
            task_type_filter = None  # backward-compat: all task rows

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
            tasks = self._query_tasks(instance_id, source_statuses, task_type_filter)
            for task in tasks:
                instance = self._lookup_instance(task.instance_id)
                # Reviewer S2: report tasks are parent-bound (their
                # ``Task.instance_id`` is set to the ROOT instance by
                # ``child_reports.py:649-656``), so this guard only
                # excludes child-instance ``process_message`` turns —
                # the child's private execution rows. Reports stay
                # visible under ``root_only=True``.
                if root_only and instance is not None and instance.parent_id:
                    continue
                record = self._task_to_record(task, instance=instance)
                if project_id is not None and record.project_id != project_id:
                    continue
                records.append(record)

        if query_jobs_table:
            jobs = self._query_jobs(project_id, instance_id, source_statuses)
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
                    self._job_to_record(j) for j in jobs
                    if j.instance_id is None or j.instance_id not in child_instance_ids
                )
            else:
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


__all__ = ["WorkRecord", "WorkResolverService"]