"""Repository layer for database access."""

# Project repository
from .project.repository import SQLModelProjectRepository
from .project.models import Project, ProjectStatus, ProjectType

# Instance repository
from .instance.repository import SQLModelInstanceRepository
from .instance.models import Instance, InstanceHierarchy, InstanceStatus

# Message queue repository
from .message_queue.repository import SQLModelMessageQueueRepository
from .message_queue.models import MessageQueue, MessageStatus

# Source repository
from .source.repository import SQLModelSourceRepository
from .source.models import SourceConfig, InstanceMapping, ProcessedMessage, SourceStatus

# Job queue repository
from .job_queue.repository import JobRepository
from .job_queue.models import JobItem, JobStatus, JobLockInfo

# Factory functions
from .factory import (
    DatabaseConfig,
    create_engine_from_config,
    create_project_repository,
    create_instance_repository,
    create_message_queue_repository,
    create_source_repository,
    create_job_repository,
    run_migrations,
)

__all__ = [
    # Project
    "SQLModelProjectRepository",
    "Project",
    "ProjectStatus",
    "ProjectType",
    # Instance
    "SQLModelInstanceRepository",
    "Instance",
    "InstanceHierarchy",
    "InstanceStatus",
    # Message queue
    "SQLModelMessageQueueRepository",
    "MessageQueue",
    "MessageStatus",
    # Source
    "SQLModelSourceRepository",
    "SourceConfig",
    "InstanceMapping",
    "ProcessedMessage",
    "SourceStatus",
    # Job queue
    "JobRepository",
    "JobItem",
    "JobStatus",
    "JobLockInfo",
    # Factory
    "DatabaseConfig",
    "create_engine_from_config",
    "create_project_repository",
    "create_instance_repository",
    "create_message_queue_repository",
    "create_source_repository",
    "create_job_repository",
    "run_migrations",
]
