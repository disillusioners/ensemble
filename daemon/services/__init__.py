"""Services package for worker pool and related infrastructure."""

from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.services.main_loop_bridge import MainLoopBridge
from daemon.services.worker_pool import Worker, WorkerPool
from daemon.services.task_processor import TaskProcessor, BaseProcessor
from daemon.services.stale_task_recovery import StaleTaskRecovery
from daemon.services.job_retry_engine import JobRetryEngine

# Instance manager service classes
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.child_reports import ChildReportsService
from daemon.services.error_reporting import ErrorReportingService
from daemon.services.cancellation import CancellationService
from daemon.services.title_generation import TitleGenerationService
from daemon.services.event_publisher import EventPublisherService
from daemon.services.completion_registry import CompletionRegistry, CompletionResult, get_completion_registry

# Context-injection message builders (Phase 1, restructure plan)
from daemon.services.context_messages import (
    assemble_context_messages,
    build_project_context_message,
    build_shared_context_message,
    build_skills_message,
    escape_for_context_block,
    CONTEXT_KIND_PROJECT,
    CONTEXT_KIND_SHARED_CONTEXT,
    CONTEXT_KIND_SKILLS,
    CONTEXT_PREFIX,
    CONTEXT_SUFFIX,
)

__all__ = [
    # Worker pool services
    "JobLockManager",
    "JobQueueService",
    "MainLoopBridge",
    "Worker",
    "WorkerPool",
    "TaskProcessor",
    "BaseProcessor",
    "StaleTaskRecovery",
    "JobRetryEngine",
    # Instance manager services
    "InstanceLifecycleService",
    "InstanceMessagingService",
    "ChildReportsService",
    "ErrorReportingService",
    "CancellationService",
    "TitleGenerationService",
    "EventPublisherService",
    # Completion registry
    "CompletionRegistry",
    "CompletionResult",
    "get_completion_registry",
    # Context-injection message builders (Phase 1, restructure plan)
    "assemble_context_messages",
    "build_project_context_message",
    "build_shared_context_message",
    "build_skills_message",
    "escape_for_context_block",
    "CONTEXT_KIND_PROJECT",
    "CONTEXT_KIND_SHARED_CONTEXT",
    "CONTEXT_KIND_SKILLS",
    "CONTEXT_PREFIX",
    "CONTEXT_SUFFIX",
]
