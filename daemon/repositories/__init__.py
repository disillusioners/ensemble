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

# MCP Server repository
from .mcp_server.repository import SQLModelMcpServerRepository
from .mcp_server.models import McpServer

# Job queue repository
from .job_queue.repository import JobRepository
from .job_queue.queue_repository import JobQueueRepository
from .job_queue.models import JobItem, JobStatus, JobLockInfo, JobQueue, QueueType

# Task repository
from .task.repository import TaskRepository
from .task.models import Task, TaskStatus, TaskType

# Event repository
from .event.repository import EventRepository
from .event.models import Event, EventKind

# Factory functions
from .factory import (
    DatabaseConfig,
    create_engine_from_config,
    create_project_repository,
    create_instance_repository,
    create_message_queue_repository,
    create_source_repository,
    create_job_repository,
    create_job_queue_repository,
    create_mcp_server_repository,
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
    # MCP Server
    "SQLModelMcpServerRepository",
    "McpServer",
    # Job queue
    "JobRepository",
    "JobQueueRepository",
    "JobItem",
    "JobStatus",
    "JobLockInfo",
    "JobQueue",
    "QueueType",
    # Task
    "TaskRepository",
    "Task",
    "TaskStatus",
    "TaskType",
    # Event
    "EventRepository",
    "Event",
    "EventKind",
    # Factory
    "DatabaseConfig",
    "create_engine_from_config",
    "create_project_repository",
    "create_instance_repository",
    "create_message_queue_repository",
    "create_source_repository",
    "create_job_repository",
    "create_job_queue_repository",
    "create_mcp_server_repository",
    "run_migrations",
]
