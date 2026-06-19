"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, func, select as sql_select
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col, update as sqlmodel_update

from .models import JobItem, JobQueue, JobStatus, QueueType

logger = logging.getLogger(__name__)


class JobRepository:
    """SQLModel-based Job Queue repository for CRUD operations.
    
    Provides persistence for job queue items with support for
    project-based job serialization.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

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
            agent_id: Agent ID (e.g., 'coder').
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
                status=JobStatus.PENDING.value,
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

    def get_active_by_instance(self, instance_id: str) -> JobItem | None:
        """Get the most recent active (PENDING or PROCESSING) job for an instance.

        Excludes terminal states (COMPLETED, FAILED, CANCELLED, DEAD_LETTER) and
        soft-deleted rows. Used by callers that need to find the current live
        job — never a historical one.

        Args:
            instance_id: Instance identifier.

        Returns:
            Most recent active non-deleted JobItem matching ``instance_id``, or
            ``None`` if none exists.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
                .where(JobItem.status.in_(
                    [JobStatus.PENDING.value, JobStatus.PROCESSING.value]
                ))
                .order_by(JobItem.created_at.desc(), JobItem.job_id)
            )
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
        """Count active jobs (PENDING + PROCESSING) for a project across all queues,
        excluding soft-deleted jobs.
        
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
                .where(JobItem.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]))
                .where(JobItem.deleted_at.is_(None))
            )
            return db_session.exec(stmt).one()

    def count_active_jobs_in_non_defer_queues(self, project_id: str) -> int:
        """Count active jobs (PENDING + PROCESSING) for a project in non-defer queues only.
        
        Used for defer queue idle check to avoid deadlock when multiple defer queues
        exist. This JOINs with job_queues table to exclude defer queue types.
        
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
                .where(JobItem.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value]))
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
                count_stmt = count_stmt.where(JobItem.status.in_(statuses))
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
                stmt = stmt.where(JobItem.status.in_(statuses))
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
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of pending JobItem objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.project_id == project_id)
                .where(JobItem.status == JobStatus.PENDING.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_all_pending(self) -> list[JobItem]:
        """List all pending jobs (for jobs without project_id).
        
        Returns:
            List of all pending JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.status == JobStatus.PENDING.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), col(JobItem.created_at).asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def find_processing_jobs(self) -> list[JobItem]:
        """Find all jobs currently in PROCESSING status.

        Used for startup recovery to identify orphaned jobs.

        Returns:
            List of all processing JobItem objects.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobItem).where(JobItem.status == JobStatus.PROCESSING.value).where(
                JobItem.deleted_at.is_(None)
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def find_processing_message_jobs_by_instance(self, instance_id: str) -> list[JobItem]:
        """Find PROCESSING MESSAGE jobs targeting a specific instance.

        Used for DB-level concurrency gate: if any exist, skip starting a new MESSAGE job.
        Uses JobItem.instance_id column (indexed) — no JSON filtering needed.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.status == JobStatus.PROCESSING.value)
                .where(JobItem.job_type == "message")
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
            )
            return list(db_session.exec(stmt))

    def find_jobs_by_instance(
        self, instance_id: str, job_type: str | None = None
    ) -> list[JobItem]:
        """Find all active jobs for a given instance.

        Used for termination cleanup: cancel ALL MESSAGE jobs for an instance.
        Uses JobItem.instance_id column (indexed) — no JSON filtering needed.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.instance_id == instance_id)
                .where(JobItem.deleted_at.is_(None))
                .where(JobItem.status.in_([JobStatus.PENDING.value, JobStatus.PROCESSING.value, JobStatus.FAILED.value]))
            )
            stmt = stmt.order_by(JobItem.created_at.asc())
            if job_type:
                stmt = stmt.where(JobItem.job_type == job_type)
            return list(db_session.exec(stmt))

    def list_pending_by_queue(self, queue_id: str) -> list[JobItem]:
        """List pending jobs for a specific queue, ordered by priority.
        
        Args:
            queue_id: Queue identifier.
            
        Returns:
            List of pending JobItem objects for the queue.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem)
                .where(JobItem.queue_id == queue_id)
                .where(JobItem.status == JobStatus.PENDING.value)
                .where(JobItem.deleted_at.is_(None))
                .order_by(col(JobItem.priority).desc(), JobItem.created_at.asc())
            )
            jobs = list(db_session.exec(stmt))
            return jobs

    def list_by_queue(
        self,
        queue_id: str,
        statuses: list[str | None] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[JobItem], int]:
        """List jobs for a specific queue with optional filters and pagination.
        
        Args:
            queue_id: Queue identifier.
            statuses: Optional list of status filters.
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
                count_stmt = count_stmt.where(JobItem.status.in_(statuses))
            total = db_session.exec(count_stmt).one()

            # Build list query with filters
            stmt = select(JobItem).where(JobItem.queue_id == queue_id)
            stmt = stmt.where(JobItem.deleted_at.is_(None))
            if statuses:
                stmt = stmt.where(JobItem.status.in_(statuses))
            
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
        ``UPDATE job_queue_items SET status = :to_status, ...extra
         WHERE job_id = :job_id AND status = :from_status``
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

        # Validate transition is allowed — cheap fail-fast before opening
        # a session / issuing the UPDATE. Preserves the original method's
        # ordering of side-effects.
        job_state_machine.validate_transition(from_status, to_status)

        transition_name = job_state_machine.get_transition_name(from_status, to_status)

        # Build the SET clause dynamically from extra_updates. Caller-supplied
        # keys override the default ``status`` only if they happen to be
        # named ``status`` (they shouldn't — ``to_status`` is the canonical
        # way to change status).
        set_values: dict[str, Any] = {"status": to_status, **extra_updates}

        with SQLModelSession(self.engine) as session:
            # Atomic UPDATE with status guard. PostgreSQL EvalPlanQual
            # re-evaluates ``status = :from_status`` after the row lock
            # is acquired; SQLite's single-statement UPDATE is atomic at
            # the database level. Either way, two concurrent writers
            # cannot both observe the predicate as true.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.status == from_status)
                .values(**set_values)
            )
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
                    from_status=existing.status,
                    to_status=to_status,
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
                "Job transition: %s | %s -> %s (%s) | extra_fields=%s",
                job_id, from_status, to_status, transition_name, list(extra_updates.keys())
            )

            return job

    def atomic_retry(
        self,
        job_id: str,
        max_retries: int,
        next_retry_at: str,
    ) -> JobItem | None:
        """Atomically retry a failed job.

        Single guarded UPDATE::

            UPDATE job_queue_items
            SET status = 'pending',
                retry_count = retry_count + 1,   -- atomic SQL increment
                next_retry_at = :next_retry_at,
                failed_at = NULL,
                error_message = NULL
            WHERE job_id = :job_id
              AND status = 'failed'
              AND retry_count < :max_retries
            RETURNING *

        The SQL-level ``status = 'failed' AND retry_count < :max_retries``
        guard is the race-safety boundary. PostgreSQL EvalPlanQual
        re-evaluates the predicate after acquiring the row lock (so a
        concurrent writer that flipped the status between the
        caller's read and this UPDATE cannot slip past us); SQLite's
        single-statement UPDATE is atomic at the database level.
        The ``retry_count = retry_count + 1`` expression lets the
        database compute the increment atomically — no
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

        Returns:
            The updated ``JobItem`` after the UPDATE commits, or
            ``None`` if no row matched — i.e. the job does not
            exist, its status is no longer ``failed`` (concurrent
            ``CANCELLED`` / ``DEAD_LETTER`` transition), or its
            ``retry_count`` has already reached ``max_retries``.
            Callers treat ``None`` uniformly as "skip retry".
        """
        with SQLModelSession(self.engine) as session:
            # Atomic guarded UPDATE. ``retry_count = retry_count + 1``
            # is a SQL expression (not a Python read-then-add), so the
            # increment happens server-side and is not subject to a
            # read-modify-write race. The ``status = 'failed'`` clause
            # is what makes concurrent retries safe: after the first
            # writer commits, the row's status is ``'pending'`` and the
            # second writer's UPDATE matches zero rows.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.status == JobStatus.FAILED.value)
                .where(JobItem.retry_count < max_retries)
                .values(
                    status=JobStatus.PENDING.value,
                    retry_count=JobItem.retry_count + 1,
                    next_retry_at=next_retry_at,
                    failed_at=None,
                    error_message=None,
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
            "status": JobStatus.PROCESSING.value,
            "started_at": now_iso,
            "instance_id": instance_id,
        }

        with SQLModelSession(self.engine) as session:
            # Single guarded UPDATE: only matches rows that are still
            # PENDING. PostgreSQL EvalPlanQual re-evaluates the status
            # predicate after the row lock is acquired; SQLite's
            # single-statement UPDATE is atomic at the database level.
            # Either way, two concurrent writers cannot both observe
            # the predicate as true — fixes the original H3 race.
            stmt = (
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.status == JobStatus.PENDING.value)
                .values(**set_values)
            )
            result = session.exec(stmt)
            session.commit()

            if result.rowcount == 0:
                # UPDATE matched no rows. Two possibilities:
                #   (a) the job_id doesn't exist at all, or
                #   (b) the job exists but its status is no longer
                #       PENDING (concurrent transition). Disambiguate
                #       with a follow-up SELECT — same session, so we
                #       see the post-UPDATE state.
                existing = session.get(JobItem, job_id)
                if existing is None:
                    return None
                raise ValueError(
                    f"Cannot start job in '{existing.status}' state, must be PENDING"
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
            from_status=JobStatus.PENDING.value,
            to_status=JobStatus.PROCESSING.value,
            started_at=now,
            instance_id=instance_id,
        )

    def complete_job(
        self,
        job_id: str,
        result_summary: str | None = None,
    ) -> JobItem | None:
        """Complete a job (PROCESSING -> COMPLETED)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.COMPLETED.value,
            completed_at=now,
            result_summary=result_summary,
        )

    def fail_job(
        self,
        job_id: str,
        error_message: str,
    ) -> JobItem | None:
        """Fail a job (PROCESSING -> FAILED)."""
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.FAILED.value,
            completed_at=now,
            error_message=error_message,
        )

    def cancel_job(self, job_id: str) -> JobItem | None:
        """Cancel a job. Works for both PENDING and PROCESSING states."""
        job = self.get(job_id)
        if job is None:
            return None

        now = datetime.now(timezone.utc).isoformat()

        if job.status == JobStatus.PENDING.value:
            return self.atomic_transition(
                job_id,
                from_status=JobStatus.PENDING.value,
                to_status=JobStatus.CANCELLED.value,
                cancelled_at=now,
            )
        elif job.status == JobStatus.PROCESSING.value:
            return self.atomic_transition(
                job_id,
                from_status=JobStatus.PROCESSING.value,
                to_status=JobStatus.CANCELLED.value,
                cancelled_at=now,
            )
        else:
            raise ValueError(
                f"Cannot cancel job in '{job.status}' state, must be PENDING or PROCESSING"
            )

    def terminate_job(
        self,
        job_id: str,
        error_message: str,
    ) -> JobItem | None:
        """Terminate a job (PROCESSING -> CANCELLED). No retry triggered."""
        now = datetime.now(timezone.utc).isoformat()
        return self.atomic_transition(
            job_id,
            from_status=JobStatus.PROCESSING.value,
            to_status=JobStatus.CANCELLED.value,
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

    def hard_delete_completed(self) -> int:
        """Hard delete all completed jobs — use soft_delete() for normal operations.
        
        Returns:
            Number of jobs deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobItem).where(
                JobItem.status == JobStatus.COMPLETED.value
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
        """Find jobs eligible for retry (FAILED with next_retry_at <= now).
        
        IMPORTANT: This method only finds jobs that are FAILED with next_retry_at
        set and passed. Jobs that are being cancelled (transitioning to CANCELLED)
        are naturally excluded because their status will not be FAILED.
        
        Args:
            project_id: Optional project ID to filter by.
            
        Returns:
            List of JobItem objects that are FAILED and their next_retry_at
            has passed.
        """
        with SQLModelSession(self.engine) as session:
            now = datetime.now(timezone.utc).isoformat()
            stmt = (
                select(JobItem)
                .where(JobItem.status == JobStatus.FAILED.value)
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
