"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, func, select as sql_select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col, update as sqlmodel_update

from .models import ACTIVE_ADMISSION_STATES, AdmissionState, JobItem, JobQueue, QueueType

logger = logging.getLogger(__name__)


# ── Legacy status → AdmissionState read-path translation ───────────────────
# Phase 7b: the ``JobStatus`` enum was removed. This map translates
# legacy status filter values (still accepted by ``list()`` /
# ``list_by_queue()`` for backward compatibility with callers that pass
# the old 7-value vocabulary) into the 4-value ``AdmissionState``
# vocabulary that replaced it. Multiple legacy values collapse onto
# fewer admission states (e.g. completed/failed/cancelled → done).
_LEGACY_TO_ADMISSION: dict[str, str] = {
    "pending": AdmissionState.QUEUED.value,
    "processing": AdmissionState.ACTIVE.value,
    "paused": AdmissionState.ACTIVE.value,
    "completed": AdmissionState.DONE.value,
    "failed": AdmissionState.DONE.value,
    "cancelled": AdmissionState.DONE.value,
    "dead_letter": AdmissionState.DEAD.value,
}


# Phase 5: JobItem mirror columns dropped from the SQLModel in Phase B.
# ``atomic_transition`` strips these from ``**extra_updates`` before
# building the UPDATE ``set_values`` so callers (complete_job,
# fail_job, terminate_job) can keep passing their kwargs without
# raising. NOTE: ``failed_at`` is intentionally NOT a member — it
# was re-added to the model in Phase 5 Batch 2 to preserve the live
# retry marker for JobRetryEngine; its removal is deferred to a
# future batch that migrates the retry engine off it.
_REMOVED_JOB_COLUMNS: frozenset[str] = frozenset({
    "status",
    "started_at",
    "completed_at",
    "result_summary",
    "error_message",
    "cancelled_at",
})


def _statuses_to_admission(statuses: list[str | None]) -> list[str]:
    """Translate legacy status filter values to ``admission_state`` values.

    Multiple legacy values collapse onto fewer AdmissionState values
    (e.g. completed/failed/cancelled → done). The result is de-duplicated.

    Args:
        statuses: List of legacy status strings (may contain None).

    Returns:
        De-duplicated list of ``AdmissionState`` value strings.
    """
    result: list[str] = []
    seen: set[str] = set()
    for s in statuses:
        if s is None:
            continue
        mapped = _LEGACY_TO_ADMISSION.get(s)
        if mapped and mapped not in seen:
            seen.add(mapped)
            result.append(mapped)
    return result


def _is_postgres(session: SQLModelSession) -> bool:
    """Return True when the session's bound engine is PostgreSQL."""
    return (
        session.bind is not None
        and session.bind.dialect.name == "postgresql"
    )


class JobRepository:
    """SQLModel-based Job Queue repository for CRUD operations.
    
    Provides persistence for job queue items with support for
    project-based job serialization.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _get_dialect_insert(self, session: SQLModelSession):
        """Return the dialect-appropriate insert callable for upsert ops.

        Generic ``sqlalchemy.insert()`` lacks ``on_conflict_do_nothing()`` —
        that method is dialect-specific. This helper returns the right
        insert callable so callers can chain ``on_conflict_do_nothing`` /
        ``on_conflict_do_update`` for both SQLite and PostgreSQL.

        Args:
            session: SQLAlchemy/SQLModel Session whose bound engine
                determines the dialect.

        Returns:
            Dialect-specific ``insert`` callable. Both ``sqlite`` and
            ``postgresql`` dialect inserts support ``on_conflict_do_nothing``.
        """
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            return pg_insert
        return sqlite_insert

    def _json_set_text_sql(self, column: str, key: str, param: str) -> str:
        """Return a dialect-aware SQL fragment that sets ``key`` on a
        JSON/JSONB ``column`` to a bound-parameter text value.

        The two supported backends use different syntax for in-place
        JSON mutation:

        * PostgreSQL JSONB: ``column || jsonb_build_object('key', :param)``
          — the ``||`` operator is atomic, treats NULL as empty, and
          overwrites an existing key (matching the intended semantics
          of ``stamp_message_id``).
        * SQLite: ``json_set(COALESCE(column, '{}'), '$.key', :param)`` —
          ``COALESCE`` keeps the call safe when the column is NULL,
          and ``json_set`` overwrites an existing key.

        Mirrors the dialect-detection pattern of
        :meth:`TaskRepository._json_extract_text_sql` — the ``key`` is
        interpolated as a static constant and ``param`` is interpolated
        as a bound-parameter reference. Callers MUST pass a static
        string for ``key`` (this method is not user-input-safe by
        design) and bind the actual value to ``:param`` themselves.

        Args:
            column: Bare column reference (e.g. ``"metadata"``).
            key: Static JSON key to set.
            param: Bound-parameter name (e.g. ``"message_id"``).

        Returns:
            SQL fragment suitable for direct interpolation into a
            ``text()`` statement.
        """
        if self.engine.dialect.name == "postgresql":
            # ``jsonb_build_object`` accepts ``anyelement``, so an
            # untyped bound param raises ``IndeterminateDatatype``; the
            # explicit ``CAST`` anchors it.
            return (
                f"{column} || jsonb_build_object('{key}', "
                f"CAST(:{param} AS text))"
            )
        # SQLite: ``{{}}`` escapes to the literal ``{}`` default JSON
        # object in the f-string output.
        return f"json_set(COALESCE({column}, '{{}}'), '$.{key}', :{param})"

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        agent_id: str,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5,
        job_metadata: dict[str, Any | None] = None,
        queue_id: str | None = None,
        idempotency_key: str | None = None,
        job_type: str = "task",
        instance_id: str | None = None,
    ) -> JobItem:
        """Create a new job queue item.

        Args:
            agent_id: Agent ID (e.g., 'developer').
            agent_dir: Path to the agent directory.
            message: Job message/content.
            source: Source of the job ("api", "telegram", "scheduler", "webhook").
            project_id: Optional project ID for job serialization.
            priority: Job priority (1-10, default 5).
            job_metadata: Optional metadata dictionary.
            queue_id: Optional queue ID for job routing.
            idempotency_key: Optional idempotency key for deduplication.
            job_type: Job type ("task" or "message", default "task").
            instance_id: Optional pre-set instance ID (for MESSAGE jobs).

        Returns:
            Created JobItem object.
        """
        with SQLModelSession(self.engine) as db_session:
            job = JobItem(
                agent_id=agent_id,
                agent_dir=agent_dir,
                message=message,
                source=source,
                project_id=project_id,
                priority=priority,
                # Phase 4 cleanup (admission_state is the sole authority):
                # admission_state is set directly to QUEUED. The legacy
                # ``status`` column was dropped from the JobItem model
                # in Phase 5; no ``status=`` kwarg can be passed.
                admission_state=AdmissionState.QUEUED.value,
                job_metadata=job_metadata or {},
                queue_id=queue_id,
                idempotency_key=idempotency_key,
                job_type=job_type,
                instance_id=instance_id,
            )

            db_session.add(job)
            db_session.commit()
            db_session.refresh(job)

            return job

    def create_or_get_by_idempotency_key(
        self,
        *,
        agent_id: str,
        agent_dir: str,
        message: str,
        source: str = "api",
        project_id: str | None = None,
        priority: int = 5,
        job_metadata: dict[str, Any | None] = None,
        queue_id: str | None = None,
        idempotency_key: str,
        job_type: str = "task",
        instance_id: str | None = None,
    ) -> tuple[JobItem | None, bool]:
        """Atomically insert a job or return the existing one with the same key.

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` (PostgreSQL) / the SQLite
        equivalent, keyed on the partial unique index ``idx_job_idempotency``
        (``WHERE idempotency_key IS NOT NULL``), to atomically claim the
        idempotency key. This closes the TOCTOU race in the previous
        read-then-insert ``enqueue`` pattern (M6): two concurrent enqueues
        with the same key would both pass ``find_by_idempotency_key`` and
        the loser's INSERT would raise an unhandled ``IntegrityError``.

        Args:
            agent_id: Agent ID.
            agent_dir: Path to the agent directory.
            message: Job message.
            source: Job source ("api", "telegram", ...).
            project_id: Optional project ID.
            priority: Job priority (1-10).
            job_metadata: Optional metadata dict.
            queue_id: Optional queue ID for routing.
            idempotency_key: Required idempotency key. Must be non-null.
            job_type: Job type ("task" or "message").
            instance_id: Optional pre-set instance ID (for MESSAGE jobs).

        Returns:
            Tuple ``(job, created)`` where ``job`` is the JobItem that now
            holds the key (either the row we just inserted or the
            pre-existing winner) and ``created`` is ``True`` if a new row
            was inserted by THIS call, ``False`` if an existing row was
            returned (i.e. another writer beat us to it).

        Raises:
            ValueError: If ``idempotency_key`` is None or empty (the
                partial unique index only matches non-null keys, so
                calling this with no key is a programming error).
        """
        if not idempotency_key:
            raise ValueError(
                "create_or_get_by_idempotency_key requires a non-empty "
                "idempotency_key"
            )

        with SQLModelSession(self.engine) as db_session:
            insert_fn = self._get_dialect_insert(db_session)
            now = datetime.now(timezone.utc).isoformat()
            new_job_id = str(uuid.uuid4())

            # Build the values dict. The Core ``Table`` uses the DB
            # column name ``metadata`` for the JSON column, while the
            # SQLModel Python attribute is ``job_metadata``. We must
            # use the DB column name when calling ``Table.insert().values()``
            # (SQLAlchemy would raise ``Unconsumed column names`` otherwise).
            values = {
                "job_id": new_job_id,
                "agent_id": agent_id,
                "agent_dir": agent_dir,
                "message": message,
                "source": source,
                "project_id": project_id,
                "priority": priority,
                # Phase 4 cleanup: admission_state is the sole
                # authority. Set explicitly to QUEUED so the raw
                # INSERT values mirror the ORM create() above.
                # ``status`` was dropped from the JobItem model in
                # Phase 5 and must NOT appear here.
                "admission_state": AdmissionState.QUEUED.value,
                "metadata": job_metadata or {},  # DB column name, not job_metadata
                "queue_id": queue_id,
                "idempotency_key": idempotency_key,
                "job_type": job_type,
                "instance_id": instance_id,
                "created_at": now,
            }

            # ON CONFLICT keyed on the partial unique index
            # ``idx_job_idempotency`` (WHERE idempotency_key IS NOT
            # NULL AND deleted_at IS NULL). We MUST pass
            # ``index_where`` matching the partial index predicate so
            # PostgreSQL can infer the right index for conflict
            # detection (required for partial-index inference). The
            # ``deleted_at IS NULL`` clause mirrors
            # ``find_by_idempotency_key``'s filter and allows the
            # soft-delete → recreate pattern: a caller soft-deletes a
            # job and then submits a fresh enqueue with the same key —
            # the old row is excluded from the index, so the new
            # INSERT wins. We pass ``JobItem.__table__`` (the
            # SQLAlchemy ``Table``) rather than the ORM class so
            # ``insert(...).values(...)`` is a pure Core insert —
            # mixing ORM classes into Core ``insert()`` tries to
            # invoke ORM bulk-persistence paths that fail on SQLModel
            # ``Table`` objects.
            job_table = JobItem.__table__
            stmt = (
                insert_fn(job_table)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["idempotency_key"],
                    index_where=text(
                        "idempotency_key IS NOT NULL AND deleted_at IS NULL"
                    ),
                )
            )

            result = db_session.execute(stmt)
            inserted = result.rowcount == 1
            db_session.commit()

            # Fetch the row holding the key — either the one we just
            # inserted or the pre-existing winner. The partial unique
            # index guarantees at most one row exists with a given key.
            job = db_session.exec(
                select(JobItem)
                .where(JobItem.idempotency_key == idempotency_key)
                .where(JobItem.deleted_at.is_(None))
            ).first()

            if inserted and job is None:
                # Extremely defensive — the INSERT reported rowcount=1
                # but the follow-up SELECT came back empty. Log and
                # surface to caller; the caller will treat this as a
                # duplicate (the row is provably there for the next
                # read).
                logger.warning(
                    "create_or_get_by_idempotency_key: INSERT succeeded "
                    "but follow-up SELECT returned no row for key=%r",
                    idempotency_key,
                )

            return job, inserted

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, job_id: str) -> JobItem | None:
        """Get a job by ID.
        
        Args:
            job_id: Unique job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            return job

    def get_by_instance(self, instance_id: str) -> JobItem | None:
        """Get the most recent non-deleted job for an instance ID.

        Args:
            instance_id: Instance identifier.

        Returns:
            Most recent non-deleted JobItem matching ``instance_id``, or ``None``
            if none exists. ``ORDER BY created_at DESC`` ensures determinism when
            multiple job rows exist for the same instance (e.g. a CANCELLED job
            left from a prior terminate plus a fresh PROCESSING job from a revive).
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
                .order_by(JobItem.created_at.desc(), JobItem.job_id)
            )
            job = db_session.exec(stmt).first()
            return job

    def get_active_by_instance(
        self, instance_id: str, job_id: str | None = None
    ) -> JobItem | None:
        """Get the active (QUEUED or ACTIVE) job for an instance.

        Filters on ``admission_state`` (not ``status``) so callers see jobs in
        any admission-active state regardless of status mirror value. Excludes
        terminal admission states (``COMPLETED``, ``FAILED``, ``CANCELLED``,
        ``DEAD_LETTER``) and soft-deleted rows. Used by callers that need to
        find the current live job — never a historical one.

        F13 (defer-seam bugfix Phase 3): when ``job_id`` is provided, resolve
        by exact ``JobItem.job_id`` rather than the freshest-by-``created_at``
        ordering. This prevents the observer from finalizing the WRONG
        sibling when two ACTIVE JobItems exist for the same instance (a
        legitimate state from manual DB ops, mock writes, or revive races
        where the freshly-created ACTIVE JobItem is the live one and the
        older JobItem is a leftover).

        When ``job_id`` is ``None`` (the legacy path), the freshest-by-
        ``created_at`` ordering is preserved for backward compatibility —
        callers that do not know the exact ID continue to get the most
        recently created ACTIVE JobItem.

        Args:
            instance_id: Instance identifier.
            job_id: Optional exact ``JobItem.job_id`` to resolve. When
                provided, the query filters on this exact ID (in addition
                to the active-state filters). When ``None``, falls back
                to the freshest-by-``created_at`` ordering.

        Returns:
            Active non-deleted JobItem matching ``instance_id`` (and, when
            ``job_id`` is provided, matching the exact ``job_id``). Returns
            ``None`` when no such row exists.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
                .where(JobItem.admission_state.in_(ACTIVE_ADMISSION_STATES))
            )
            if job_id is not None:
                # F13: resolve by exact ID — the queried JobItem is
                # the only candidate, so created_at ordering is
                # irrelevant. This avoids the wrong-sibling bug
                # where a freshest-by-created_at lookup would
                # shadow a freshly-created ACTIVE JobItem with an
                # older sibling.
                stmt = stmt.where(JobItem.job_id == job_id)
                return db_session.exec(stmt).first()
            # Legacy path: freshest-by-created_at ordering preserved.
            stmt = stmt.order_by(JobItem.created_at.desc(), JobItem.job_id)
            return db_session.exec(stmt).first()

    def find_by_idempotency_key(self, idempotency_key: str) -> JobItem | None:
        """Find a job by its idempotency key.
        
        Used for idempotent enqueue: before creating a new job, check if one
        already exists with the same key.
        
        Args:
            idempotency_key: The idempotency key to search for.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.idempotency_key == idempotency_key).where(
                JobItem.deleted_at.is_(None)
            )
            job = db_session.exec(stmt).first()
            return job

    def count_active_jobs_by_project(self, project_id: str) -> int:
        """Count active jobs (QUEUED + ACTIVE admission_state) for a project
        across all queues, excluding soft-deleted jobs.

        Phase 3: queries admission_state instead of status. The defer
        idle-gate uses this count to decide "is there pending work?" so
        both 'queued' and 'active' must be included — a project with
        only 'queued' jobs (no 'active' yet) must still block defer
        queues (C2 fix for FIFO priority).

        Args:
            project_id: Project identifier.

        Returns:
            Count of active jobs for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(func.count())
                .select_from(JobItem)
                .where(JobItem.project_id == project_id)
                .where(JobItem.admission_state.in_(ACTIVE_ADMISSION_STATES))
                .where(JobItem.deleted_at.is_(None))
            )
            return db_session.exec(stmt).one()

    def count_active_jobs_in_non_defer_queues(self, project_id: str) -> int:
        """Count active jobs (QUEUED + ACTIVE admission_state) for a project in
        non-defer queues only.

        Used for defer queue idle check to avoid deadlock when multiple defer queues
        exist. This JOINs with job_queues table to exclude defer queue types.

        Phase 3: queries admission_state instead of status. Must include BOTH
        'queued' AND 'active' (C2 fix) — the defer idle-gate in
        ``job_processor.py`` uses this to decide whether non-defer queues are
        idle; a project with only 'queued' work must still block defer queues
        to preserve FIFO priority.

        Args:
            project_id: Project identifier.

        Returns:
            Count of active jobs in non-defer queues for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            # Import JobQueue model here to avoid circular imports
            from .models import JobQueue

            stmt = (
                select(func.count())
                .select_from(JobItem)
                .join(JobQueue, JobItem.queue_id == JobQueue.queue_id)
                .where(JobItem.project_id == project_id)
                .where(JobItem.admission_state.in_(ACTIVE_ADMISSION_STATES))
                .where(JobItem.deleted_at.is_(None))
                .where(JobQueue.queue_type != QueueType.DEFER.value)
            )
            return db_session.exec(stmt).one()

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        statuses: list[str | None] = None,
        project_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> tuple[list[JobItem], int]:
        """List jobs with optional filters and pagination.
        
        Args:
            statuses: Optional list of status filters.
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.
            include_deleted: If False (default), exclude soft-deleted jobs.
            
        Returns:
            Tuple of (list of jobs, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(JobItem)
            if not include_deleted:
                count_stmt = count_stmt.where(JobItem.deleted_at.is_(None))
            if statuses:
                admission_set = _statuses_to_admission(statuses)
                if admission_set:
                    count_stmt = count_stmt.where(
                        JobItem.admission_state.in_(admission_set)
                    )
            if project_id:
                count_stmt = count_stmt.where(JobItem.project_id == project_id)
            if queue_id:
                count_stmt = count_stmt.where(JobItem.queue_id == queue_id)
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem)
            if not include_deleted:
                stmt = stmt.where(JobItem.deleted_at.is_(None))
            if statuses:
                admission_set = _statuses_to_admission(statuses)
                if admission_set:
                    stmt = stmt.where(JobItem.admission_state.in_(admission_set))
            if project_id:
                stmt = stmt.where(JobItem.project_id == project_id)
            if queue_id:
                stmt = stmt.where(JobItem.queue_id == queue_id)
            
            stmt = stmt.order_by(
                col(JobItem.created_at).desc(),
                col(JobItem.priority).desc()
            ).offset(offset).limit(limit)
            
            jobs = list(db_session.exec(stmt))
            
            return jobs, total

    def list_pending_by_project(self, project_id: str) -> list[JobItem]:
        """List pending jobs for a specific project, ordered by priority.

        Phase 3: queries admission_state='queued' instead of status='pending'.
        Under the new model, 'queued' is the admission bucket that covers
        PENDING-status jobs (the dual-write keeps status in sync).

        Args:
            project_id: Project identifier.

        Returns:
            List of pending JobItem objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.project_id == project_id)
                .where(JobItem.admission_state == AdmissionState.QUEUED.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_all_pending(self) -> list[JobItem]:
        """List all pending jobs (for jobs without project_id).

        Phase 3: queries admission_state='queued' instead of status='pending'.

        Returns:
            List of all pending JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.admission_state == AdmissionState.QUEUED.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), col(JobItem.created_at).asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def find_processing_jobs(self) -> list[JobItem]:
        """Find all jobs currently in ACTIVE admission_state.

        Used for startup recovery to identify orphaned jobs.

        Phase 3: queries admission_state='active' instead of status='processing'.
        Under the new model, 'active' includes both PROCESSING-status jobs AND
        PAUSED-status jobs (a paused job keeps its lock and stays 'active' in
        admission — pause is an Instance concern). See ``JobRecoveryService``.
        The PAUSED-instance branch in recover_on_startup distinguishes
        paused-vs-orphaned via Instance.status, NOT via this query's result.

        Returns:
            List of all processing JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.admission_state == AdmissionState.ACTIVE.value).where(
                JobItem.deleted_at.is_(None)
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def find_jobs_by_instance(
        self, instance_id: str, job_type: str | None = None
    ) -> list[JobItem]:
        """Find all active jobs for a given instance.

        Used for termination cleanup: cancel ALL MESSAGE jobs for an instance.
        Uses JobItem.instance_id column (indexed) — no JSON filtering needed.

        Phase 3: queries admission_state IN ('queued', 'active') instead of
        status IN ('pending', 'processing', 'failed', 'paused'). Under the
        new model, any queued-or-active job for an instance should be found;
        terminal ('done'/'dead') jobs are excluded. FAILED-status jobs that
        are awaiting retry are admission_state='queued' (set by atomic_retry
        in Phase 2) and remain included via that path.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
                .where(JobItem.admission_state.in_(ACTIVE_ADMISSION_STATES))
            )
            stmt = stmt.order_by(JobItem.created_at.asc())
            if job_type:
                stmt = stmt.where(JobItem.job_type == job_type)
            return list(db_session.exec(stmt))

    def list_pending_by_queue(self, queue_id: str) -> list[JobItem]:
        """List pending jobs for a specific queue, ordered by priority.

        Phase 3: queries admission_state='queued' instead of status='pending'.

        Args:
            queue_id: Queue identifier.

        Returns:
            List of pending JobItem objects for the queue.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.queue_id == queue_id)
                .where(JobItem.admission_state == AdmissionState.QUEUED.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_by_queue(
        self,
        queue_id: str,
        statuses: list[str | None] = None,
        admission_states: list[str | None] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobItem], int]:
        """List jobs for a specific queue with optional filters and pagination.

        Args:
            queue_id: Queue identifier.
            statuses: Optional list of legacy status value filters
                (e.g. ``"processing"``, ``"pending"``). Mutually inclusive
                with ``admission_states`` — both filters are applied with
                ``AND`` semantics when supplied.
            admission_states: Optional list of ``AdmissionState`` value
                filters. Phase 3 admission-decision migration: prefer this
                over ``statuses`` for any queue-admission query. PAUSED
                jobs are ``admission_state='active'`` (lock held) and are
                only matched via this filter, not via
                ``statuses=['processing']``.
            limit: Maximum number of jobs to return.
            offset: Number of jobs to skip.

        Returns:
            Tuple of (list of jobs, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Build count query
            count_stmt = select(func.count()).select_from(JobItem)
            count_stmt = count_stmt.where(JobItem.queue_id == queue_id)
            count_stmt = count_stmt.where(JobItem.deleted_at.is_(None))
            if statuses:
                # Phase 4 cleanup: translate legacy status filter to
                # admission_state (status column is frozen at INSERT default).
                admission_set = _statuses_to_admission(statuses)
                if admission_set:
                    count_stmt = count_stmt.where(
                        JobItem.admission_state.in_(admission_set)
                    )
            if admission_states:
                count_stmt = count_stmt.where(JobItem.admission_state.in_(admission_states))
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem).where(JobItem.queue_id == queue_id)
            stmt = stmt.where(JobItem.deleted_at.is_(None))
            if statuses:
                admission_set = _statuses_to_admission(statuses)
                if admission_set:
                    stmt = stmt.where(JobItem.admission_state.in_(admission_set))
            if admission_states:
                stmt = stmt.where(JobItem.admission_state.in_(admission_states))

            stmt = stmt.order_by(
                col(JobItem.priority).desc(),
                col(JobItem.created_at).desc()
            ).offset(offset).limit(limit)

            jobs = list(db_session.exec(stmt))

            return jobs, total

    # --------------------------------------------------------
    # STATE TRANSITIONS
    # --------------------------------------------------------

    def atomic_transition(
        self,
        job_id: str,
        from_status: str | None,
        to_status: str,
        **extra_updates: Any,
    ) -> JobItem | None:
        """
        Atomically transition a job's status in a single guarded UPDATE.

        Replaces the prior SELECT → Python status check → ORM setattr →
        commit pattern (which was racy under PostgreSQL READ COMMITTED
        isolation — two concurrent callers could both pass the Python
        check and clobber each other's terminal-status writes).

        The new implementation issues a single
        ``UPDATE job_queue_items SET admission_state = :to_admission, ...extra
         WHERE job_id = :job_id AND admission_state = :from_admission``
        and inspects ``rowcount``. On PostgreSQL, the EvalPlanQual
        recheck guarantees the status predicate is re-evaluated after the
        row lock is acquired, so a concurrent writer that flipped the
        status between our check and our write cannot slip past us. On
        SQLite, the single-statement UPDATE is inherently atomic at the
        database level.

        Args:
            job_id: The job to transition.
            from_status: Current expected status. Acts as the SQL-level
                guard; if the row's status does not match this value the
                update is a no-op and ``InvalidTransitionError`` is
                raised (after disambiguating "row not found" from
                "status mismatch" via a follow-up SELECT).
                ``None`` is permitted by the signature for symmetry with
                the state machine's "create" transition; in practice no
                caller passes ``None`` and the SQL guard becomes
                ``status IS NULL`` (always false for persisted rows).
            to_status: Target status.
            **extra_updates: Additional fields to set in the same
                UPDATE statement. Supported keys observed across
                callers: ``started_at``, ``completed_at``,
                ``cancelled_at``, ``instance_id``, ``result_summary``,
                ``error_message``. All map directly to ``JobItem``
                columns.

        Returns:
            The updated ``JobItem`` after the UPDATE commits, or
            ``None`` if the job does not exist.

        Raises:
            InvalidTransitionError: If the transition is not allowed by
                the state machine, or if the row exists but its current
                status does not match ``from_status`` (a concurrent
                writer changed it first).
        """
        # Lazy import to avoid circular dependency with services package
        from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError

        # Phase 7b: the ``JobStatus`` enum was removed. ``atomic_transition``
        # accepts either legacy status strings or AdmissionState strings on
        # ``from_status`` / ``to_status``; the map translates legacy values
        # to admission values for the state-machine pre-check and the SQL
        # guard. The mapping is lossy (multiple legacy values collapse onto
        # one AdmissionState), so the Python pre-check is permissive — the
        # SQL guard (``WHERE admission_state = :from_admission``) remains
        # the race-safety boundary.
        from_admission = (
            None if from_status is None
            else _LEGACY_TO_ADMISSION.get(from_status, from_status)
        )
        to_admission = _LEGACY_TO_ADMISSION.get(to_status, to_status)

        # Validate transition is allowed on the admission vocabulary —
        # cheap fail-fast before opening a session / issuing the UPDATE.
        # Preserves the original method's ordering of side-effects.
        job_state_machine.validate_transition(
            from_admission, to_admission, job_id=job_id
        )

        # Phase 5: strip JobItem mirror columns that were dropped from
        # the model in Phase B. Callers (complete_job, fail_job,
        # terminate_job) still pass these kwargs for backward
        # compatibility — silently drop them so the UPDATE only
        # touches columns that exist in the schema. ``failed_at`` is
        # re-added to the model and is NOT in ``_REMOVED_JOB_COLUMNS``,
        # so it flows through unchanged.
        filtered_updates = {
            k: v for k, v in extra_updates.items()
            if k not in _REMOVED_JOB_COLUMNS
        }
        set_values: dict[str, Any] = {
            "admission_state": to_admission,
            **filtered_updates,
        }

        with SQLModelSession(self.engine) as session:
            # Atomic UPDATE with admission_state guard. PostgreSQL
            # EvalPlanQual re-evaluates the predicate after the row
            # lock is acquired; SQLite's single-statement UPDATE is
            # atomic at the database level. Either way, two
            # concurrent writers cannot both observe the predicate
            # as true.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
            )
            if from_admission is not None:
                stmt = stmt.where(JobItem.admission_state == from_admission)
            stmt = stmt.values(**set_values)
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # UPDATE matched no rows. Two possibilities:
                #   (a) the job_id doesn't exist at all, or
                #   (b) the job exists but its status no longer matches
                #       ``from_status`` (concurrent transition).
                # Disambiguate with a follow-up SELECT — same session,
                # so we see the post-UPDATE state.
                existing = session.get(JobItem, job_id)
                if existing is None:
                    return None
                raise InvalidTransitionError(
                    job_id=job_id,
                    from_state=existing.admission_state,
                    to_state=to_status,
                )

            # Re-read the row to return a fully-populated JobItem instance
            # (mirrors the gold-template `transition_status_if` approach).
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row was deleted between the
                # UPDATE and the SELECT. Preserve the "return None for
                # missing job" contract rather than raising.
                return None

            logger.info(
                "Job transition: %s | %s -> %s | admission: %s -> %s | extra_fields=%s",
                job_id, from_status, to_status, from_admission, to_admission,
                list(extra_updates.keys()),
            )

            return job

    def atomic_retry(
        self,
        job_id: str,
        max_retries: int,
        next_retry_at: str,
        from_admission_state: str = AdmissionState.DONE.value,
    ) -> JobItem | None:
        """Atomically retry a failed job.

        Single guarded UPDATE::

            UPDATE job_queue_items
SET admission_state = 'queued',
                    retry_count = retry_count + 1,   -- atomic SQL increment
                    next_retry_at = :next_retry_at,
                    failed_at = NULL
                WHERE job_id = :job_id
              AND admission_state = :from_admission_state
              AND retry_count < :max_retries
            RETURNING *

        Phase 4 (Job as Queue Proxy): the SQL guard moved from
        ``status = 'failed'`` to ``admission_state = :from_admission_state``
        (default ``'done'`` — the dual-write mirror for
        ``status='failed'``). The plan's §3.2 retry-without-instance
        guarantee removes the intermediate FAILED state in NEW code
        paths — the canonical ``_finalize_terminal(Decision.RETRY)``
        transitions ``active → queued`` directly via the dual-write
        co-move and never visits ``status='failed'`` as an
        intermediate. The legacy ``fail_job`` helper still produces
        rows with ``status='failed'`` + ``admission_state='done'``
        (Phase 2 dual-write mapping) — those are the rows this
        method's default ``from_admission_state='done'`` matches.
        Phase 4 callers that operate on a freshly-finalized active
        job pass ``from_admission_state='active'`` explicitly so
        the SQL guard matches the canonical source state.

        The SQL-level ``admission_state = :from_admission_state AND
        retry_count < :max_retries`` guard is the race-safety boundary.
        PostgreSQL EvalPlanQual re-evaluates the predicate after
        acquiring the row lock (so a concurrent writer that flipped the
        status between the caller's read and this UPDATE cannot slip
        past us); SQLite's single-statement UPDATE is atomic at the
        database level. The ``retry_count = retry_count + 1`` expression
        lets the database compute the increment atomically — no
        read-modify-write race where two concurrent callers could
        both observe ``retry_count = N`` and both write ``N + 1``.

        Args:
            job_id: The job to retry.
            max_retries: Effective retry cap (resolved by the caller
                via the fallback chain ``job.max_retries`` →
                ``queue.default_max_retries`` →
                ``config.default_max_retries``). Used as the SQL
                guard: if the row's ``retry_count`` has already
                reached this value the UPDATE is a no-op.
            next_retry_at: ISO timestamp for the next retry attempt
                (already backoff-computed by the caller).
            from_admission_state: Admission-state guard (default
                ``'done'`` for legacy ``fail_job`` callers; Phase 4
                callers operating on a canonical active job pass
                ``'active'``). The job is transitioned to
                ``'queued'`` only if this guard matches.

        Returns:
            The updated ``JobItem`` after the UPDATE commits, or
            ``None`` if no row matched — i.e. the job does not
            exist, its admission_state is no longer ``from_admission_state``
            (concurrent ``done`` / ``dead`` transition), or its
            ``retry_count`` has already reached ``max_retries``.
            Callers treat ``None`` uniformly as "skip retry".
        """
        with SQLModelSession(self.engine) as session:
            # Atomic guarded UPDATE. ``retry_count = retry_count + 1``
            # is a SQL expression (not a Python read-then-add), so the
            # increment happens server-side and is not subject to a
            # read-modify-write race. The SQL guard is what makes
            # concurrent retries safe: after the first writer
            # commits, the row's status is ``'pending'`` and the
            # second writer's UPDATE matches zero rows.
            #
            # Phase 4 cleanup: the guard now lives on
            # ``admission_state`` (the queue-proxy authority per
            # Plan §3.1). The ``failed_at IS NOT NULL`` predicate
            # restores the pre-Phase-4 ``status='failed'`` semantics:
            # ``admission_state='done'`` covers completed, cancelled,
            # AND failed jobs, but only failed jobs carry the
            # ``failed_at`` timestamp and are retryable.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.admission_state == from_admission_state)
                .where(JobItem.failed_at.isnot(None))
                .where(JobItem.retry_count < max_retries)
                .values(
                    # Phase 4 cleanup: ``status`` is no longer
                    # written (admission_state is the sole
                    # authority). admission_state moves to QUEUED
                    # directly.
                    admission_state=AdmissionState.QUEUED.value,
                    # Phase 7c: clear ``terminal_reason`` so a
                    # retried-failed job doesn't leak the previous
                    # ``'failed'`` discriminator into its ``queued``
                    # lifetime (which would surface via ``JobResponse``
                    # until the next failure resets it).
                    terminal_reason=None,
                    retry_count=JobItem.retry_count + 1,
                    next_retry_at=next_retry_at,
                    failed_at=None,
                )
            )
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # UPDATE matched no rows. Three possibilities, all
                # indistinguishable from the UPDATE alone — and all
                # collapse to "no-op, do not retry":
                #   (a) ``job_id`` doesn't exist at all,
                #   (b) the job exists but its status is no longer
                #       ``'failed'`` (concurrent ``CANCELLED`` /
                #       ``DEAD_LETTER`` transition),
                #   (c) the job exists but ``retry_count >= max_retries``
                #       (already at the cap).
                # The caller treats ``None`` uniformly as "skip retry",
                # so no further disambiguation is required here.
                logger.debug(
                    "atomic_retry no-op for %s (missing, concurrent "
                    "transition, or retry_count at cap)",
                    job_id,
                )
                return None

            # Re-read the row to return a fully-populated ``JobItem``
            # (mirrors ``atomic_transition`` / ``start_job``).
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row was deleted between
                # the UPDATE and the SELECT. Preserve the "return None
                # for missing job" contract rather than raising.
                return None

            logger.info(
                "Job retry scheduled: %s | retry_count=%s | next_retry_at=%s",
                job_id,
                job.retry_count,
                next_retry_at,
            )

            return job

    def finalize_active_to_done(
        self,
        job_id: str,
        derived_status: str,
        result_summary: str | None = None,
        error_message: str | None = None,
        terminal_reason: str | None = None,
    ) -> JobItem | None:
        """Phase 4 cleanup (Job as Queue Proxy): transition ``active → done``.

        This is the low-level building block behind the single
        terminal-write boundary ``JobQueueService._finalize_terminal``
        (Plan §3.2 / §6.1). Phase 4 cleanup makes ``admission_state``
        the sole write authority; the legacy ``status`` column is no
        longer written (Phase 5 drops the column outright).

        Single guarded UPDATE::

            UPDATE job_queue_items
            SET admission_state = 'done',
                terminal_reason  = :terminal_reason
            WHERE job_id = :job_id
              AND admission_state = 'active'
            RETURNING *

        The ``admission_state = 'active'`` predicate is the race-safety
        guard: a concurrent writer that flipped the job out of ACTIVE
        (e.g. concurrent CANCELLED via ``_terminate_instance_db_sync``)
        sees rowcount=0 and we no-op. Mirrors the gold-template
        ``atomic_transition`` pattern, but the canonical column is now
        ``admission_state`` and ``status`` is no longer co-moved.

        Args:
            job_id: The job to finalize.
            derived_status: Caller's terminal-spelling indicator
                (COMPLETED → 'completed', ERROR/FAILED → 'failed',
                TERMINATED → 'cancelled'). Retained for logging /
                observability but no longer written to the DB.
            result_summary: Filled for COMPLETED; ``None`` is acceptable
                for ERROR/TERMINATED (the caller can pass ``None`` to
                keep the prior value, or pass an explicit string to
                overwrite).
            error_message: Filled for ERROR/TERMINATED; same rules as
                ``result_summary``.
            terminal_reason: Phase 7c — records HOW the job terminated
                (``"completed"`` / ``"failed"`` / ``"cancelled"`` /
                ``"aborted"``). ``None`` means "no opinion" — the
                column keeps its prior value. The caller
                (``_finalize_terminal``) is responsible for deriving
                the right value; this method does NOT infer one from
                ``derived_status`` so a misuse can't write
                ``"cancelled"`` for a natural completion.

        Returns:
            The updated ``JobItem`` after the UPDATE commits, or
            ``None`` if no row matched — i.e. the job does not exist
            or its ``admission_state`` is no longer ``'active'``
            (concurrent terminal transition).
        """
        now = datetime.now(timezone.utc).isoformat()
        # Build SET clause dynamically — only ``admission_state`` and
        # (Phase 7c) ``terminal_reason`` are written here. The
        # remaining terminal-side fields (``completed_at``,
        # ``cancelled_at``, ``result_summary``, ``error_message``)
        # live on the Instance; the JobItem mirror columns were
        # dropped in Phase 5 so they cannot be targeted by this
        # UPDATE.
        #
        # Phase 4 cleanup: ``status`` is no longer written here
        # (admission_state is the sole authority). The
        # ``derived_status`` parameter is retained for the caller's
        # logging / observability (it tells us WHICH terminal path
        # fired — COMPLETED, FAILED, CANCELLED) but no longer
        # participates in the SQL write.
        set_values: dict[str, Any] = {
            "admission_state": AdmissionState.DONE.value,
        }
        # Phase 7c: only set terminal_reason when the caller provided
        # an explicit value. ``None`` means "don't touch the column"
        # (backward-compat for callers that haven't been migrated yet
        # — pre-7c rows keep their NULL).
        if terminal_reason is not None:
            set_values["terminal_reason"] = terminal_reason

        with SQLModelSession(self.engine) as session:
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                # Phase 4: admission_state is the authoritative guard.
                .where(JobItem.admission_state == AdmissionState.ACTIVE.value)
                .values(**set_values)
            )
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # Two possibilities: job doesn't exist, or it already
                # left ACTIVE (concurrent terminal transition).
                # Disambiguate via follow-up SELECT.
                existing = session.get(JobItem, job_id)
                if existing is None:
                    logger.debug(
                        "finalize_active_to_done: job %s not found", job_id
                    )
                    return None
                logger.debug(
                    "finalize_active_to_done: job %s no-op "
                    "(admission_state=%s, expected 'active')",
                    job_id,
                    existing.admission_state,
                )
                return None

            # Re-read to return a fully-populated JobItem.
            job = session.get(JobItem, job_id)
            if job is None:
                return None
            logger.info(
                "Job finalized (active → done): %s | status=%s | admission_state=done",
                job_id,
                derived_status,
            )
            return job

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, job_id: str, **updates) -> JobItem | None:
        """Update a job's fields.

        Defense-in-depth guard: callers must NOT pass ``status=`` here.
        Status changes are routed through :meth:`atomic_transition`
        (and its convenience wrappers ``start_job`` /
        ``start_job_atomic`` / ``complete_job`` / ``fail_job`` /
        ``cancel_job`` / ``terminate_job``) so the SQL-level
        ``WHERE status = :from_status`` guard prevents concurrent
        clobbering of terminal statuses. Bypassing that path by
        writing ``status`` directly here would reintroduce the very
        race the atomic-transition fix was designed to eliminate.

        Any field other than ``status`` is updated as a plain ORM
        setattr — these updates are not part of the state-machine
        contract (e.g. ``priority``, ``message``, ``job_metadata``)
        and are safe to write directly. The ``JobItem.version``
        column provides additional cross-process optimistic locking
        for those ORM-flushed writes via SQLAlchemy's
        ``version_id_col`` machinery.

        Args:
            job_id: Job identifier.
            **updates: Fields to update. ``status`` is rejected — use
                :meth:`atomic_transition` (or one of its wrappers)
                instead.

        Returns:
            Updated JobItem if found, None otherwise.

        Raises:
            ValueError: If ``status`` is supplied via ``updates`` (use
                :meth:`atomic_transition` for status changes).
        """
        if "status" in updates:
            raise ValueError(
                "Use atomic_transition for status changes "
                "(see JobRepository.atomic_transition / "
                "start_job_atomic / complete_job / fail_job / "
                "cancel_job / terminate_job)"
            )
        # Phase 4 cleanup: ``admission_state`` is the sole authority.
        # Direct ORM writes to ``admission_state`` would bypass the
        # central transition path (``atomic_transition`` and its
        # wrappers — ``start_job``, ``start_job_atomic_with_lock``,
        # ``finalize_active_to_done``, ``atomic_retry``, ``cancel_job``)
        # and create a row that violates the queued/active/done/dead
        # invariant. Reject the same way ``status`` is rejected.
        if "admission_state" in updates:
            raise ValueError(
                "Use atomic_transition (or its wrappers — "
                "start_job / start_job_atomic_with_lock / "
                "finalize_active_to_done / atomic_retry / cancel_job) "
                "for admission_state changes"
            )

        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return None

            for key, value in updates.items():
                if hasattr(job, key):
                    setattr(job, key, value)

            db_session.commit()
            db_session.refresh(job)

            return job

    def stamp_message_id(self, job_id: str, message_id: str) -> None:
        """Stamp ``message_id`` onto ``job_queue_items.metadata`` after enqueue.

        Phase 1 of the defer-seam bugfix: ``claim_pending_task`` correlates
        MESSAGE JobItems to their original ``message_id`` via the JSON
        extraction at ``job_queue_items.metadata->>'message_id'`` (PG) /
        ``json_extract(metadata, '$.message_id')`` (SQLite). The extraction
        always returned NULL because the admission path never wrote the
        value, so the cross-system guard could not match active MESSAGE
        JobItems to the corresponding ``message_queue`` row → self-deadlock.

        This method performs a dialect-aware single-statement UPDATE so
        concurrent writers targeting different keys compose correctly
        (mirrors the gold-template pattern in
        ``InstanceRepository.set_metadata``):

        * PostgreSQL: ``metadata || jsonb_build_object('message_id', :message_id)``
        * SQLite:     ``json_set(COALESCE(metadata, '{}'), '$.message_id', :message_id)``

        No-op when the row does not exist (``rowcount = 0`` is not an
        error; the caller will handle the missing row on the next read).

        Args:
            job_id: Job identifier whose metadata to stamp.
            message_id: Original message_id to correlate against.
        """
        with SQLModelSession(self.engine) as db_session:
            sql_fragment = self._json_set_text_sql(
                "metadata", "message_id", "message_id"
            )
            stmt = text(
                f"UPDATE job_queue_items SET metadata = {sql_fragment} "
                f"WHERE job_id = :job_id"
            )
            db_session.execute(
                stmt,
                {"message_id": message_id, "job_id": job_id},
            )
            db_session.commit()

    def start_job(
        self,
        job_id: str,
        instance_id: str,
    ) -> JobItem | None:
        """Mark a job as processing (started) — atomically.

        Can only be called on PENDING jobs. Uses a single guarded
        ``UPDATE … WHERE job_id = :job_id AND status = 'pending'`` so
        two concurrent callers cannot both succeed: the SQL-level
        status predicate is re-evaluated after the row lock is acquired
        (PostgreSQL EvalPlanQual) or the entire UPDATE is atomic at
        the database level (SQLite), eliminating the TOCTOU race the
        prior ``get()`` + Python check + ORM ``update()`` pattern had
        under PostgreSQL READ COMMITTED.

        Contract (preserved from the pre-fix implementation):
          * Job does not exist → returns ``None``.
          * Job exists but is not PENDING → raises ``ValueError`` with
            message ``"Cannot start job in '<status>' state, must be
            PENDING"``. Two production callers in
            ``job_queue_service.trigger_next_job_sync`` and several
            tests depend on catching this specific exception class
            and message — the fix must not change them.

        Args:
            job_id: Job identifier.
            instance_id: Instance ID that is processing this job.

        Returns:
            Updated ``JobItem`` after the UPDATE commits, or ``None``
            if the job does not exist.

        Raises:
            ValueError: If the job exists but its current status is not
                ``pending`` (a concurrent writer changed it first, or it
                was already started/completed/failed/cancelled).
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        set_values: dict[str, Any] = {
            # Phase 5 cleanup: ``started_at`` was dropped from the
            # JobItem model in Phase B (execution timing now lives on
            # ``Instance``). Only ``admission_state`` and ``instance_id``
            # are written here.
            "admission_state": AdmissionState.ACTIVE.value,
            "instance_id": instance_id,
        }

        with SQLModelSession(self.engine) as session:
            # Single guarded UPDATE: only matches rows that are still
            # QUEUED. PostgreSQL EvalPlanQual re-evaluates the
            # admission_state predicate after the row lock is
            # acquired; SQLite's single-statement UPDATE is atomic at
            # the database level. Either way, two concurrent writers
            # cannot both observe the predicate as true.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.admission_state == AdmissionState.QUEUED.value)
                .values(**set_values)
            )
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # UPDATE matched no rows. Two possibilities:
                #   (a) the job_id doesn't exist at all, or
                #   (b) the job exists but its admission_state is no
                #       longer QUEUED (concurrent transition).
                #       Disambiguate with a follow-up SELECT — same
                #       session, so we see the post-UPDATE state.
                existing = session.get(JobItem, job_id)
                if existing is None:
                    return None
                raise ValueError(
                    f"Cannot start job in admission_state "
                    f"'{existing.admission_state}', must be 'queued'"
                )

            # Re-read the row to return a fully-populated JobItem
            # instance (mirrors the gold-template `transition_status_if`
            # and the in-file `atomic_transition` approach).
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row was deleted between
                # the UPDATE and the SELECT. Preserve the "return None
                # for missing job" contract rather than raising.
                return None

            return job

    def start_job_atomic(
        self,
        job_id: str,
        instance_id: str,
    ) -> JobItem | None:
        """Start a job atomically (PENDING -> PROCESSING).

        Note: No deleted_at IS NULL check needed here — defense is at the query level above
        (list_pending_by_project, list_all_pending, list_pending_by_queue all exclude deleted jobs)
        """
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status="pending",
            to_status="processing",
            started_at=now,
            instance_id=instance_id,
        )

    def start_job_atomic_with_lock(
        self,
        job_id: str,
        instance_id: str,
        project_id: str,
        queue_id: str,
        concurrency_limit: int,
    ) -> tuple[JobItem | None, bool]:
        """Atomically acquire a queue lock AND transition PENDING -> PROCESSING
        in a SINGLE transaction.

        B1 Fix (Phase 2 of "Job as Queue Proxy"): the PostgreSQL constraint
        trigger ``trg_job_locks_active_guard`` (installed in
        ``daemon/manager.py::_ensure_postgres_columns``) fires at COMMIT
        of every transaction that INSERTs into ``job_locks``. It requires
        the matching ``job_queue_items.admission_state = 'active' AND
        deleted_at IS NULL`` row to be visible at COMMIT time.

        The pre-fix flow ran two SEPARATE transactions:

            TX-A: ``LockRepository.try_acquire_slot`` — INSERT job_locks
                  → COMMIT (trigger fires here, admission_state='queued' →
                  raises ``integrity_constraint_violation``)
            TX-B: ``JobRepository.start_job_atomic`` — UPDATE admission_state='active'
                  → COMMIT

        Every job start in production hit the trigger at the TX-A commit
        and aborted. This method collapses both writes into one
        ``engine.begin()`` block so the trigger sees both the lock row
        AND the active admission_state at COMMIT.

        Returns:
            Tuple ``(job, lock_acquired)``:

            - ``(JobItem, True)`` — lock acquired AND status transitioned
              PENDING -> PROCESSING. Both writes committed atomically.
            - ``(None, False)`` — no slot was available. Transaction
              rolled back; no rows persisted (no lock, no status change).
            - Raises ``ValueError`` — lock acquired but the status UPDATE
              matched 0 rows (status was no longer PENDING: a concurrent
              ``start_job`` / ``cancel_job`` / etc. changed it first).
              Transaction rolled back so the lock is auto-released — the
              caller does NOT need to release the lock manually.
              Matches the contract of :meth:`start_job` /
              :meth:`start_job_atomic` for "not in PENDING" so callers
              (``_try_start_job``, ``start_job``, ``trigger_next_job_sync``)
              keep their ``except ValueError`` handlers.

        Args:
            job_id: Job identifier.
            instance_id: Pre-generated UUID for the new instance. MUST be
                a fresh value; reused on retry within the same transaction
                is fine because the rollback also discards the lock row.
            project_id: Project owning the queue.
            queue_id: Queue identifier. For the project-based legacy
                path this is the synthesized ``"project:{project_id}"``.
            concurrency_limit: Maximum concurrent jobs allowed on this
                queue (``queue.concurrency_limit``). For the project-based
                legacy path this is ``1``.

        Raises:
            ValueError: If the job exists but its status is not PENDING
                when the UPDATE fires (concurrent transition). The
                transaction — including the lock INSERT — is rolled back
                atomically; no caller cleanup is required.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        dialect = self.engine.dialect.name

        with self.engine.begin() as conn:
            # 1. Atomically claim a slot. Same dialect-branching pattern
            # as ``LockRepository.try_acquire_slot`` — raw ``text()`` so
            # we stay inside the engine.begin() transaction.
            if dialect == "postgresql":
                lock_insert_stmt = text(
                    """
                    INSERT INTO job_locks
                        (lock_id, project_id, queue_id, job_id,
                         instance_id, lock_slot, acquired_at)
                    VALUES
                        (:lock_id, :project_id, :queue_id, :job_id,
                         :instance_id, :slot, :now)
                    ON CONFLICT (project_id, queue_id, lock_slot) DO NOTHING
                    """
                )
            else:
                lock_insert_stmt = text(
                    """
                    INSERT OR IGNORE INTO job_locks
                        (lock_id, project_id, queue_id, job_id,
                         instance_id, lock_slot, acquired_at)
                    VALUES
                        (:lock_id, :project_id, :queue_id, :job_id,
                         :instance_id, :slot, :now)
                    """
                )

            lock_acquired = False
            for slot in range(concurrency_limit):
                lock_id = str(uuid.uuid4())
                result = conn.execute(
                    lock_insert_stmt,
                    {
                        "lock_id": lock_id,
                        "project_id": project_id,
                        "queue_id": queue_id,
                        "job_id": job_id,
                        "instance_id": instance_id,
                        "slot": slot,
                        "now": now_iso,
                    },
                )
                if (result.rowcount or 0) == 1:
                    lock_acquired = True
                    break

            if not lock_acquired:
                # All slots taken. ``engine.begin()`` commits the empty
                # transaction on normal exit (no-op) — caller sees
                # ``(None, False)``.
                return None, False

            # 2. UPDATE job_queue_items in the SAME transaction. The
            # PostgreSQL ``trg_job_locks_active_guard`` trigger fires at
            # COMMIT; because both the lock INSERT and this UPDATE are
            # staged in one transaction, the trigger sees both at COMMIT
            # and accepts the new active state.
            #
            # Raw ``text()`` SQL (not ``sqlmodel_update``) so we share
            # the same transaction handle and can read ``rowcount``
            # directly. Phase 5 cleanup: ``status`` and ``started_at``
            # were dropped from the JobItem model in Phase B —
            # ``admission_state`` is the sole authority (queue-side
            # vocabulary), and execution timing now lives on
            # ``Instance``. The guard moved from the removed ``status``
            # column to ``admission_state`` itself: a job can only
            # start if it's currently ``queued``.
            update_stmt = text(
                """
                UPDATE job_queue_items
                SET admission_state = :admission_state,
                    instance_id = :instance_id
                WHERE job_id = :job_id
                  AND admission_state = :admission_state_guard
                  AND deleted_at IS NULL
                """
            )
            update_result = conn.execute(
                update_stmt,
                {
                    "admission_state": AdmissionState.ACTIVE.value,
                    "instance_id": instance_id,
                    "job_id": job_id,
                    "admission_state_guard": AdmissionState.QUEUED.value,
                },
            )

            if (update_result.rowcount or 0) == 0:
                # admission_state guard matched 0 rows: either the
                # job doesn't exist, or its admission_state is no
                # longer QUEUED (concurrent start_job / cancel_job /
                # etc.). Disambiguate with a follow-up SELECT in the
                # same transaction so we can raise the right
                # exception. Whatever we decide, raising inside
                # ``engine.begin()`` triggers ROLLBACK of BOTH the
                # lock INSERT and the failed UPDATE — the caller never
                # sees a partially-committed lock.
                existing = conn.execute(
                    text("SELECT admission_state FROM job_queue_items WHERE job_id = :job_id"),
                    {"job_id": job_id},
                ).first()
                current_admission = existing[0] if existing is not None else None
                if current_admission is None:
                    # Job doesn't exist — treat as "lock not acquired"
                    # so callers' ``None-returning`` branches trigger.
                    # The transaction rolls back via the raise below;
                    # the lock INSERT is undone automatically.
                    raise ValueError(
                        f"Cannot start job '{job_id}': job not found"
                    )
                raise ValueError(
                    f"Cannot start job in '{current_admission}' admission_state, "
                    f"must be 'queued'"
                )

        # Transaction committed successfully — re-read the row to
        # return a fully-populated ``JobItem`` (mirrors ``start_job`` /
        # ``atomic_transition``).
        with SQLModelSession(self.engine) as session:
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row was deleted between the
                # COMMIT and the SELECT. Preserve the "return None for
                # missing job" contract rather than raising.
                return None, True
            return job, True

    def rearm_with_lock(
        self,
        job_id: str,
        instance_id: str,
    ) -> tuple["JobItem | None", bool]:
        """Atomically re-acquire a queue lock AND transition DONE -> ACTIVE
        in a SINGLE transaction (F9 fix).

        Mirrors the B1 fix pattern (:meth:`start_job_atomic_with_lock`) but for
        the orphan-race post-commit re-arm path. The pre-fix flow in
        :meth:`daemon.services.job_feedback_observer.JobFeedbackObserver._finalize_job`
        ran in two SEPARATE transactions — the lock DELETE + admission_state
        UPDATE landed in TX-A (committed cleanly because admission_state
        was already 'done' before the DELETE), but the subsequent
        ``atomic_transition(done -> active)`` in TX-B fired the
        ``trg_job_queue_items_active_lock_guard`` constraint trigger with
        no matching ``job_locks`` row, raising
        ``integrity_constraint_violation`` on PostgreSQL.

        The fix collapses both writes (lock INSERT + admission_state UPDATE)
        into one ``engine.begin()`` block so the PG triggers see both
        rows visible at COMMIT and accept the re-arm. ``concurrency_limit``
        and ``(project_id, queue_id)`` are looked up from the JobItem and
        JobQueue tables inside the transaction so callers (the observer)
        only need to pass the job identity.

        Args:
            job_id: Job identifier (the JobItem to re-arm).
            instance_id: Pre-existing instance_id — the re-arm keeps the
                instance that previously ran the job (the post-commit
                late-child re-check detected a generation change on this
                instance). MUST match ``job.instance_id`` in the DB.

        Returns:
            Tuple ``(job, lock_acquired)``:

            - ``(JobItem, True)`` — lock acquired AND admission_state
              transitioned ``done`` -> ``active``. Both writes committed
              atomically inside one transaction.
            - ``(None, False)`` — no-op: missing job, ``admission_state``
              is not ``done`` (concurrent transition raced ahead), or all
              ``concurrency_limit`` slots are taken. Transaction is
              rolled back / left empty — no rows persisted.

        Raises:
            ValueError: Lock acquired but the admission_state UPDATE
                matched 0 rows — defensive catch-all for the race where
                the in-method SELECT saw ``admission_state='done'`` but a
                concurrent actor flipped it before our UPDATE landed.
                The transaction — including the lock INSERT — is rolled
                back atomically; callers do NOT need to release the lock
                manually.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        dialect = self.engine.dialect.name

        with self.engine.begin() as conn:
            # 1. Pre-flight: look up the JobItem to find (project_id,
            # queue_id) and verify admission_state. Doing this inside
            # the same transaction that holds the writes means we see a
            # consistent snapshot. Returns (None, False) for the
            # missing-job / wrong-state branches BEFORE any lock INSERT.
            existing = conn.execute(
                text(
                    "SELECT project_id, queue_id, admission_state "
                    "FROM job_queue_items WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            ).first()
            if existing is None:
                # Missing job — commit empty transaction, return no-op.
                return None, False
            project_id, queue_id, current_admission = existing
            if current_admission != AdmissionState.DONE.value:
                # Not in 'done' — the post-finalize re-arm only applies
                # to a completed job. Any other state means a concurrent
                # actor moved the job; the re-arm should be a no-op.
                return None, False

            # 2. Look up the queue's concurrency_limit. Default to 1 if
            # the queue row is missing (defensive — a 'done' JobItem
            # without a queue row is a pre-F9 migration artefact).
            concurrency_limit = 1
            if queue_id is not None:
                queue_row = conn.execute(
                    text(
                        "SELECT concurrency_limit FROM job_queues "
                        "WHERE queue_id = :queue_id"
                    ),
                    {"queue_id": queue_id},
                ).first()
                if queue_row is not None and queue_row[0]:
                    concurrency_limit = int(queue_row[0])

            # 3. Atomically claim a slot. Same dialect-branching
            # pattern as ``start_job_atomic_with_lock`` — raw
            # ``text()`` SQL so we share the same transaction handle.
            if dialect == "postgresql":
                lock_insert_stmt = text(
                    """
                    INSERT INTO job_locks
                        (lock_id, project_id, queue_id, job_id,
                         instance_id, lock_slot, acquired_at)
                    VALUES
                        (:lock_id, :project_id, :queue_id, :job_id,
                         :instance_id, :slot, :now)
                    ON CONFLICT (project_id, queue_id, lock_slot) DO NOTHING
                    """
                )
            else:
                lock_insert_stmt = text(
                    """
                    INSERT OR IGNORE INTO job_locks
                        (lock_id, project_id, queue_id, job_id,
                         instance_id, lock_slot, acquired_at)
                    VALUES
                        (:lock_id, :project_id, :queue_id, :job_id,
                         :instance_id, :slot, :now)
                    """
                )

            lock_acquired = False
            for slot in range(concurrency_limit):
                lock_id = str(uuid.uuid4())
                result = conn.execute(
                    lock_insert_stmt,
                    {
                        "lock_id": lock_id,
                        "project_id": project_id,
                        "queue_id": queue_id,
                        "job_id": job_id,
                        "instance_id": instance_id,
                        "slot": slot,
                        "now": now_iso,
                    },
                )
                if (result.rowcount or 0) == 1:
                    lock_acquired = True
                    break

            if not lock_acquired:
                # All slots taken — commit empty transaction, return
                # ``(None, False)`` (matches ``start_job_atomic_with_lock``
                # contract for the no-slot case).
                return None, False

            # 4. UPDATE job_queue_items in the SAME transaction. The
            # PostgreSQL ``trg_job_queue_items_active_lock_guard``
            # trigger fires at COMMIT; because both the lock INSERT
            # and this UPDATE are staged in one transaction, the
            # trigger sees both at COMMIT and accepts the new
            # active state.
            update_stmt = text(
                """
                UPDATE job_queue_items
                SET admission_state = :admission_state,
                    instance_id = :instance_id
                WHERE job_id = :job_id
                  AND admission_state = :admission_state_guard
                  AND deleted_at IS NULL
                """
            )
            update_result = conn.execute(
                update_stmt,
                {
                    "admission_state": AdmissionState.ACTIVE.value,
                    "instance_id": instance_id,
                    "job_id": job_id,
                    "admission_state_guard": AdmissionState.DONE.value,
                },
            )

            if (update_result.rowcount or 0) == 0:
                # admission_state guard matched 0 rows: a concurrent
                # actor flipped the state between our pre-flight SELECT
                # and this UPDATE. Rollback (raise) so the lock INSERT
                # is undone atomically — caller never sees a
                # half-committed lock row.
                raise ValueError(
                    f"Cannot re-arm job '{job_id}': admission_state "
                    f"changed from 'done' between pre-flight SELECT and "
                    f"UPDATE (race lost)"
                )

        # Transaction committed successfully — re-read the row to
        # return a fully-populated ``JobItem``.
        with SQLModelSession(self.engine) as session:
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row was deleted between the
                # COMMIT and the SELECT. Preserve the "return None for
                # missing job" contract rather than raising.
                return None, True
            return job, True

    def complete_job(
        self,
        job_id: str,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Complete a job (PROCESSING -> COMPLETED)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status="processing",
            to_status="completed",
            completed_at=now,
            result_summary=result_summary,
        )

    def fail_job(
        self,
        job_id: str,
        error_message: str,
    ) -> JobItem | None:
        """Fail a job (PROCESSING -> FAILED).

        Phase 4 cleanup: also writes ``failed_at`` so the retry
        engine can distinguish a ``done``-admission row that came
        through the FAILED path (retryable) from one that came
        through COMPLETED / CANCELLED (terminal, not retryable).
        The dual-write ``status='failed'`` mirror that previously
        carried this information is gone.
        """
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status="processing",
            to_status="failed",
            completed_at=now,
            error_message=error_message,
            failed_at=now,
        )

    def cancel_job(self, job_id: str) -> JobItem | None:
        """Cancel a job from any cancellable state in a single atomic UPDATE.

        Replaces the prior read-then-dispatch pattern (read job, branch on
        ``status`` in Python, then call ``atomic_transition`` with the
        read ``from_status``). That pattern was vulnerable to a TOCTOU race:
        a concurrent ``start_job`` could transition PENDING -> PROCESSING
        between our read and our UPDATE-WHERE-status='pending', causing
        the dispatched ``PENDING -> CANCELLED`` transition to no-op
        (rowcount=0), raise ``InvalidTransitionError``, and silently
        LOSE the cancel even though ``PROCESSING -> CANCELLED`` is a
        valid transition.

        The new implementation issues a single guarded UPDATE covering
        all cancellable states (PENDING, PROCESSING, FAILED) in one
        statement:

            UPDATE job_queue_items
            SET admission_state='done', terminal_reason='cancelled'
            WHERE job_id=:job_id AND admission_state IN ('queued','active')

        On PostgreSQL, EvalPlanQual re-evaluates the admission_state-IN predicate
        after the row lock is acquired, so a concurrent writer that
        flipped the status between our check and our write cannot slip
        past us. On SQLite, the single-statement UPDATE is atomic at
        the database level. Either way, two concurrent writers cannot
        both observe the predicate as true.

        A disambiguation SELECT only runs when ``rowcount == 0`` to
        distinguish "job doesn't exist" (returns ``None``) from "job
        is in a non-cancellable terminal state" (raises ``ValueError``,
        preserving the original error contract).

        Phase 7c: ``terminal_reason='cancelled'`` is written in the same
        UPDATE so the resolver can surface ``cancelled`` (not the lossy
        legacy ``completed`` default) for ``admission_state='done'``
        rows that came through this path.

        Args:
            job_id: Job identifier.

        Returns:
            Updated JobItem after the UPDATE commits, or ``None`` if the
            job does not exist.

        Raises:
            ValueError: If the job exists but is in a non-cancellable
                state (e.g. COMPLETED, CANCELLED).
        """
        now = datetime.now(timezone.utc).isoformat()
        # Cancellable set includes PAUSED so the atomic UPDATE-WHERE-IN
        # covers the PAUSED→CANCELLED transition defined in
        # ``daemon/services/job_state_machine.py`` TRANSITIONS (the
        # ``"cancel_after_pause"`` transition added in Phase 1 of the
        # pause/resume redesign, 2026-06-25). Without ``PAUSED`` here,
        # calling ``cancel_job`` on a paused job would no-op (rowcount=0)
        # and raise ``ValueError`` even though the state machine marks
        # the transition as legal. PAUSED is non-terminal, so it belongs
        # in this set alongside PENDING / PROCESSING / FAILED.
        cancellable_admission = (
            AdmissionState.QUEUED.value,
            AdmissionState.ACTIVE.value,
        )

        with SQLModelSession(self.engine) as session:
            # Atomic UPDATE with admission_state-IN guard. Single
            # statement covers queued/active (PENDING, PROCESSING,
            # FAILED-awaiting-retry, PAUSED) so a concurrent start_job
            # that flips QUEUED -> ACTIVE between our read and write is
            # still matched by the guard and the cancel is preserved.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.admission_state.in_(cancellable_admission))
                .values(
                    # Phase 4 cleanup: ``status`` is no longer
                    # written (admission_state is the sole
                    # authority). CANCELLED → admission_state =
                    # DONE directly. ``cancelled_at`` was dropped in
                    # Phase 5; cancellation time is now derivable from
                    # the Instance side (Instance.status /
                    # Instance.terminated_at).
                    admission_state=AdmissionState.DONE.value,
                    # Phase 7c: discriminator for ``done`` rows. The
                    # resolver surfaces ``cancelled`` here rather than
                    # ``completed`` (the lossy legacy default). See
                    # ``work_resolver._job_to_record`` — terminal_reason
                    # takes priority for ``admission_state='done'``.
                    terminal_reason="cancelled",
                )
            )
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # UPDATE matched no rows. Two possibilities:
                #   (a) the job_id doesn't exist at all, or
                #   (b) the job exists but is in a non-cancellable state
                #       (COMPLETED, CANCELLED).
                # Disambiguate via follow-up SELECT — same session, so
                # we see the post-UPDATE state.
                existing = session.get(JobItem, job_id)
                if existing is None:
                    return None
                raise ValueError(
                    f"Cannot cancel job in admission_state "
                    f"'{existing.admission_state}', must be 'queued' or 'active'"
                )

            # Re-read the row to return a fully-populated JobItem.
            job = session.get(JobItem, job_id)
            if job is None:
                # Vanishingly unlikely race: row deleted between UPDATE
                # and re-read. Surface as "not found" for symmetry.
                return None
            return job

    def terminate_job(
        self,
        job_id: str,
        error_message: str,
    ) -> JobItem | None:
        """Terminate a job (PROCESSING -> CANCELLED). No retry triggered."""
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status="processing",
            to_status="cancelled",
            completed_at=now,
            error_message=error_message,
        )

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def soft_delete(self, job_id: str) -> JobItem | None:
        """Soft-delete a job by setting deleted_at timestamp.
        
        Idempotent - if already deleted, returns the job as-is.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return None
            if job.deleted_at is not None:
                return job  # Already deleted, idempotent
            job.deleted_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            db_session.refresh(job)
            return job

    def restore(self, job_id: str) -> JobItem | None:
        """Restore a soft-deleted job by clearing deleted_at.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Restored JobItem if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return None
            job.deleted_at = None
            db_session.commit()
            db_session.refresh(job)
            return job

    def hard_delete(self, job_id: str) -> dict[str, Any]:
        """Hard delete — use soft_delete() for normal operations.
        
        Args:
            job_id: Job identifier.
            
        Returns:
            Dictionary with deletion status.
        """
        with SQLModelSession(self.engine) as db_session:
            job = db_session.get(JobItem, job_id)
            if job is None:
                return {"deleted": False, "job_id": job_id, "error": "Not found"}

            db_session.delete(job)
            db_session.commit()

            return {
                "deleted": True,
                "job_id": job_id,
                "agent_dir": job.agent_dir,
            }

    def hard_delete_terminal(self) -> int:
        """Hard delete all terminal jobs (admission_state='done').

        Phase 4: renamed from ``hard_delete_completed``. The query now
        gates on ``admission_state='done'`` which covers COMPLETED,
        FAILED, and CANCELLED (all terminal jobs). Use soft_delete()
        for normal operations.
        
        Returns:
            Number of jobs deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobItem).where(
                JobItem.admission_state == AdmissionState.DONE.value
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def hard_delete_by_project(self, project_id: str) -> int:
        """Hard delete all jobs for a specific project — use soft_delete() for normal operations.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of jobs deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobItem).where(
                JobItem.project_id == project_id
            )
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def find_retryable_jobs(self, project_id: str = None) -> list[JobItem]:
        """Find jobs eligible for retry (QUEUED admission_state with
        next_retry_at set and passed).

        Phase 3: queries admission_state='queued' AND next_retry_at IS NOT NULL
        AND next_retry_at <= now. Under the new model, retried jobs are
        back in admission_state='queued' (set by ``atomic_retry`` in Phase 2).
        A fresh queued job (never tried) also matches
        admission_state='queued' but has ``next_retry_at IS NULL`` — so
        the ``next_retry_at IS NOT NULL`` clause is the discriminator that
        selects ONLY retried jobs waiting for their retry window.

        Jobs that are being cancelled (transitioning to admission_state='done')
        are naturally excluded because their admission_state is no longer
        'queued'.

        Args:
            project_id: Optional project ID to filter by.

        Returns:
            List of JobItem objects that are QUEUED with a non-null
            next_retry_at <= now.
        """
        with SQLModelSession(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            stmt = (
                select(JobItem)
                .where(JobItem.admission_state == AdmissionState.QUEUED.value)
                .where(JobItem.next_retry_at.is_not(None))
                .where(col(JobItem.next_retry_at) <= now)
                .where(JobItem.deleted_at.is_(None))
            )

            if project_id is not None:
                stmt = stmt.where(JobItem.project_id == project_id)

            stmt = stmt.order_by(
                col(JobItem.priority).desc(), JobItem.created_at.desc()
            )
            return list(session.exec(stmt))
