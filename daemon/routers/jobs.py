"""Jobs router — aggregates sub-routers."""

from fastapi import APIRouter

from daemon.routers.jobs_crud import router as crud_router
from daemon.routers.jobs_management import router as mgmt_router
from daemon.routers.jobs_streaming import router as stream_router
from daemon.routers.jobs_crud import (
    get_job_queue_service,
    get_dead_letter_svc,
    _job_to_response,
    TERMINAL_STATUSES,
)
from daemon.services.job_queue_service import JobQueueService
from daemon.services.dead_letter_service import DeadLetterService
from daemon.routers.schemas import (
    JobResponse,
    JobListResponse,
    JobCreateRequest,
    JobValidationError,
    JobNotFoundResponse,
)


def set_job_queue_service(service: JobQueueService) -> None:
    """Set the JobQueueService instance (for backward compatibility)."""
    get_job_queue_service.set_service(service)


def set_dead_letter_service(service: DeadLetterService) -> None:
    """Set the DeadLetterService instance (for backward compatibility)."""
    get_dead_letter_svc.set_service(service)


# Backward compatibility aliases (from schemas.py)
TaskResponse = JobResponse
TaskListResponse = JobListResponse
TaskCreateRequest = JobCreateRequest
TaskValidationError = JobValidationError
TaskNotFoundResponse = JobNotFoundResponse

# Create aggregator router - no prefix since sub-routers have their own /jobs prefix
router = APIRouter(tags=["jobs"])
router.include_router(crud_router)
router.include_router(mgmt_router)
router.include_router(stream_router)

__all__ = [
    "router",
    # Service dependency accessors
    "get_job_queue_service",
    "get_dead_letter_svc",
    # Backward compatibility setters (for tests)
    "set_job_queue_service",
    "set_dead_letter_service",
    # Shared utilities for external use
    "_job_to_response",
    "TERMINAL_STATUSES",
    # Backward compatibility aliases
    "TaskResponse",
    "TaskListResponse",
    "TaskCreateRequest",
    "TaskValidationError",
    "TaskNotFoundResponse",
]
