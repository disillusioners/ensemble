"""Dead Letter Queue Service - Handles moving failed jobs to DLQ and replaying them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List

from daemon.repositories.job_queue import DeadLetterItem, JobRepository
from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.models import status_to_admission
from daemon.services.job_state_machine import InvalidTransitionError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as SQLAlchemySession
    from sqlmodel import Session as SQLModelSession

logger = logging.getLogger(__name__)


class DeadLetterServiceError(Exception):
    """Base exception for DeadLetterService errors."""
    pass


class JobNotInFailedStateError(DeadLetterServiceError):
    """Raised when trying to move a non-FAILED job to DLQ."""
    
    def __init__(self, job_id: str, current_status: str) -> None:
        self.job_id = job_id
        self.current_status = current_status
        super().__init__(
            f"Job {job_id} is in '{current_status}' state, must be FAILED to move to DLQ"
        )


class DLQItemNotFoundError(DeadLetterServiceError):
    """Raised when DLQ item is not found."""
    
    def __init__(self, dlq_id: str) -> None:
        self.dlq_id = dlq_id
        super().__init__(f"DLQ item {dlq_id} not found")


class DeadLetterService:
    """Service for managing the dead letter queue.
    
    Handles atomic operations for:
    - Moving failed jobs to the dead letter queue
    - Replaying jobs from the dead letter queue
    - Listing and managing DLQ items
    """
    
    def __init__(
        self,
        job_repository: JobRepository,
        dlq_repository: DeadLetterRepository,
        job_queue_service: Any = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        """Initialize the DeadLetterService.
        
        Args:
            job_repository: Repository for job persistence.
            dlq_repository: Repository for DLQ item persistence.
            job_queue_service: Optional JobQueueService for watcher notifications.
            loop: Optional event loop for async notifications.
        """
        self._job_repo = job_repository
        self._dlq_repo = dlq_repository
        self._job_queue_service = job_queue_service
        self._loop = loop
    
    def move_to_dlq(
        self,
        session: "SQLModelSession",
        job_id: str,
        reason: str = "MAX_RETRIES",
        from_admission_state: str = "failed",
    ) -> DeadLetterItem:
        """Move a failed job to the dead-letter queue atomically.
        
        MUST be called within an existing session/transaction.
        This method does NOT create its own session.
        
        Uses pessimistic locking (FOR UPDATE) to prevent TOCTOU race conditions
        when multiple processes try to move the same job to DLQ.
        
        Defense-in-depth: the status UPDATE additionally carries a
        ``WHERE admission_state = :from_admission_state`` guard so that a
        concurrent writer which somehow slipped past the row lock (or a
        future caller that bypasses the Python check) cannot clobber a
        non-eligible state.
        
        Phase 4 (Job as Queue Proxy): the SQL guard moved from
        ``status = 'failed'`` to ``admission_state = :from_admission_state``
        (default ``'active'``). The plan's §3.2 retry-without-instance
        guarantee removes the intermediate FAILED state — finalize paths
        transition ``active → dead`` directly through ``_finalize_terminal``.
        Legacy callers may still pass ``from_admission_state='failed'`` to
        preserve the old behavior.
        
        Args:
            session: An existing SQLModel Session (shared transaction).
            job_id: The job to move.
            reason: "MAX_RETRIES" or "MANUAL".
            from_admission_state: Admission-state guard (default ``'active'``
                for Phase 4 callers; legacy callers may pass ``'failed'``).
        
        Returns:
            The created DeadLetterItem.
        
        Raises:
            ValueError: If job not found or not in eligible state.
        """
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import update as sqlmodel_update
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        
        # Use FOR UPDATE to acquire pessimistic row lock, preventing TOCTOU race
        job = session.get(JobItem, job_id, with_for_update=True)
        
        if job is None:
            raise DLQItemNotFoundError(job_id)
        
        # Verify job is in the eligible state (now safe under lock).
        # Phase 4 (Job as Queue Proxy): the eligibility check matches
        # the SQL guard below — for ``from_admission_state='active'``
        # (Phase 4 canonical, Plan §3.1), the check is on the
        # admission_state column; for the legacy default
        # ``from_admission_state='failed'``, the check falls back to
        # ``status='failed'`` (the dual-write mirror per
        # ``status_to_admission``). Both checks are performed under
        # the row lock so a concurrent writer that flipped the state
        # between the caller's read and our UPDATE cannot slip past
        # us.
        if from_admission_state == AdmissionState.ACTIVE.value:
            if job.admission_state != AdmissionState.ACTIVE.value:
                raise JobNotInFailedStateError(job_id, job.status)
        else:
            # Legacy / dual-write mirror path (``'failed'`` source).
            if job.status != "failed":
                raise JobNotInFailedStateError(job_id, job.status)
        
        # Ensure project_id is normalized (defense-in-depth)
        if job.project_id is None:
            raise ValueError("project_id must be normalized before DLQ insert. This indicates a normalization gap.")
        
        # Create DLQ item from job data
        dlq_item = DeadLetterItem(
            job_id=job.job_id,
            agent_id=job.agent_id,
            agent_dir=job.agent_dir,
            message=job.message,
            source=job.source,
            project_id=job.project_id,
            queue_id=job.queue_id,
            priority=job.priority,
            error_message=job.error_message or "",
            retry_count=job.retry_count,
            failed_at=job.failed_at or datetime.now(timezone.utc).isoformat(),
            reason=reason,
            metadata_json=job.job_metadata,
        )
        
        try:
            # Add DLQ item to session
            session.add(dlq_item)
            
            # SQL-level guard (defense-in-depth). The FOR UPDATE lock
            # + Python check above are the primary guard; this
            # ``WHERE`` clause ensures that a concurrent writer which
            # slipped past the lock cannot silently transition a
            # non-eligible job. Mirrors the gold-template pattern in
            # JobRepository.atomic_transition.
            #
            # Phase 4 (Job as Queue Proxy): the SQL guard mirrors
            # the Python eligibility check above — for
            # ``from_admission_state='active'`` (Phase 4 canonical),
            # we gate on the admission_state column (Plan §3.1
            # authority); for the legacy default
            # ``from_admission_state='failed'``, we fall back to the
            # legacy ``status='failed'`` predicate (the dual-write
            # mirror per ``status_to_admission``). Both predicates
            # identify the same eligible row under the Phase 2+ dual-
            # write contract.
            if from_admission_state == "active":
                guard_clause = JobItem.admission_state == from_admission_state
            else:
                guard_clause = JobItem.status == "failed"
            update_result = session.exec(
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(guard_clause)
                # Phase 2 dual-write: DEAD_LETTER → admission_state =
                # DEAD. Co-moved with status in the SAME guarded UPDATE
                # so the two columns stay consistent at the queue/DLQ
                # boundary. ``status_to_admission`` is the single
                # source of truth.
                .values(
                    status="dead_letter",
                    admission_state=status_to_admission("dead_letter"),
                )
            )

            if update_result.rowcount == 0:
                # Concurrent process flipped this job out of the eligible
                # state between our Python check and this UPDATE. Detach
                # the pending DLQ item so the caller's commit does not
                # insert a DLQ row for a job that is no longer eligible.
                session.expunge(dlq_item)
                raise JobNotInFailedStateError(job_id, job.status)

            # Let the caller commit the session
            return dlq_item
        except IntegrityError:
            # Concurrent process already moved this job to DLQ
            session.rollback()
            raise JobNotInFailedStateError(job_id, job.status)
    
    def move_to_dlq_standalone(
        self,
        job_id: str,
        reason: str = "MAX_RETRIES",
        from_admission_state: str = "failed",
    ) -> DeadLetterItem:
        """Atomically move a FAILED job to the dead letter queue.
        
        This is a standalone version that creates its own session.
        Use move_to_dlq() when you need to participate in a shared transaction.
        
        Both the job status transition AND DLQ item creation happen in the
        single transaction - either both succeed or both fail.
        
        Uses pessimistic locking (FOR UPDATE) to prevent TOCTOU race conditions
        when multiple processes try to move the same job to DLQ.
        
        Phase 4 (Job as Queue Proxy): the SQL guard moved from
        ``status = 'failed'`` to ``admission_state = :from_admission_state``
        (default ``'active'``). See ``move_to_dlq`` for the rationale.
        Legacy callers may pass ``from_admission_state='failed'``.
        
        Args:
            job_id: The job to move to DLQ.
            reason: Reason for moving to DLQ (e.g., "MAX_RETRIES", "MANUAL").
            from_admission_state: Admission-state guard (default ``'active'``).
            
        Returns:
            The created DeadLetterItem.
            
        Raises:
            JobNotInFailedStateError: If job is not in eligible state (including concurrent modification).
        """
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import Session as SQLModelSession, update as sqlmodel_update
        from daemon.repositories.job_queue.models import JobItem, AdmissionState
        
        with SQLModelSession(self._job_repo.engine) as session:
            # Use FOR UPDATE to acquire pessimistic row lock, preventing TOCTOU race
            job = session.get(JobItem, job_id, with_for_update=True)
            if job is None:
                raise DLQItemNotFoundError(job_id)
            
            # Validate job is in the eligible state (now safe under lock).
            # Phase 4: prefer the admission_state check; fall back to the
            # legacy status check when caller pinned FAILED.
            if from_admission_state == AdmissionState.ACTIVE.value:
                if job.admission_state != AdmissionState.ACTIVE.value:
                    raise JobNotInFailedStateError(job_id, job.status)
            else:
                if job.status != "failed":
                    raise JobNotInFailedStateError(job_id, job.status)
            
            # Ensure project_id is normalized (defense-in-depth)
            if job.project_id is None:
                raise ValueError("project_id must be normalized before DLQ standalone insert. This indicates a normalization gap.")
            
            # Create DLQ item from job data
            dlq_item = DeadLetterItem(
                job_id=job.job_id,
                agent_id=job.agent_id,
                agent_dir=job.agent_dir,
                message=job.message,
                source=job.source,
                project_id=job.project_id,
                queue_id=job.queue_id,
                priority=job.priority,
                error_message=job.error_message or "",
                retry_count=job.retry_count,
                failed_at=job.failed_at or datetime.now(timezone.utc).isoformat(),
                reason=reason,
                metadata_json=job.job_metadata,
            )
            
            try:
                # Add DLQ item to session
                session.add(dlq_item)
                
                # SQL-level guard (defense-in-depth). The
                # FOR UPDATE lock + Python check above are the primary
                # guard; this ``WHERE`` clause ensures a concurrent
                # writer which slipped past the lock cannot silently
                # transition a non-eligible job. Mirrors the gold-
                # template pattern in JobRepository.atomic_transition.
                #
                # Phase 4 (Job as Queue Proxy): the SQL guard now
                # uses ``admission_state`` (the queue-proxy authority
                # per Plan §3.1). For backward compatibility with
                # legacy callers that pre-date Phase 4 (default
                # ``from_admission_state='failed'``), the guard
                # falls back to the legacy ``status='failed'``
                # predicate — the dual-write mapping
                # ``status_to_admission('failed')='done'`` keeps the
                # two columns in lockstep under Phase 2+, so either
                # predicate matches the same row.
                if from_admission_state == "active":
                    guard_clause = JobItem.admission_state == from_admission_state
                else:
                    # Legacy / dual-write mirror — gate on the
                    # status column for ``from_admission_state=
                    # 'failed'`` (the legacy SQL guard the M3
                    # status-guard tests assert).
                    guard_clause = JobItem.status == "failed"
                update_result = session.exec(
                    sqlmodel_update(JobItem)
                    .where(JobItem.job_id == job_id)
                    .where(guard_clause)
                    # Phase 2 dual-write: DEAD_LETTER → admission_state
                    # = DEAD in the same guarded UPDATE.
                    .values(
                        status="dead_letter",
                        admission_state=status_to_admission("dead_letter"),
                    )
                )
                
                if update_result.rowcount == 0:
                    # Concurrent process flipped this job out of the
                    # eligible state between our Python check and this
                    # UPDATE. Detach the pending DLQ item and roll back
                    # this standalone transaction so no partial state is
                    # committed.
                    session.expunge(dlq_item)
                    session.rollback()
                    raise JobNotInFailedStateError(job_id, job.status)

                # Commit both operations atomically
                session.commit()
                session.refresh(dlq_item)

                # Notify watchers after successful commit
                if self._job_queue_service and self._loop and self._loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._job_queue_service.notify_watchers(job_id, "dead_letter", job.error_message),
                        self._loop,
                    )

                return dlq_item
            except IntegrityError:
                # Concurrent process already moved this job to DLQ
                session.rollback()
                raise JobNotInFailedStateError(job_id, job.status)

    def replay_from_dlq(self, dlq_id: str) -> Any:
        """Atomically replay a job from the dead letter queue.
        
        This operation in a SINGLE transaction:
        1. Fetches the DLQ item (with FOR UPDATE lock)
        2. Updates the job to PENDING status (resetting retry_count)
        3. Deletes the DLQ item
        
        Either all operations succeed or none do.
        
        Uses pessimistic locking (FOR UPDATE) on the DLQ item to prevent
        concurrent replays of the same DLQ item.
        
        Defense-in-depth: the job status UPDATE additionally carries a
        ``WHERE status = 'dead_letter'`` guard so that a concurrent
        writer which somehow slipped past the row lock (or a future
        caller that bypasses the Python check) cannot clobber a
        non-dead_letter status. All retry/clear fields are reset in the
        SAME guarded UPDATE — there is no ORM-level read-modify-write
        window for a concurrent ``atomic_retry`` to slip into.
        
        Args:
            dlq_id: The DLQ item to replay.
            
        Returns:
            The updated JobItem.
            
        Raises:
            DLQItemNotFoundError: If DLQ item not found.
        """
        from sqlmodel import Session as SQLModelSession, update as sqlmodel_update
        from daemon.repositories.job_queue.models import JobItem, DeadLetterItem
        
        with SQLModelSession(self._job_repo.engine) as session:
            # Fetch DLQ item with FOR UPDATE lock to prevent concurrent replay
            dlq_item = session.get(DeadLetterItem, dlq_id, with_for_update=True)
            if dlq_item is None:
                raise DLQItemNotFoundError(dlq_id)
            
            job_id = dlq_item.job_id
            
            # Fetch the job from the same session (also lock to prevent concurrent modifications)
            job = session.get(JobItem, job_id, with_for_update=True)
            if job is None:
                raise DLQItemNotFoundError(dlq_id)
            
            # Verify job is in dead_letter state
            if job.status != "dead_letter":
                from daemon.services.job_state_machine import InvalidTransitionError
                raise InvalidTransitionError(
                    job_id=job_id,
                    from_status=job.status,
                    to_status="pending",
                )
            
            # Atomic UPDATE with status guard (defense-in-depth). All
            # retry/clear fields are reset in the SAME guarded UPDATE —
            # no ORM-level read-modify-write window for a concurrent
            # atomic_retry to slip into. Mirrors the gold-template
            # pattern in JobRepository.atomic_transition.
            update_result = session.exec(
                sqlmodel_update(JobItem)
                .where(JobItem.job_id == job_id)
                .where(JobItem.status == "dead_letter")
                # Phase 2 dual-write: PENDING → admission_state =
                # QUEUED. All retry/clear fields are reset in the SAME
                # guarded UPDATE — including admission_state, which
                # was previously DEAD on this row.
                .values(
                    status="pending",
                    admission_state=status_to_admission("pending"),
                    retry_count=0,
                    failed_at=None,
                    error_message=None,
                    started_at=None,
                    completed_at=None,
                    instance_id=None,
                )
            )
            
            if update_result.rowcount == 0:
                # Concurrent process flipped this job out of 'dead_letter'
                # between our Python check and this UPDATE.
                from daemon.services.job_state_machine import InvalidTransitionError
                raise InvalidTransitionError(
                    job_id=job_id,
                    from_status=job.status,
                    to_status="pending",
                )
            
            # Delete the DLQ item
            session.delete(dlq_item)
            
            # Commit both operations atomically
            session.commit()
            
            # Re-read the updated job to return a fully-populated JobItem
            # (mirrors the gold-template `transition_status_if` approach).
            replayed_job = session.get(JobItem, job_id)
            if replayed_job is None:
                # Vanishingly unlikely race: row was deleted between the
                # UPDATE and the SELECT. Preserve the "raise on missing
                # job" contract.
                raise DLQItemNotFoundError(dlq_id)
            
            return replayed_job
    
    def list_dlq(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
        reason: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[DeadLetterItem], int]:
        """List dead letter queue items with optional filters and pagination.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            reason: Optional reason filter.
            limit: Maximum number of items to return.
            offset: Number of items to skip for pagination.
            
        Returns:
            Tuple of (list of matching items, total count BEFORE pagination).
        """
        return self._dlq_repo.list(
            project_id=project_id,
            queue_id=queue_id,
            reason=reason,
            limit=limit,
            offset=offset,
        )
    
    def get_dlq(self, dlq_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by DLQ ID.
        
        Args:
            dlq_id: The DLQ identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        return self._dlq_repo.get(dlq_id)
    
    def get_dlq_by_job_id(self, job_id: str) -> DeadLetterItem | None:
        """Get a dead letter item by original job ID.
        
        Args:
            job_id: The original job identifier.
            
        Returns:
            DeadLetterItem if found, None otherwise.
        """
        return self._dlq_repo.get_by_job_id(job_id)
    
    def delete_dlq(self, dlq_id: str) -> bool:
        """Delete a dead letter item by DLQ ID.
        
        Args:
            dlq_id: The DLQ identifier.
            
        Returns:
            True if deleted, False if not found.
        """
        return self._dlq_repo.delete(dlq_id)
    
    def cleanup_dlq(
        self,
        max_age_days: int,
        reason: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Delete dead letter items older than max_age_days.
        
        Args:
            max_age_days: Maximum age in days for items to keep.
            reason: Optional reason filter to only delete items with specific reason.
            project_id: Optional project ID filter to only delete items for a specific project.
            
        Returns:
            Number of items deleted.
        """
        return self._dlq_repo.cleanup_by_age(
            max_age_days * 24,
            reason=reason,
            project_id=project_id,
        )
    
    def count_dlq(
        self,
        project_id: str | None = None,
        queue_id: str | None = None,
    ) -> int:
        """Count dead letter queue items with optional filters.
        
        Args:
            project_id: Optional project ID filter.
            queue_id: Optional queue ID filter.
            
        Returns:
            Count of matching items.
        """
        return self._dlq_repo.count(
            project_id=project_id,
            queue_id=queue_id,
        )


# Module-level singleton for dependency injection
_service: DeadLetterService | None = None


def get_dead_letter_service() -> DeadLetterService:
    """Get the module-level DeadLetterService instance.
    
    Returns:
        The singleton DeadLetterService instance.
        
    Raises:
        RuntimeError: If service has not been initialized.
    """
    if _service is None:
        raise RuntimeError("DeadLetterService has not been initialized")
    return _service


def set_dead_letter_service(service: DeadLetterService) -> None:
    """Set the module-level DeadLetterService instance.
    
    Args:
        service: The service instance to use.
    """
    global _service
    _service = service
