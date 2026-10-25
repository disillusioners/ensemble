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
    SkillBankItem,
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

# Message metadata side table (Phase 1 C2 — langgraph-checkpoint-perf).
# Imported here so ``SQLModel.metadata.create_all()`` (called from
# ``daemon/manager.py``) registers the ``message_metadata`` table on
# fresh PostgreSQL databases. Fresh SQLite databases pick the table up
# from the MigrationRunner via
# ``daemon/migrations/versions/20260825_000001_create_message_metadata.sql``.
# The repo is intentionally SYNC (decisions.md D14) to match the shared
# engine factory contract.
from .message_metadata.models import MessageMetadata
from .message_metadata.repository import MessageMetadataRepository

# Report Injection queue.
# Imported here so ``SQLModel.metadata.create_all()`` registers the
# ``report_injections`` table. This is a brand-new table (not an
# additive column), so ``create_all`` handles it on every backend
# (fresh + existing) and no ``_ensure_postgres_columns`` entry is
# needed. See ``daemon/repositories/report_injection/`` package
# docstring for the deadlock-fix context.
from .report_injection.models import ReportInjection, ReportInjectionState
from .report_injection.repository import ReportInjectionRepository

# Mission-record anchor tables (completion-subsystem Proposal B PR1).
# Imported here so ``SQLModel.metadata.create_all()`` (called from
# ``daemon/manager.py``) registers the ``mission_records`` /
# ``mission_commissions`` tables. Brand-new tables — no
# ``_ensure_postgres_columns`` entry is needed. The SQLite-side
# canonical DDL ships in the migration runner
# (``20261025_122631_create_mission_records.sql`` +
# ``20261025_122700_create_mission_commissions.sql``).
# NAME COLLISION (read-only note): the legacy mission READ-MODEL is the
# ``MissionRecord`` dataclass at ``daemon/services/mission_resolver.py:241``
# — a DIFFERENT symbol from the ``mission_records`` table below. No
# module imports both, so no import alias is needed.
from .mission_record.models import MissionCommission, MissionRecord

# Instance UI preferences (pin + color tag).
# Imported here so ``SQLModel.metadata.create_all()`` registers the
# ``instance_ui_prefs`` table. This is a brand-new table (not an
# additive column), so ``create_all`` handles it on every backend
# (fresh + existing) and no ``_ensure_postgres_columns`` entry is
# needed.
from .instance_ui_prefs.models import InstanceUiPrefs
from .instance_ui_prefs.repository import InstanceUiPrefsRepository

# Blueprint repository (Project Blueprint system).
# Imported here so SQLModel.metadata.create_all() registers the three
# new tables (project_blueprints, project_blueprint_triggers,
# project_blueprint_revisions). Brand-new tables — no _ensure_postgres_columns.
# Phase 2 (C3): the pending-experience queue table
# (project_blueprint_pending_updates) is also brand-new and is
# registered the same way.
from .blueprint.models import Blueprint, BlueprintTrigger, BlueprintRevision
from .blueprint.pending_models import BlueprintPendingUpdate
from .blueprint.repository import BlueprintRepository
from .blueprint.pending_repository import BlueprintPendingRepository

# Prompt-version package (Phase A of the agent-prompt-immutability
# commission). Re-export the models so SQLModel.metadata.create_all()
# (called from daemon/manager.py) registers the prompt_versions +
# prompt_audit_events tables on fresh PostgreSQL databases. Fresh
# SQLite databases pick the tables up from the MigrationRunner via
# the 20261013_214000_create_prompt_versions.sql migration. Brand-
# new tables — no _ensure_postgres_columns needed.
from .prompt_version import (
    PROMPT_ARTIFACTS,
    PROMPT_AUDIT_EVENT_TYPES,
    PROMPT_STATES,
    PromptAuditEvent,
    PromptVersion,
)

# Service-tool repository (service-tool Phase 1.A store track).
# Imported here so ``SQLModel.metadata.create_all()`` (called from
# ``daemon/manager.py``) registers the ``service_tracking`` table on
# fresh databases (both dialects — the model's dual-dialect partial
# UNIQUE index ``idx_service_tracking_name_active`` carries
# ``sqlite_where``/``postgresql_where``). Existing SQLite databases
# pick the table up from the MigrationRunner via
# ``daemon/migrations/versions/20260915_212810_create_service_tracking.sql``;
# existing PostgreSQL databases rely on the
# ``EnsembleManager._ensure_postgres_columns`` mirror (the .sql
# runner is a NO-OP on PG). The repo is intentionally SYNC (frozen
# F3 interface) — async callers wrap calls in ``asyncio.to_thread``.
from .service_tool.repository import ServiceRepo
from .service_tool.models import ServiceTracking, ServiceStatus

# Prompt-version repository (Phase A of the agent-prompt-immutability
# commission). Re-exported here so the factory function below can
# construct it from the shared engine.
from .prompt_version.repository import PromptVersionRepository

# Custom-agent config package (Phase C of the agent-prompt-
# immutability commission, User Directive #5A — 2026-10-14).
# Re-exported here so ``SQLModel.metadata.create_all()`` registers
# the ``custom_agent_configs`` table on fresh PostgreSQL databases
# and the factory function below can construct it from the shared
# engine. Fresh SQLite databases pick the table up from the
# MigrationRunner via the
# ``20261014_040000_create_custom_agent_configs.sql`` migration.
# Brand-new table — no _ensure_postgres_columns needed
# (stored_watches / prompt_versions precedent).
from .custom_agent_configs import (
    CUSTOM_AGENT_ID_PREFIX,
    CustomAgentConfig,
    CustomAgentConfigRepository,
    CustomAgentIdError,
    is_custom_agent_id,
    make_custom_agent_id,
)

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
    create_shared_meta_kv_repository,
    create_skill_repository,
    create_blueprint_repository,
    create_blueprint_embedding_repository,
    create_blueprint_pending_repository,
    create_skill_lineage_repository,
    create_skill_usage_repository,
    create_skill_trigger_repository,
    create_skill_embedding_repository,
    create_skill_ab_test_repository,
    create_skill_bank_repository,
    create_message_metadata_repository,
    create_prompt_version_repository,
    create_custom_agent_config_repository,
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
    "SkillBankItem",
    # Dependency Bus
    "DependencyWatcher",
    "DependencyWatcherState",
    "DependencyWatcherRepository",
    # Message metadata side table (Phase 1 C2)
    "MessageMetadata",
    "MessageMetadataRepository",
    # Report Injection queue
    "ReportInjection",
    "ReportInjectionState",
    "ReportInjectionRepository",
    # Mission-record anchor (completion-subsystem Proposal B PR1)
    "MissionRecord",
    "MissionCommission",
    # Instance UI preferences (pin + color tag)
    "InstanceUiPrefs",
    "InstanceUiPrefsRepository",
    # Blueprint (Project Blueprint system)
    "Blueprint",
    "BlueprintTrigger",
    "BlueprintRevision",
    "BlueprintRepository",
    "BlueprintPendingUpdate",
    "BlueprintPendingRepository",
    # Service tool (service-tool Phase 1.A store track)
    "ServiceRepo",
    "ServiceTracking",
    "ServiceStatus",
    # Prompt-version (Phase A of agent-prompt-immutability)
    "PromptVersionRepository",
    "PromptVersion",
    "PromptAuditEvent",
    "PROMPT_STATES",
    "PROMPT_AUDIT_EVENT_TYPES",
    "PROMPT_ARTIFACTS",
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
    "create_shared_meta_kv_repository",
    "create_skill_repository",
    "create_blueprint_repository",
    "create_blueprint_embedding_repository",
    "create_blueprint_pending_repository",
    "create_skill_lineage_repository",
    "create_skill_usage_repository",
    "create_skill_trigger_repository",
    "create_skill_embedding_repository",
    "create_skill_ab_test_repository",
    "create_skill_bank_repository",
    "create_message_metadata_repository",
    "create_prompt_version_repository",
    "create_custom_agent_config_repository",
    "run_migrations",
]
