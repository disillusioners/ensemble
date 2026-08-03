"""SQLModel table definitions for the Project Blueprint subsystem."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, ForeignKey, Index, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Blueprint(SQLModel, table=True):
    """A project-scoped blueprint document."""

    __tablename__ = "project_blueprints"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "slug",
            name="uq_project_blueprints_project_slug",
        ),
        UniqueConstraint(
            "project_id",
            "name",
            name="uq_project_blueprints_project_name",
        ),
        Index("ix_project_blueprints_project_id", "project_id"),
        Index("ix_project_blueprints_kind", "kind"),
        Index("ix_project_blueprints_status", "status"),
        Index(
            "ix_project_blueprints_project_kind_active",
            "project_id",
            "kind",
            "is_active",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    slug: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=256,
    )
    name: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=256,
    )
    kind: str = Field(default="area", max_length=16)
    content: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(default="published", max_length=32)
    tags: list[dict] = Field(
        default_factory=list,
        sa_column=Column("tags", JSONBType, nullable=False),
    )
    file_refs: list[str] = Field(
        default_factory=list,
        sa_column=Column("file_refs", JSONBType, nullable=False),
    )
    trigger_queries: list[str] = Field(
        default_factory=list,
        sa_column=Column("trigger_queries", JSONBType, nullable=False),
    )
    # C5 fix: Denormalized cache — the ``project_blueprint_triggers`` table
    # is the AUTHORITATIVE source for matching. This field mirrors the query
    # strings for UI display. On rollback, restore it from the authoritative
    # table state (the write service clears the trigger table on rollback).
    version: int = Field(default=1)
    embedding_model: Optional[str] = Field(default=None, max_length=128)
    source: str = Field(default="auto", max_length=16)
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    last_reviewed_at: Optional[str] = Field(default=None)
    is_active: bool = Field(default=True)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "slug": self.slug,
            "name": self.name,
            "kind": self.kind,
            "content": self.content,
            "status": self.status,
            "tags": self.tags,
            "file_refs": self.file_refs,
            "trigger_queries": list(self.trigger_queries) if self.trigger_queries else [],
            "version": self.version,
            "embedding_model": self.embedding_model,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_reviewed_at": self.last_reviewed_at,
            "is_active": self.is_active,
        }


class BlueprintTrigger(SQLModel, table=True):
    """A generated trigger query and its embedding for a blueprint."""

    __tablename__ = "project_blueprint_triggers"
    __table_args__ = (
        Index("ix_project_blueprint_triggers_blueprint_id", "blueprint_id"),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    blueprint_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("project_blueprints.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    query_text: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=512,
    )
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "blueprint_id": self.blueprint_id,
            "query_text": self.query_text,
            "embedding": self.embedding,
            "created_at": self.created_at,
        }


class BlueprintRevision(SQLModel, table=True):
    """An append-only content snapshot for a blueprint version."""

    __tablename__ = "project_blueprint_revisions"
    __table_args__ = (
        Index("ix_project_blueprint_revisions_blueprint_id", "blueprint_id"),
        Index(
            "ix_project_blueprint_revisions_blueprint_version",
            "blueprint_id",
            "version",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    blueprint_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("project_blueprints.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    version: int = Field(default=1)
    content_snapshot: str = Field(sa_column=Column(Text, nullable=False))
    source: str = Field(default="auto", max_length=16)
    file_refs: list[str] = Field(
        default_factory=list,
        sa_column=Column("file_refs", JSONBType, nullable=False),
    )
    tags: list[dict] = Field(
        default_factory=list,
        sa_column=Column("tags", JSONBType, nullable=False),
    )
    trigger_queries: list[str] = Field(
        default_factory=list,
        sa_column=Column("trigger_queries", JSONBType, nullable=False),
    )
    reason: Optional[str] = Field(default=None, max_length=512)
    created_at: str = Field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "blueprint_id": self.blueprint_id,
            "version": self.version,
            "content_snapshot": self.content_snapshot,
            "source": self.source,
            "file_refs": self.file_refs,
            "tags": self.tags,
            "trigger_queries": self.trigger_queries,
            "reason": self.reason,
            "created_at": self.created_at,
        }
