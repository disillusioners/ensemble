"""SQLModel-based JobQueue Repository implementation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, func, update
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import ACTIVE_ADMISSION_STATES, AdmissionState, JobItem, JobQueue, QueueType


class JobQueueRepository:
    """SQLModel-based JobQueue repository for CRUD operations.
    
    Provides persistence for named job queues with support for
    per-project job isolation.
    """
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        project_id: str,
        queue_name: str,
        queue_type: str = QueueType.FIFO.value,
        concurrency_limit: int = 1,
        is_system: bool = False,
        is_paused: bool = False,
        description: str | None = None,
    ) -> JobQueue:
        """Create a new job queue.
        
        Args:
            project_id: Project ID that owns this queue.
            queue_name: Human-readable queue name.
            queue_type: Queue type ("fifo", "parallel", or "defer").
            concurrency_limit: Max concurrent jobs (1-20).
            is_system: Whether this is a system queue.
            is_paused: Whether the queue is paused.
            description: Optional description.
            
        Returns:
            Created JobQueue object.
        """
        now = datetime.now(timezone.utc).isoformat()
        
        with SQLModelSession(self.engine) as db_session:
            queue = JobQueue(
                project_id=project_id,
                queue_name=queue_name,
                queue_name_lower=queue_name.lower(),
                queue_type=queue_type,
                concurrency_limit=concurrency_limit,
                is_system=is_system,
                is_paused=is_paused,
                description=description,
                created_at=now,
                updated_at=now,
            )

            db_session.add(queue)
            db_session.commit()
            db_session.refresh(queue)

            return queue

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, queue_id: str) -> JobQueue | None:
        """Get a queue by ID.
        
        Args:
            queue_id: Unique queue identifier.
            
        Returns:
            JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            return queue

    def get_by_name(
        self,
        project_id: str,
        queue_name: str,
    ) -> JobQueue | None:
        """Get a queue by project ID and name (case-insensitive).
        
        Args:
            project_id: Project identifier.
            queue_name: Queue name (case-insensitive lookup).
            
        Returns:
            JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id,
                JobQueue.queue_name_lower == queue_name.lower(),
            )
            queue = db_session.exec(stmt).first()
            return queue

    def list_by_project(self, project_id: str) -> list[JobQueue]:
        """List all queues for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            List of JobQueue objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id
            ).order_by(JobQueue.queue_name_lower)
            queues = list(db_session.exec(stmt))
            return queues

    def get_system_queues(self, project_id: str) -> list[JobQueue]:
        """List all system queues for a project.

        Args:
            project_id: Project identifier.

        Returns:
            List of system JobQueue objects for the project.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(JobQueue).where(
                JobQueue.project_id == project_id,
                JobQueue.is_system == True,
            ).order_by(JobQueue.queue_name_lower)
            queues = list(db_session.exec(stmt))
            return queues

    def list_queues_with_admittable_work(
        self,
        admission_states: list[str] | None = None,
        limit: int = 1000,
    ) -> list[JobQueue]:
        """Return queues that hold JobItems in any of ``admission_states``.

        Work-driven scan (fix for admission starvation: see
        ``JobProcessor._process_next_job`` docstring). Replaces the
        project-list scan previously used by ``_process_next_job``,
        which starved in DBs with >100 projects because
        ``list_projects`` defaulted to ``limit=100, updated_at DESC``
        — the ``system_default_project`` often ranked below the
        cutoff and its queues were never visited.

        Args:
            admission_states: Optional list of ``AdmissionState`` values
                to include. Defaults to ``[QUEUED, ACTIVE]`` — the
                set that counts as "in-flight" and that the worker
                loop needs to make progress on. ``DEAD`` is excluded
                by default; pass ``["queued", "active", "dead"]``
                explicitly to include dead-lettered rows (rare).
            limit: Maximum number of distinct queues to return.
                Caps the result set so the polling hot path stays
                bounded even when backlog is huge. ``ORDER BY`` on
                ``min(created_at)`` ensures oldest backlog is
                processed first when the cap is hit.

        Returns:
            List of ``JobQueue`` rows that have at least one
            non-deleted JobItem in any of the requested admission
            states. Joins through to ``JobItem`` (filtered on
            ``deleted_at IS NULL``) so soft-deleted rows do not
            surface as "admittable".

        Raises:
            ValueError: ``admission_states`` is an empty list. An
                empty ``IN ()`` clause compiles to ``WHERE FALSE`` on
                both SQLite and PostgreSQL, which silently returns
                zero rows and is indistinguishable from "no
                admittable work" — that's an admission-starvation
                footgun, so we surface it loudly. Pass ``None`` (use
                the default) or include at least one admission state.
        """
        if admission_states is None:
            # Single source of truth for the in-flight admission set:
            # ``ACTIVE_ADMISSION_STATES`` (models.py) is the canonical
            # ``frozenset`` that every membership filter routes through
            # (see ``JobRepository`` SQL sites and ``LockRepository``
            # SQL builder). Route the default through it so the set is
            # spelled in exactly one place.
            admission_states = list(ACTIVE_ADMISSION_STATES)

        # Defensive: an empty ``IN ()`` clause compiles to ``WHERE FALSE``
        # on both SQLite and PostgreSQL, which silently returns zero rows
        # and is indistinguishable from "no admittable work" to the
        # caller. That's an admission-starvation footgun — surface it
        # loudly rather than returning an empty list.
        if not admission_states:
            raise ValueError(
                "list_queues_with_admittable_work: admission_states must "
                "be non-empty; an empty IN (...) clause would silently "
                "starve the scan. Pass None (default) or include at "
                "least one admission state."
            )

        with SQLModelSession(self.engine) as db_session:
            # GROUP BY (queue_id, project_id) deduplicates queues that
            # share both IDs — same logical shape as DISTINCT but
            # expressed via GROUP BY so the SELECT can also expose
            # ``func.min(JobItem.created_at)`` for the ORDER BY clause.
            # SQLAlchemy/Core translates the GROUP BY to the
            # dialect-appropriate form (PG GROUP BY, SQLite GROUP BY).
            # Ordering by the oldest pending job keeps a FIFO-ish
            # posture at the queue level.
            stmt = (
                select(JobQueue)
                .join(JobItem, JobItem.queue_id == JobQueue.queue_id)
                .where(JobItem.admission_state.in_(admission_states))
                .where(JobItem.deleted_at.is_(None))
                .group_by(JobQueue.queue_id, JobQueue.project_id)
                .order_by(func.min(JobItem.created_at).asc())
                .limit(limit)
            )
            queues = list(db_session.exec(stmt))
            return queues

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(
        self,
        queue_id: str,
        **updates: Any,
    ) -> JobQueue | None:
        """Update a queue's fields.
        
        Args:
            queue_id: Queue identifier.
            **updates: Fields to update.
            
        Returns:
            Updated JobQueue if found, None otherwise.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            if queue is None:
                return None

            # Handle queue_name update (sync queue_name_lower)
            if 'queue_name' in updates:
                queue.queue_name = updates.pop('queue_name')
                queue.queue_name_lower = queue.queue_name.lower()

            # Update other fields
            for key, value in updates.items():
                if hasattr(queue, key):
                    setattr(queue, key, value)

            # Always update updated_at
            queue.updated_at = datetime.now(timezone.utc).isoformat()

            db_session.commit()
            db_session.refresh(queue)

            return queue

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(self, queue_id: str) -> dict[str, Any]:
        """Delete a queue by ID.
        
        Args:
            queue_id: Queue identifier.
            
        Returns:
            Dictionary with deletion status.
        """
        with SQLModelSession(self.engine) as db_session:
            queue = db_session.get(JobQueue, queue_id)
            if queue is None:
                return {"deleted": False, "queue_id": queue_id, "error": "Not found"}

            db_session.delete(queue)
            db_session.commit()

            return {
                "deleted": True,
                "queue_id": queue_id,
                "project_id": queue.project_id,
            }

    def delete_by_project(self, project_id: str) -> int:
        """Delete all queues for a project.
        
        Args:
            project_id: Project identifier.
            
        Returns:
            Number of queues deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = sql_delete(JobQueue).where(JobQueue.project_id == project_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    # --------------------------------------------------------
    # JOB STATISTICS
    # --------------------------------------------------------

    def count_jobs_by_admission(self, queue_id: str) -> dict[str, int]:
        """Count jobs in a queue grouped by admission_state.

        Args:
            queue_id: Queue identifier.
            
        Returns:
            Dictionary mapping admission_state to count, e.g.
            {"queued": 5, "active": 1, "done": 3}.
        """
        from .models import AdmissionState

        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(JobItem.admission_state, func.count(JobItem.job_id))
                .where(JobItem.queue_id == queue_id)
                # Exclude soft-deleted rows from the active/pending
                # counters — deleted jobs are hidden in the FE by
                # default and would otherwise inflate ``active_jobs``.
                .where(JobItem.deleted_at.is_(None))
                .group_by(JobItem.admission_state)
            )
            results = db_session.exec(stmt).all()
            
            # Initialize with all admission states for consistency
            counts = {state.value: 0 for state in AdmissionState}
            for admission_state, count in results:
                counts[admission_state] = count
            
            return counts

    # Backward-compat alias for the renamed method.
    count_jobs_by_status = count_jobs_by_admission

    def reassign_pending_jobs_atomic(
        self,
        from_queue_id: str,
        to_queue_id: str,
        target_admission_states: list[str | None] = None,
    ) -> int:
        """Atomically reassign jobs from one queue to another.
        
        Uses a single SQL UPDATE statement for atomicity.
        
        Args:
            from_queue_id: Source queue ID.
            to_queue_id: Destination queue ID.
            target_admission_states: List of admission states to reassign
                (default: ["queued"]).
            
        Returns:
            Number of jobs reassigned.
        """
        from .models import AdmissionState

        if target_admission_states is None:
            target_admission_states = [AdmissionState.QUEUED.value]
        
        with SQLModelSession(self.engine) as db_session:
            # Use SQLAlchemy Core update() for atomic execution
            stmt = (
                update(JobItem)
                .where(JobItem.queue_id == from_queue_id)
                .where(JobItem.admission_state.in_(target_admission_states))
                .values(queue_id=to_queue_id)
            )
            result = db_session.execute(stmt)
            db_session.commit()
            
            return result.rowcount
