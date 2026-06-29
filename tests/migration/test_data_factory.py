"""Test data factory for SQLite→PostgreSQL migration E2E testing.

Populates a SQLite database with deterministic test data covering all 22
SQLModel tables, every column type (UUID, JSON, TEXT, INTEGER, BOOLEAN,
DATETIME), and edge cases (empty strings, null values, unicode, deeply
nested JSON, large text fields).

Usage::

    python tests/migration/test_data_factory.py          # creates /tmp/test_migration.db
    python tests/migration/test_data_factory.py /path.db  # creates at given path

The module is designed to run standalone — no PostgreSQL required.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

# ---------------------------------------------------------------------------
# Import every model module so its tables register with SQLModel.metadata.
# This mirrors the import list in daemon.migrations.data_migrator.
# ---------------------------------------------------------------------------
from daemon.repositories.instance import models as _instance_models  # noqa: F401
from daemon.repositories.project import models as _project_models  # noqa: F401
from daemon.repositories.source import models as _source_models  # noqa: F401
from daemon.repositories.job_queue import models as _job_queue_models  # noqa: F401
from daemon.repositories.job_queue import watcher_models as _watcher_models  # noqa: F401
from daemon.repositories.message_queue import models as _message_queue_models  # noqa: F401
from daemon.repositories.mcp_server import models as _mcp_server_models  # noqa: F401
from daemon.repositories.task import models as _task_models  # noqa: F401
from daemon.repositories.event import models as _event_models  # noqa: F401
from daemon.migrations.models import SchemaMigration as _SchemaMigration  # noqa: F401

# Re-export model classes used for data insertion
from daemon.repositories.project.models import (
    CriticalNoteModel,
    Project,
    ProjectHistoryEntry,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectTagLink,
)
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.source.models import (
    InstanceMapping,
    ProcessedMessage,
    ScheduleExecution,
    SourceConfig,
)
from daemon.repositories.job_queue.models import (
    DeadLetterItem,
    JobItem,
    JobLock,
    JobQueue,
)
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.event.models import Event
from daemon.repositories.mcp_server.models import McpServer
from daemon.repositories.task.models import Task
from daemon.migrations.models import SchemaMigration



# >>> test-local status_to_admission (Phase 4 cleanup) <<<
# Phase 4 cleanup removed ``status_to_admission`` from
# ``daemon.repositories.job_queue.models``. Redefined here for test
# seeds that derive ``admission_state`` from a ``status`` value.
def status_to_admission(status):  # noqa: ANN001,ANN201
    return {
        "pending": "queued",
        "processing": "active",
        "paused": "active",
        "completed": "done",
        "failed": "done",
        "cancelled": "done",
        "dead_letter": "dead",
    }.get(status, "queued")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Deterministic timestamps
# ---------------------------------------------------------------------------

_TS_BASE = "2025-01-15T10:00:00+00:00"
_TS_OFFSETS: dict[str, int] = {}
_ts_counter = 0


def _ts(label: str = "") -> str:
    """Return a deterministic ISO timestamp, incrementing per call."""
    global _ts_counter
    _ts_counter += 1
    # Deterministic: base + N minutes
    base = datetime.fromisoformat(_TS_BASE)
    offset_minutes = _ts_counter * 7
    dt = base + __import__("datetime").timedelta(minutes=offset_minutes)
    return dt.isoformat()


def _dt(label: str = "") -> datetime:
    """Return a deterministic datetime object."""
    return datetime.fromisoformat(_ts(label))


def _reset_ts() -> None:
    """Reset the timestamp counter for reproducibility."""
    global _ts_counter
    _ts_counter = 0


# ---------------------------------------------------------------------------
# Deterministic UUIDs
# ---------------------------------------------------------------------------

_UUID_COUNTER = 0


def _uuid() -> str:
    """Return a deterministic UUID-like string."""
    global _UUID_COUNTER
    _UUID_COUNTER += 1
    return f"test-{_UUID_COUNTER:08d}-0000"


def _reset_uuids() -> None:
    """Reset the UUID counter for reproducibility."""
    global _UUID_COUNTER
    _UUID_COUNTER = 0


# ---------------------------------------------------------------------------
# Edge-case test data
# ---------------------------------------------------------------------------

UNICODE_TEXT = "日本語テスト Ñoño café résumé → ← ü ö ä ß 你好世界 🚀 💡"
EMPTY_STRING = ""
LONG_TEXT = "x" * 5000  # Large text field

DEEPLY_NESTED_JSON = {
    "level_1": {
        "level_2": {
            "level_3": {
                "level_4": {
                    "level_5": {
                        "value": "deep",
                        "items": [1, 2, 3, {"nested_list_item": True}],
                    }
                }
            }
        }
    },
    "unicode_key_日本語": "unicode_value",
    "null_value": None,
    "empty_string": "",
    "boolean_values": [True, False],
    "large_number": 999999999999,
}


# ---------------------------------------------------------------------------
# Population functions per table
# ---------------------------------------------------------------------------


def _populate_projects(session: Session, config: dict) -> dict[str, int]:
    """Populate projects and related junction/metadata tables.

    Creates 3 projects covering all field types and edge cases.
    """
    project_ids = [_uuid() for _ in range(3)]

    # --- projects ---
    projects_data = [
        Project(
            project_id=project_ids[0],
            name="test-project-alpha",
            project_type="software",
            status="active",
            main_directory="/projects/alpha",
            related_directories=["/shared/lib", "/tools"],
            description="Test project with full fields",
            job_queue_paused=False,
            project_metadata={
                "framework": "react",
                "version": 3,
                "nested": {"key": "value"},
            },
            relationships={"depends_on": [], "blocks": ["beta"]},
            creator_instance_id=_uuid(),
            creator_agent_id="leader",
            created_at=_ts(),
            updated_at=_ts(),
        ),
        Project(
            project_id=project_ids[1],
            name="test-project-beta",
            project_type="documentation",
            status="paused",
            main_directory="/docs/beta",
            related_directories=[],
            description=UNICODE_TEXT,
            job_queue_paused=True,
            project_metadata=DEEPLY_NESTED_JSON,
            relationships={},
            creator_instance_id=None,
            creator_agent_id=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        Project(
            project_id=project_ids[2],
            name="test-project-gamma",
            project_type="research",
            status="completed",
            main_directory=None,
            related_directories=None,
            description=None,
            job_queue_paused=False,
            project_metadata={},
            relationships=None,
            creator_instance_id=None,
            creator_agent_id=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
    ]
    for p in projects_data:
        session.add(p)
    session.commit()

    # --- project_tags ---
    tags_data = [
        ProjectTagLink(project_id=project_ids[0], tag="python"),
        ProjectTagLink(project_id=project_ids[0], tag="backend"),
        ProjectTagLink(project_id=project_ids[0], tag=UNICODE_TEXT[:50]),
        ProjectTagLink(project_id=project_ids[1], tag="docs"),
    ]
    for t in tags_data:
        session.add(t)
    session.commit()

    # --- project_shortnames ---
    shortnames_data = [
        ProjectShortnameLink(project_id=project_ids[0], shortname="alpha"),
        ProjectShortnameLink(project_id=project_ids[1], shortname="beta"),
        ProjectShortnameLink(project_id=project_ids[2], shortname=""),
    ]
    for s in shortnames_data:
        session.add(s)
    session.commit()

    # --- project_history ---
    history_data = [
        ProjectHistoryEntry(
            id=_uuid(),
            project_id=project_ids[0],
            entry_type="milestone",
            summary="Project created",
            details=LONG_TEXT,
            source_agent="leader",
            source_instance_id=_uuid(),
            entry_metadata={"key": "value", "nested": {"deep": True}},
            created_at=_dt(),
        ),
        ProjectHistoryEntry(
            id=_uuid(),
            project_id=project_ids[0],
            entry_type="commit",
            summary=UNICODE_TEXT[:300],
            details=None,
            source_agent=None,
            source_instance_id=None,
            entry_metadata=None,
            created_at=_dt(),
        ),
        ProjectHistoryEntry(
            id=_uuid(),
            project_id=project_ids[1],
            entry_type="note",
            summary="",
            details="",
            source_agent="developer",
            source_instance_id=None,
            entry_metadata={},
            created_at=_dt(),
        ),
    ]
    for h in history_data:
        session.add(h)
    session.commit()

    # --- project_metadata_records ---
    metadata_records = [
        ProjectMetadataRecord(
            project_id=project_ids[0],
            meta_key="framework",
            meta_value="react",
            created_at=_ts(),
            updated_at=_ts(),
        ),
        ProjectMetadataRecord(
            project_id=project_ids[0],
            meta_key="config_json",
            meta_value=DEEPLY_NESTED_JSON,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        ProjectMetadataRecord(
            project_id=project_ids[1],
            meta_key="null_value",
            meta_value=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
    ]
    for m in metadata_records:
        session.add(m)
    session.commit()

    # --- critical_notes ---
    critical_notes = [
        CriticalNoteModel(
            id=_uuid(),
            project_id=project_ids[0],
            created_at=_ts(),
            updated_at=_ts(),
            source_agent="leader",
            category="convention",
            priority="critical",
            summary="Use snake_case for all Python files",
            reference="https://docs.python.org/3/",
        ),
        CriticalNoteModel(
            id=_uuid(),
            project_id=project_ids[0],
            created_at=_ts(),
            updated_at=_ts(),
            source_agent="",
            category="risk",
            priority="high",
            summary=UNICODE_TEXT[:200],
            reference=None,
        ),
        CriticalNoteModel(
            id=_uuid(),
            project_id=project_ids[2],
            created_at=_ts(),
            updated_at=_ts(),
            source_agent="developer",
            category="decision",
            priority="medium",
            summary="",
            reference="",
        ),
    ]
    for cn in critical_notes:
        session.add(cn)
    session.commit()

    return {
        "projects": 3,
        "project_tags": 4,
        "project_shortnames": 3,
        "project_history": 3,
        "project_metadata_records": 3,
        "critical_notes": 3,
    }


def _populate_instances(session: Session, project_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate instances with all status types and parent/child relationships."""
    pid = project_ids[0] if project_ids else None

    parent_id = _uuid()
    child_ids = [_uuid() for _ in range(3)]

    instances_data = [
        # Parent instance
        Instance(
            instance_id=parent_id,
            project_id=pid,
            agent_id="leader",
            agent_dir="./agents/leader",
            agent_name="Leader Agent",
            parent_id=None,
            status="running",
            instance_metadata={
                "title": "Parent orchestrator",
                "config": {"model": "gpt-4o", "temperature": 0.7},
            },
            version=3,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        # Children with different statuses
        Instance(
            instance_id=child_ids[0],
            project_id=pid,
            agent_id="developer",
            agent_dir="./agents/developer",
            agent_name="Developer",
            parent_id=parent_id,
            status="idle",
            instance_metadata={"title": "Code helper"},
            version=1,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        Instance(
            instance_id=child_ids[1],
            project_id=pid,
            agent_id="developer",
            agent_dir="./agents/developer",
            agent_name=None,
            parent_id=parent_id,
            status="paused",
            instance_metadata=DEEPLY_NESTED_JSON,
            version=1,
            last_activity_at=None,
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=_ts(),
        ),
        # Instance covering remaining statuses
        Instance(
            instance_id=child_ids[2],
            project_id=project_ids[1] if project_ids and len(project_ids) > 1 else pid,
            agent_id="oracle",
            agent_dir="./agents/oracle",
            agent_name="Oracle",
            parent_id=None,
            status="completed",
            instance_metadata={},
            version=1,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        # Instance with error status and unicode metadata
        Instance(
            instance_id=_uuid(),
            project_id=None,
            agent_id="developer",
            agent_dir="./agents/developer",
            agent_name=UNICODE_TEXT[:50],
            parent_id=None,
            status="error",
            instance_metadata={"error": UNICODE_TEXT, "traceback": LONG_TEXT},
            version=1,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        # Instance with terminated / queued / waiting_children / failed statuses
        Instance(
            instance_id=_uuid(),
            project_id=None,
            agent_id="leader",
            agent_dir="./agents/leader",
            agent_name=None,
            parent_id=None,
            status="terminated",
            instance_metadata={},
            version=1,
            last_activity_at=None,
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        Instance(
            instance_id=_uuid(),
            project_id=None,
            agent_id="leader",
            agent_dir="./agents/leader",
            agent_name=None,
            parent_id=None,
            status="queued",
            instance_metadata={},
            version=1,
            last_activity_at=None,
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        Instance(
            instance_id=_uuid(),
            project_id=None,
            agent_id="leader",
            agent_dir="./agents/leader",
            agent_name=None,
            parent_id=parent_id,
            status="waiting_children",
            instance_metadata={},
            version=2,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
        Instance(
            instance_id=_uuid(),
            project_id=None,
            agent_id="developer",
            agent_dir="./agents/developer",
            agent_name=None,
            parent_id=None,
            status="failed",
            instance_metadata={"error": "Task-level failure"},
            version=1,
            last_activity_at=_dt(),
            created_at=_ts(),
            updated_at=_ts(),
            paused_at=None,
        ),
    ]
    for inst in instances_data:
        session.add(inst)
    session.commit()

    # --- instance_hierarchy ---
    hierarchy_data = [
        InstanceHierarchy(
            parent_id=parent_id,
            child_id=child_ids[0],
            created_at=_ts(),
        ),
        InstanceHierarchy(
            parent_id=parent_id,
            child_id=child_ids[1],
            created_at=_ts(),
        ),
        InstanceHierarchy(
            parent_id=parent_id,
            child_id=child_ids[2],
            created_at=_ts(),
        ),
    ]
    for h in hierarchy_data:
        session.add(h)
    session.commit()

    return {
        "instances": len(instances_data),
        "instance_hierarchy": len(hierarchy_data),
    }


def _populate_sources(session: Session, config: dict) -> dict[str, int]:
    """Populate source_configs, instance_mappings, processed_messages, schedule_executions."""
    # Cover all 4 SourceStatus values: stopped, starting, running, error
    source_ids = [_uuid() for _ in range(4)]

    # --- source_configs ---
    sources = [
        SourceConfig(
            source_id=source_ids[0],
            source_type="telegram",
            name="telegram-bot-main",
            config={"bot_token": "test-token", "allowed_users": [123, 456]},
            credentials=None,
            enabled=True,
            status="running",
            error_message=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        SourceConfig(
            source_id=source_ids[1],
            source_type="scheduler",
            name="cron-daily",
            config=DEEPLY_NESTED_JSON,
            credentials="encrypted:abc123",
            enabled=True,
            status="starting",
            error_message=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        SourceConfig(
            source_id=source_ids[2],
            source_type="webhook",
            name="webhook-disabled",
            config={},
            credentials=None,
            enabled=False,
            status="error",
            error_message=UNICODE_TEXT,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        SourceConfig(
            source_id=source_ids[3],
            source_type="slack",
            name="slack-bot-stopped",
            config={"channel": "#general", "watch_events": ["message"]},
            credentials=None,
            enabled=True,
            status="stopped",
            error_message=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
    ]
    for s in sources:
        session.add(s)
    session.commit()

    # --- instance_mappings ---
    instance_ids = [_uuid() for _ in range(2)]
    mappings = [
        InstanceMapping(
            mapping_id=_uuid(),
            source_id=source_ids[0],
            external_user_id="tg-user-12345",
            agent_instance_id=instance_ids[0],
            agent_id="developer",
            agent_dir="./agents/developer",
            mapping_metadata={"chat_id": 999, "language": "en"},
            last_message_at=_ts(),
            created_at=_ts(),
        ),
        InstanceMapping(
            mapping_id=_uuid(),
            source_id=source_ids[0],
            external_user_id="tg-user-日本語",
            agent_instance_id=instance_ids[1],
            agent_id="leader",
            agent_dir="./agents/leader",
            mapping_metadata={},
            last_message_at=None,
            created_at=_ts(),
        ),
    ]
    for m in mappings:
        session.add(m)
    session.commit()

    # --- processed_external_messages ---
    processed = [
        ProcessedMessage(
            source_id=source_ids[0],
            external_message_id="msg-001",
            processed_at=_ts(),
        ),
        ProcessedMessage(
            source_id=source_ids[0],
            external_message_id="msg-002",
            processed_at=_ts(),
        ),
        ProcessedMessage(
            source_id=source_ids[1],
            external_message_id="msg-unicode-日本語",
            processed_at=_ts(),
        ),
    ]
    for p in processed:
        session.add(p)
    session.commit()

    # --- schedule_executions ---
    executions = [
        ScheduleExecution(
            execution_id=_uuid(),
            schedule_id=source_ids[1],
            triggered_at=_ts(),
            instance_id=instance_ids[0],
            status="completed",
            error_message=None,
            completed_at=_ts(),
        ),
        ScheduleExecution(
            execution_id=_uuid(),
            schedule_id=source_ids[1],
            triggered_at=_ts(),
            instance_id=None,
            status="triggered",
            error_message=None,
            completed_at=None,
        ),
        ScheduleExecution(
            execution_id=_uuid(),
            schedule_id=source_ids[1],
            triggered_at=_ts(),
            instance_id=instance_ids[1],
            status="failed",
            error_message=LONG_TEXT,
            completed_at=_ts(),
        ),
    ]
    for e in executions:
        session.add(e)
    session.commit()

    return {
        "source_configs": len(sources),
        "instance_mappings": len(mappings),
        "processed_external_messages": len(processed),
        "schedule_executions": len(executions),
    }


def _populate_message_queue(session: Session, instance_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate message_queue with all message types and statuses."""
    iid = instance_ids[0] if instance_ids else _uuid()

    # Cover all 6 MessageStatus values: pending, ready, processing, retrying, completed, failed
    messages = [
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content="Hello, world!",
            type="human",
            source="api",
            root_source="api",
            status="ready",
            priority=1,
            retry_count=0,
            max_retries=5,
            error_message=None,
            last_error=None,
            message_metadata={"user": "test-user"},
            enqueued_at=_dt(),
            processing_started_at=None,
            last_activity_at=None,
            completed_at=None,
            next_retry_at=None,
            processing_task_id=None,
            images=None,
        ),
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content=UNICODE_TEXT,
            type="agent",
            source=None,
            root_source=None,
            status="processing",
            priority=5,
            retry_count=0,
            max_retries=3,
            error_message=None,
            last_error=None,
            message_metadata=DEEPLY_NESTED_JSON,
            enqueued_at=_dt(),
            processing_started_at=_dt(),
            last_activity_at=_dt(),
            completed_at=None,
            next_retry_at=None,
            processing_task_id=_uuid(),
            images=["data:image/png;base64,iVBOR..."],
        ),
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content="Completed message",
            type="agent",
            source="api",
            root_source="api",
            status="completed",
            priority=3,
            retry_count=0,
            max_retries=5,
            error_message=None,
            last_error=None,
            message_metadata={"result": "success"},
            enqueued_at=_dt(),
            processing_started_at=_dt(),
            last_activity_at=_dt(),
            completed_at=_dt(),
            next_retry_at=None,
            processing_task_id=_uuid(),
            images=None,
        ),
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content=LONG_TEXT,
            type="system",
            source="scheduler",
            root_source="scheduler",
            status="failed",
            priority=10,
            retry_count=3,
            max_retries=3,
            error_message="Max retries exceeded",
            last_error="Connection timeout",
            message_metadata={},
            enqueued_at=_dt(),
            processing_started_at=_dt(),
            last_activity_at=_dt(),
            completed_at=_dt(),
            next_retry_at=None,
            processing_task_id=None,
            images=None,
        ),
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content="",
            type="completion_report",
            source=None,
            root_source=None,
            status="pending",
            priority=1,
            retry_count=0,
            max_retries=5,
            error_message=None,
            last_error=None,
            message_metadata=None,
            enqueued_at=_dt(),
            processing_started_at=None,
            last_activity_at=None,
            completed_at=None,
            next_retry_at=None,
            processing_task_id=None,
            images=None,
        ),
        MessageQueue(
            message_id=_uuid(),
            instance_id=iid,
            content="Error report content",
            type="error_report",
            source="internal",
            root_source="telegram",
            status="retrying",
            priority=3,
            retry_count=1,
            max_retries=5,
            error_message=None,
            last_error="Temporary failure",
            message_metadata={"attempt": 2},
            enqueued_at=_dt(),
            processing_started_at=_dt(),
            last_activity_at=_dt(),
            completed_at=None,
            next_retry_at=_dt(),
            processing_task_id=_uuid(),
            images=["data:image/png;base64,abc123", "data:image/jpeg;base64,def456"],
        ),
    ]
    for m in messages:
        session.add(m)
    session.commit()

    return {"message_queue": len(messages)}


def _populate_job_queues(session: Session, project_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate job_queues, job_queue_items, job_locks, dead_letter_items."""
    pid = project_ids[0] if project_ids else _uuid()

    # --- job_queues ---
    queue_ids = [_uuid() for _ in range(3)]
    queues = [
        JobQueue(
            queue_id=queue_ids[0],
            project_id=pid,
            queue_name="default",
            queue_name_lower="default",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
            is_paused=False,
            description="Default FIFO queue",
            default_max_retries=5,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        JobQueue(
            queue_id=queue_ids[1],
            project_id=pid,
            queue_name="parallel-tasks",
            queue_name_lower="parallel-tasks",
            queue_type="parallel",
            concurrency_limit=5,
            is_system=False,
            is_paused=False,
            description=None,
            default_max_retries=None,
            created_at=_ts(),
            updated_at=_ts(),
        ),
        JobQueue(
            queue_id=queue_ids[2],
            project_id=pid,
            queue_name="defer-queue",
            queue_name_lower="defer-queue",
            queue_type="defer",
            concurrency_limit=1,
            is_system=False,
            is_paused=True,
            description="Defer queue (paused)",
            default_max_retries=3,
            created_at=_ts(),
            updated_at=_ts(),
        ),
    ]
    for q in queues:
        session.add(q)
    session.commit()

    # --- job_queue_items ---
    # Cover all 6 JobStatus values: pending, processing, completed, failed, cancelled, dead_letter
    job_ids = [_uuid() for _ in range(8)]
    instance_id = _uuid()
    jobs = [
        JobItem(
            job_id=job_ids[0],
            agent_id="developer",
            agent_dir="./agents/developer",
            message="Fix the login bug",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=5,

            admission_state=status_to_admission("pending"),
            created_at=_ts(),
            instance_id=None,
            job_metadata={"request_id": "req-001"},
            deleted_at=None,
            job_type="task",
            retry_count=0,
            max_retries=None,
            idempotency_key=None,
            failed_at=None,
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[1],
            agent_id="developer",
            agent_dir="./agents/developer",
            message=UNICODE_TEXT,
            source="telegram",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=8,

            admission_state=status_to_admission("processing"),
            created_at=_ts(),
            instance_id=instance_id,
            job_metadata=DEEPLY_NESTED_JSON,
            deleted_at=None,
            job_type="message",
            retry_count=0,
            max_retries=5,
            idempotency_key="idem-001",
            failed_at=None,
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[2],
            agent_id="oracle",
            agent_dir="./agents/oracle",
            message="Review complete",
            source="scheduler",
            project_id=pid,
            queue_id=queue_ids[1],
            priority=3,

            admission_state=status_to_admission("completed"),
            created_at=_ts(),
            instance_id=instance_id,
            job_metadata={},
            deleted_at=None,
            job_type="task",
            retry_count=0,
            max_retries=None,
            idempotency_key=None,
            failed_at=None,
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[3],
            agent_id="leader",
            agent_dir="./agents/leader",
            message="Failed task",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=1,

            admission_state=status_to_admission("failed"),
            created_at=_ts(),
            instance_id=instance_id,
            job_metadata={},
            deleted_at=None,
            job_type="task",
            retry_count=3,
            max_retries=3,
            idempotency_key=None,
            failed_at=_ts(),
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[4],
            agent_id="developer",
            agent_dir="./agents/developer",
            message="Cancelled by user",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=5,

            admission_state=status_to_admission("cancelled"),
            created_at=_ts(),
            instance_id=None,
            job_metadata={},
            deleted_at=None,
            job_type="task",
            retry_count=0,
            max_retries=None,
            idempotency_key=None,
            failed_at=None,
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[5],
            agent_id="developer",
            agent_dir="./agents/developer",
            message="Soft-deleted job",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=2,

            admission_state=status_to_admission("completed"),
            created_at=_ts(),
            instance_id=instance_id,
            job_metadata={},
            deleted_at=_ts(),
            job_type="message",
            retry_count=0,
            max_retries=None,
            idempotency_key=None,
            failed_at=None,
            next_retry_at=None,
        ),
        JobItem(
            job_id=job_ids[6],
            agent_id="developer",
            agent_dir="./agents/developer",
            message="Retrying task",
            source="webhook",
            project_id=pid,
            queue_id=queue_ids[1],
            priority=7,

            admission_state=status_to_admission("pending"),
            created_at=_ts(),
            instance_id=None,
            job_metadata={"attempt": 2},
            deleted_at=None,
            job_type="task",
            retry_count=1,
            max_retries=5,
            idempotency_key="idem-retry",
            failed_at=None,
            next_retry_at=_ts(),
        ),
        JobItem(
            job_id=job_ids[7],
            agent_id="leader",
            agent_dir="./agents/leader",
            message="Job moved to dead letter queue",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=1,

            admission_state=status_to_admission("dead_letter"),
            created_at=_ts(),
            instance_id=instance_id,
            job_metadata={"attempts": 5, "last_failure": "network"},
            deleted_at=None,
            job_type="task",
            retry_count=5,
            max_retries=5,
            idempotency_key=None,
            failed_at=_ts(),
            next_retry_at=None,
        ),
    ]
    for j in jobs:
        session.add(j)
    session.commit()

    # --- job_locks ---
    locks = [
        JobLock(
            lock_id=_uuid(),
            project_id=pid,
            queue_id=queue_ids[0],
            job_id=job_ids[1],
            instance_id=instance_id,
            acquired_at=_ts(),
        ),
    ]
    for l in locks:
        session.add(l)
    session.commit()

    # --- dead_letter_items ---
    dlq_items = [
        DeadLetterItem(
            dlq_id=_uuid(),
            job_id=job_ids[3],
            agent_id="leader",
            agent_dir="./agents/leader",
            message="Failed task",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=1,
            error_message="Exceeded max retries: " + UNICODE_TEXT,
            retry_count=3,
            failed_at=_ts(),
            moved_to_dlq_at=_ts(),
            reason="MAX_RETRIES",
            metadata_json={"original_job_id": job_ids[3], "attempts": 3},
        ),
        DeadLetterItem(
            dlq_id=_uuid(),
            job_id=job_ids[7],
            agent_id="leader",
            agent_dir="./agents/leader",
            message="Job moved to dead letter queue",
            source="api",
            project_id=pid,
            queue_id=queue_ids[0],
            priority=1,
            error_message="Circuit breaker tripped",
            retry_count=5,
            failed_at=_ts(),
            moved_to_dlq_at=_ts(),
            reason="CIRCUIT_BREAKER",
            metadata_json={"original_job_id": job_ids[7], "attempts": 5},
        ),
    ]
    for d in dlq_items:
        session.add(d)
    session.commit()

    return {
        "job_queues": len(queues),
        "job_queue_items": len(jobs),
        "job_locks": len(locks),
        "dead_letter_items": len(dlq_items),
    }


def _populate_job_watchers(session: Session, instance_ids: list[str] | None, job_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate job_watchers."""
    iid = instance_ids[0] if instance_ids else _uuid()
    jid = job_ids[0] if job_ids else _uuid()

    watchers = [
        JobWatcher(
            watch_id=_uuid(),
            job_id=jid,
            instance_id=iid,
            watch_events=["completed", "failed", "cancelled", "dead_letter"],
            created_at=_dt(),
        ),
        JobWatcher(
            watch_id=_uuid(),
            job_id=jid,
            instance_id=instance_ids[1] if instance_ids and len(instance_ids) > 1 else _uuid(),
            watch_events=["completed"],
            created_at=_dt(),
        ),
    ]
    for w in watchers:
        session.add(w)
    session.commit()

    return {"job_watchers": len(watchers)}


def _populate_events(session: Session, instance_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate event table with all event kinds."""
    iid = instance_ids[0] if instance_ids else _uuid()

    event_kinds = [
        "message_received",
        "processing_started",
        "processing_completed",
        "processing_failed",
        "child_completed",
        "child_failed",
        "instance_completed",
        "instance_lifecycle",
        "error",
        "message_completed",
    ]

    events = []
    for i, kind in enumerate(event_kinds):
        events.append(Event(
            id=None,  # autoincrement
            instance_id=iid if i % 3 != 0 else _uuid(),
            message_id=_uuid() if i % 2 == 0 else None,
            kind=kind,
            data=json.dumps({"event_index": i, "kind": kind}) if i % 2 == 0 else None,
            created_at=_dt(),
        ))
    # Add event with edge-case data
    events.append(Event(
        id=None,
        instance_id=iid,
        message_id=None,
        kind="error",
        data=UNICODE_TEXT,
        created_at=_dt(),
    ))
    events.append(Event(
        id=None,
        instance_id=iid,
        message_id=None,
        kind="message_received",
        data=LONG_TEXT,
        created_at=_dt(),
    ))

    for e in events:
        session.add(e)
    session.commit()

    return {"event": len(events)}


def _populate_mcp_servers(session: Session, config: dict) -> dict[str, int]:
    """Populate mcp_servers with configuration JSON."""
    servers = [
        McpServer(
            id=_uuid(),
            name="filesystem-server",
            description="File system access MCP server",
            config={"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
            is_active=True,
            is_builtin=True,
            config_schema=[
                {"name": "root_path", "type": "string", "required": True},
            ],
            config_schema_version="1",
            created_at=_ts(),
            updated_at=_ts(),
        ),
        McpServer(
            id=_uuid(),
            name="knowledge-base",
            description=UNICODE_TEXT,
            config=DEEPLY_NESTED_JSON,
            is_active=True,
            is_builtin=False,
            config_schema=None,
            config_schema_version="0",
            created_at=_ts(),
            updated_at=None,
        ),
        McpServer(
            id=_uuid(),
            name="inactive-server",
            description=None,
            config={},
            is_active=False,
            is_builtin=False,
            config_schema=[],
            config_schema_version="0",
            created_at=_ts(),
            updated_at=None,
        ),
    ]
    for s in servers:
        session.add(s)
    session.commit()

    return {"mcp_servers": len(servers)}


def _populate_tasks(session: Session, instance_ids: list[str] | None, config: dict) -> dict[str, int]:
    """Populate task table with various task types and statuses."""
    iid = instance_ids[0] if instance_ids else _uuid()

    tasks = [
        Task(
            id=None,
            task_type="process_message",
            instance_id=iid,
            message_id=_uuid(),
            status="pending",
            worker_id=None,
            retry_count=0,
            next_retry_at=None,
            cancel_requested=False,
            cancel_requested_at=None,
            retry_scheduled=False,
            result=None,
            error=None,
            created_at=_dt(),
            started_at=None,
            completed_at=None,
        ),
        Task(
            id=None,
            task_type="process_message",
            instance_id=iid,
            message_id=_uuid(),
            status="running",
            worker_id="worker-1",
            retry_count=0,
            next_retry_at=None,
            cancel_requested=False,
            cancel_requested_at=None,
            retry_scheduled=False,
            result=None,
            error=None,
            created_at=_dt(),
            started_at=_dt(),
            completed_at=None,
        ),
        Task(
            id=None,
            task_type="send_report",
            instance_id=iid,
            message_id=None,
            status="completed",
            worker_id="worker-2",
            retry_count=0,
            next_retry_at=None,
            cancel_requested=False,
            cancel_requested_at=None,
            retry_scheduled=False,
            result=json.dumps({"success": True, "data": UNICODE_TEXT}),
            error=None,
            created_at=_dt(),
            started_at=_dt(),
            completed_at=_dt(),
        ),
        Task(
            id=None,
            task_type="cleanup",
            instance_id=iid,
            message_id=None,
            status="failed",
            worker_id="worker-3",
            retry_count=2,
            next_retry_at=_ts(),
            cancel_requested=False,
            cancel_requested_at=None,
            retry_scheduled=True,
            result=None,
            error=LONG_TEXT,
            created_at=_dt(),
            started_at=_dt(),
            completed_at=_dt(),
        ),
        Task(
            id=None,
            task_type="process_message",
            instance_id=iid,
            message_id=_uuid(),
            status="cancelled",
            worker_id=None,
            retry_count=0,
            next_retry_at=None,
            cancel_requested=True,
            cancel_requested_at=_ts(),
            retry_scheduled=False,
            result=None,
            error=None,
            created_at=_dt(),
            started_at=None,
            completed_at=None,
        ),
    ]
    for t in tasks:
        session.add(t)
    session.commit()

    return {"task": len(tasks)}


def _populate_schema_migrations(session: Session, config: dict) -> dict[str, int]:
    """Populate schema_migrations with version tracking."""
    migrations = [
        SchemaMigration(
            version="20250101_000001",
            name="initial_schema",
            applied_at=_ts(),
            execution_time_ms=150,
            checksum="sha256:" + "a" * 64,
        ),
        SchemaMigration(
            version="20250115_120000",
            name="add_job_queue_paused",
            applied_at=_ts(),
            execution_time_ms=42,
            checksum="sha256:" + "b" * 64,
        ),
        SchemaMigration(
            version="20250201_080000",
            name="add_creator_agent_id",
            applied_at=_ts(),
            execution_time_ms=None,
            checksum=None,
        ),
    ]
    for m in migrations:
        session.add(m)
    session.commit()

    return {"schema_migrations": len(migrations)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Canonical list of all 22 tables
ALL_TABLES: list[str] = [
    "projects",
    "project_tags",
    "project_shortnames",
    "project_history",
    "project_metadata_records",
    "critical_notes",
    "instances",
    "instance_hierarchy",
    "message_queue",
    "source_configs",
    "instance_mappings",
    "processed_external_messages",
    "schedule_executions",
    "job_queues",
    "job_queue_items",
    "job_locks",
    "dead_letter_items",
    "job_watchers",
    "event",
    "mcp_servers",
    "task",
    "schema_migrations",
]


def populate_sqlite_test_data(db_path: str, config: dict | None = None) -> dict[str, int]:
    """Create a SQLite database with deterministic test data for all 22 tables.

    Args:
        db_path: Path where the SQLite database will be created.
        config: Optional configuration dict. Reserved for future use
            (e.g. controlling data volume, which edge cases to include).

    Returns:
        Dict mapping table_name → row_count for verification.
    """
    config = config or {}
    _reset_ts()
    _reset_uuids()

    # Remove existing file to start clean
    path = Path(db_path)
    if path.exists():
        path.unlink()

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    # Create all tables via SQLModel metadata
    SQLModel.metadata.create_all(engine)

    counts: dict[str, int] = {}

    with Session(engine) as session:
        # 1. Projects (parent of many FK chains)
        project_counts = _populate_projects(session, config)
        counts.update(project_counts)
        project_ids = list(
            session.exec(__import__("sqlmodel").select(Project.project_id)).all()
        )

        # 2. Instances (references projects)
        instance_counts = _populate_instances(session, project_ids, config)
        counts.update(instance_counts)
        instance_ids = list(
            session.exec(__import__("sqlmodel").select(Instance.instance_id)).all()
        )

        # 3. Sources (independent)
        source_counts = _populate_sources(session, config)
        counts.update(source_counts)

        # 4. Message queue (references instances)
        mq_counts = _populate_message_queue(session, instance_ids, config)
        counts.update(mq_counts)

        # 5. Job queues (references projects)
        jq_counts = _populate_job_queues(session, project_ids, config)
        counts.update(jq_counts)
        job_ids = list(
            session.exec(__import__("sqlmodel").select(JobItem.job_id)).all()
        )

        # 6. Job watchers (references instances + job_queue_items)
        jw_counts = _populate_job_watchers(session, instance_ids, job_ids, config)
        counts.update(jw_counts)

        # 7. Events (references instances)
        ev_counts = _populate_events(session, instance_ids, config)
        counts.update(ev_counts)

        # 8. MCP servers (independent)
        mcp_counts = _populate_mcp_servers(session, config)
        counts.update(mcp_counts)

        # 9. Tasks (references instances)
        task_counts = _populate_tasks(session, instance_ids, config)
        counts.update(task_counts)

        # 10. Schema migrations (independent)
        sm_counts = _populate_schema_migrations(session, config)
        counts.update(sm_counts)

    engine.dispose()

    logger.info(f"Populated {len(counts)} tables in {db_path}")
    for table, count in sorted(counts.items()):
        logger.info(f"  {table}: {count} rows")

    return counts


def generate_verification_hash(db_path: str) -> dict[str, dict[str, Any]]:
    """Compute row count and SHA-256 checksum for each table in the database.

    Opens the SQLite database directly via ``sqlite3`` (bypassing the ORM)
    so the checksum reflects the raw bytes on disk — this catches any
    ORM-layer serialization differences that might slip through.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Dict mapping table_name → {"count": N, "checksum": "sha256_hex"}.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Get all user tables
        tables = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
        tables = [t[0] for t in tables]

        result: dict[str, dict[str, Any]] = {}
        for table in tables:
            # Row count
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

            # Checksum: hash all row data as JSON
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
            columns = [
                desc[0]
                for desc in conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description
            ]

            # Serialize deterministically: sort keys, ensure consistent JSON
            row_dicts = []
            for row in rows:
                row_dict = {}
                for col, val in zip(columns, row):
                    # Convert bytes to hex string for JSON serialization
                    if isinstance(val, bytes):
                        val = val.hex()
                    row_dict[col] = val
                row_dicts.append(row_dict)

            # Sort rows for deterministic ordering
            data_json = json.dumps(row_dicts, sort_keys=True, default=str)
            checksum = hashlib.sha256(data_json.encode("utf-8")).hexdigest()

            result[table] = {"count": count, "checksum": checksum}

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    target = sys.argv[1] if len(sys.argv) > 1 else "/tmp/test_migration.db"

    print(f"Creating test database at {target} ...")
    counts = populate_sqlite_test_data(target)
    print(f"\nPopulated {len(counts)} tables:")
    for table, count in sorted(counts.items()):
        print(f"  {table:35s} {count:3d} rows")

    print(f"\nGenerating verification hashes ...")
    hashes = generate_verification_hash(target)
    print(f"Verification hash for {len(hashes)} tables:")
    for table, info in sorted(hashes.items()):
        print(f"  {table:35s} count={info['count']:3d}  sha256={info['checksum'][:16]}...")

    print(f"\nDatabase size: {Path(target).stat().st_size:,} bytes")
    print("Done.")
