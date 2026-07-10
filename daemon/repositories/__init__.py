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
from .job_queue.models import JobItem, JobLockInfo, JobQueue, QueueType

# Task repository
from .task.repository import TaskRepository
from .task.models import Task, TaskStatus, TaskType

# Event repository
from .event.repository import EventRepository
from .event.models import Event, EventKind

# Database connection repository
from .db_connection.repository import DbConnectionRepository
from .db_connection.models import DbConnectionConfig

# Infra asset repository (Phase 1 of the infra info storage design)
from .infra.repository import SQLModelInfraRepository, BootstrapResult
from .infra.models import InfraAsset, InfraAssetType, InfraAssetHistory, InfraChangeType
from .infra.types import JSONBType, InfraTypeDefinition, INFRA_TYPE_DEFINITIONS

# Skill repository (Phase 1 of the Skill Evolution System)
from .skill.repository import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from .skill.models import (
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)

# Dependency Bus repository.
# Imported here so ``SQLModel.metadata.create_all()`` (called from
# ``daemon/manager.py``) registers the ``dependency_watchers`` table
# on fresh PostgreSQL databases. Fresh SQLite databases pick the
# table up from the MigrationRunner instead; the existing migration
# runner pipeline runs after ``create_all`` and applies the
# ``20260621_000001_create_dependency_watchers.sql`` migration.
from .dependency_bus.models import DependencyWatcher, DependencyWatcherState
from .dependency_bus.repository import DependencyWatcherRepository

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
    create_db_connection_repository,
    create_infra_repository,
    create_skill_repository,
    create_skill_lineage_repository,
    create_skill_usage_repository,
    create_skill_trigger_repository,
    create_skill_embedding_repository,
    create_skill_ab_test_repository,
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
    # Database connection
    "DbConnectionRepository",
    "DbConnectionConfig",
    # Infra asset (Phase 1)
    "SQLModelInfraRepository",
    "BootstrapResult",
    "InfraAsset",
    "InfraAssetType",
    "InfraAssetHistory",
    "InfraChangeType",
    "JSONBType",
    "InfraTypeDefinition",
    "INFRA_TYPE_DEFINITIONS",
    # Skill (Phase 1 of the Skill Evolution System)
    "SkillRepository",
    "SkillLineageRepository",
    "SkillUsageRepository",
    "SkillTriggerRepository",
    "SkillEmbeddingRepository",
    "SkillABTestRepository",
    "Skill",
    "SkillLineage",
    "SkillUsageRecord",
    "SkillTrigger",
    "SkillEmbedding",
    "SkillABTest",
    # Dependency Bus
    "DependencyWatcher",
    "DependencyWatcherState",
    "DependencyWatcherRepository",
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
    "create_db_connection_repository",
    "create_infra_repository",
    "create_skill_repository",
    "create_skill_lineage_repository",
    "create_skill_usage_repository",
    "create_skill_trigger_repository",
    "create_skill_embedding_repository",
    "create_skill_ab_test_repository",
    "run_migrations",
]
