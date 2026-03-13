"""Repository layer for database access."""

# Project repository
from .project.repository import SQLModelProjectRepository
from .project.models import Project, ProjectStatus, ProjectType

# Session repository
from .session.repository import SQLModelSessionRepository
from .session.models import Session, SessionHierarchy, SessionStatus

# Message queue repository
from .message_queue.repository import SQLModelMessageQueueRepository
from .message_queue.models import MessageQueue, MessageStatus

# Source repository
from .source.repository import SQLModelSourceRepository
from .source.models import SourceConfig, SessionMapping, ProcessedMessage, SourceStatus

# Factory functions
from .factory import (
    DatabaseConfig,
    create_engine_from_config,
    create_project_repository,
    create_session_repository,
    create_message_queue_repository,
    create_source_repository,
)

__all__ = [
    # Project
    "SQLModelProjectRepository",
    "Project",
    "ProjectStatus",
    "ProjectType",
    # Session
    "SQLModelSessionRepository",
    "Session",
    "SessionHierarchy",
    "SessionStatus",
    # Message queue
    "SQLModelMessageQueueRepository",
    "MessageQueue",
    "MessageStatus",
    # Source
    "SQLModelSourceRepository",
    "SourceConfig",
    "SessionMapping",
    "ProcessedMessage",
    "SourceStatus",
    # Factory
    "DatabaseConfig",
    "create_engine_from_config",
    "create_project_repository",
    "create_session_repository",
    "create_message_queue_repository",
    "create_source_repository",
]
