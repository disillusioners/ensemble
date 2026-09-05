"""Instance-related database models (tables).

This module contains the SQLModel table definitions for the Instance entity
and its related junction tables.
"""

from __future__ import annotations

import enum
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, Integer, String, event, text
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType


class InstanceStatus(str, enum.Enum):
    """Instance status enum."""
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"  # Active but no in-flight work (e.g. awaiting next user input)
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    TERMINATED = "terminated"
    QUEUED = "queued"  # Idle but has queued messages
    WAITING_CHILDREN = "waiting_children"  # Parent waiting for child completion reports
    FAILED = "failed"  # Task-level failure (distinct from instance ERROR)

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class InstanceHierarchy(SQLModel, table=True):
    """Junction table for instance parent-child hierarchy."""
    __tablename__ = "instance_hierarchy"

    parent_id: str = Field(primary_key=True)
    child_id: str = Field(primary_key=True)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Instance(SQLModel, table=True):
    """SQLModel Instance table - internal ORM representation."""
    __tablename__ = "instances"

    instance_id: str = Field(primary_key=True)
    project_id: str | None = Field(default=None, sa_column=Column("project_id", String, nullable=True))
    agent_id: str = Field(index=True)
    agent_dir: str = Field(index=True)
    agent_name: str | None = Field(default=None, index=True)
    agent_tag: str | None = Field(
        default=None,
        sa_column=Column("agent_tag", String, nullable=True)
        # W9: No index — agent_tag filtering is rare
    )
    parent_id: str | None = Field(default=None, index=True)
    status: str = Field(default=InstanceStatus.IDLE.value, index=True)
    
    instance_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONBType)
    )

    # Optimistic locking version
    version: int = Field(default=1)
    # For watchdog timeout detection
    last_activity_at: datetime | None = Field(default=None)

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    paused_at: str | None = Field(default=None, index=True)

    # ─── Leader Completion Attestation (Phase 3) ──────────────────────────
    # Row-scoped counter — resets ONLY on the four triggers per leader
    # ruling 1: (1) attested allow, (2) terminal_after_bound finalization,
    # (3) revive-from-COMPLETED via a NEW top-level user/mission message,
    # (4) instance creation. The same single reset op also clears the
    # ``completion_gate_escalated`` flag below (leader ruling 2 — the
    # escalation flag shares the per-mission lifecycle).
    #
    # Schema mirrors ``daemon/migrations/versions/20260905_000001_
    # attestation_ledger_columns.sql`` (SQLite path) and the matching
    # ``ALTER TABLE`` block in
    # ``daemon/manager.py::_ensure_postgres_columns`` (PostgreSQL path).
    # Existing instance rows receive defaults via the migration's column
    # default (``0`` / ``False``); fresh DBs get them via
    # ``SQLModel.metadata.create_all()``.
    attestation_denied_count: int = Field(
        default=0,
        sa_column=Column(
            "attestation_denied_count",
            Integer,
            nullable=False,
            default=0,
            server_default=text("0"),
        ),
    )
    #: Terminal-after-bound escalation flag (leader ruling 2 — persists
    #: for postmortem until cleared by the same single reset op).
    completion_gate_escalated: bool = Field(
        default=False,
        sa_column=Column(
            "completion_gate_escalated",
            Boolean,
            nullable=False,
            default=False,
            server_default=text("false"),
        ),
    )

    @property
    def title(self) -> str | None:
        """Extract title from instance_metadata."""
        return self.instance_metadata.get("title") if self.instance_metadata else None

    @property
    def initiative_message(self) -> str | None:
        """Extract initiative message from instance_metadata."""
        return self.instance_metadata.get("initiative_message") if self.instance_metadata else None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "instance_id": self.instance_id,
            "project_id": self.project_id,
            "agent_id": self.agent_id,
            "agent_dir": self.agent_dir,
            "agent_name": self.agent_name,
            "agent_tag": self.agent_tag,
            "parent_id": self.parent_id,
            "status": self.status,
            "title": self.title,
            "initiative_message": self.initiative_message,
            "metadata": dict(self.instance_metadata) if self.instance_metadata else {},
            "version": self.version,
            "last_activity_at": self.last_activity_at.isoformat() if self.last_activity_at else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "paused_at": self.paused_at,
            # Leader Completion Attestation Phase 3 — surfaced for
            # operator visibility (the FE mission badge and dry-log
            # promotion adjudicator read these directly).
            "attestation_denied_count": self.attestation_denied_count,
            "completion_gate_escalated": self.completion_gate_escalated,
        }


# ─── updated_at consistency ─────────────────────────────────────────────────
# ``updated_at`` has no DB-level ``onupdate``, and several code paths set
# ``status`` (or other fields) via direct ORM writes WITHOUT bumping
# ``updated_at`` — notably the revive block in
# ``instance_messaging._prepare_enqueued_message`` (completed -> running on
# reuse), plus direct ORM status writes in ``child_reports`` and
# ``error_reporting``. This left ``updated_at`` stale relative to
# ``status``, which broke the frontend's "higher-updated_at-wins" merge:
# a reused instance showed stale ``completed`` until a full page reload
# (the API's stale ``updated_at`` tied with the FE's stale local value,
# so the merge preserved the stale terminal status).
#
# This ORM ``before_update`` listener makes ``updated_at`` reliable for
# EVERY ORM update so the frontend merge can trust it as the
# authoritative freshness signal. Core ``UPDATE`` statements in the
# instance repository already set ``updated_at`` explicitly and are
# unaffected — ORM events do not fire for Core updates, so there is no
# double-write or conflict. The listener is idempotent and simply
# reflects the semantic meaning of ``updated_at`` ("last update").
@event.listens_for(Instance, "before_update")
def _bump_instance_updated_at(mapper, connection, target) -> None:
    """Auto-stamp ``updated_at`` on every ORM UPDATE of an Instance row."""
    target.updated_at = datetime.now(timezone.utc).isoformat()
