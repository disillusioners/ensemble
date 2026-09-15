"""Project-related database models (tables).

This module contains the SQLModel table definitions for the Project entity
and its related junction tables.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, UniqueConstraint
from sqlmodel import SQLModel, Field
from pydantic import BaseModel, field_validator

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Single source of truth for ``created_at`` / ``updated_at`` /
    ``minted_at`` defaults across the project-tree models so the
    format stays consistent (tz-aware, microsecond-resolution,
    ``+00:00`` UTC offset). Mirrors the helper in
    :mod:`daemon.repositories.skill.models`.
    """
    return datetime.now(timezone.utc).isoformat()


CRITICAL_NOTES_MAX_ENTRIES = 30


class CriticalNotesCategory(str, enum.Enum):
    CONVENTION = "convention"
    PATTERN = "pattern"
    RISK = "risk"
    DECISION = "decision"
    CONSTRAINT = "constraint"

class CriticalNotesPriority(str, enum.Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

class CriticalNotes(BaseModel):
    """A single critical notes entry for a project."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_agent: str = ""
    category: str
    priority: str
    summary: str
    reference: str | None = None
    # ── Lifecycle columns (critical-notes-retrieval Phase 1, 2026-09-15) ──
    # This BaseModel TRACKS the storage shape on CriticalNoteModel below
    # (the round-trip ``CriticalNotes(**note.to_dict())`` idiom in the
    # tool layer), but is NOT a byte-for-byte mirror: ``last_reviewed_at``
    # is None-defaulted here to admit legacy NULLs (storage declares it
    # non-optional on fresh lineages; the legacy column is nullable on
    # pre-migration DBs). See the model-side comment for the broader
    # storage-vs-wire drift and the NULL→created_at read fallback.
    # ``superseded_by_id`` is a SOFT self-reference (plain str, no DB FK —
    # integrity is enforced leader-only at the tool layer; a hard FK would
    # give dialect-divergent cascade behavior between PG and SQLite).
    pinned: bool = False
    pinned_at: str | None = None
    pinned_by: str | None = None
    superseded_by_id: str | None = None
    last_reviewed_at: str | None = None
    detail_ref: str | None = None

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        valid = [e.value for e in CriticalNotesCategory]
        if v not in valid:
            raise ValueError(f"Invalid category '{v}', must be one of {valid}")
        return v

    @field_validator('priority')
    @classmethod
    def validate_priority(cls, v):
        valid = [e.value for e in CriticalNotesPriority]
        if v not in valid:
            raise ValueError(f"Invalid priority '{v}', must be one of {valid}")
        return v

    @field_validator('summary')
    @classmethod
    def validate_summary(cls, v):
        if len(v) > 200:
            raise ValueError(f"Summary must be ≤200 chars, got {len(v)}")
        return v

    def to_dict(self) -> dict:
        return self.model_dump()


class ProjectStatus(str, enum.Enum):
    """Project status enum."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    
    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls._value2member_map_


class ProjectType(str, enum.Enum):
    """Project type enum."""
    SOFTWARE = "software"
    DOCUMENTATION = "documentation"
    RESEARCH = "research"
    TASK = "task"
    GENERAL = "general"
    INFRASTRUCTURE = "infrastructure"  # Infrastructure-as-Code (Terraform, Ansible, Pulumi)
    GITOPS = "gitops"  # GitOps pipelines (ArgoCD, Flux, Git-based deployment)
    DEVOPS = "devops"  # CI/CD, deployment automation, pipeline tooling
    LIBRARY = "library"  # Reusable libraries/packages/SDKs
    DATA = "data"  # Data engineering, pipelines, analytics, ML
    MOBILE = "mobile"  # Mobile applications (iOS/Android)

    @classmethod
    def is_valid(cls, project_type: str) -> bool:
        return project_type in cls._value2member_map_


class HistoryEntryType(str, enum.Enum):
    MILESTONE = "milestone"
    COMMIT = "commit"
    PHASE = "phase"
    BUGFIX = "bugfix"
    DEPLOYMENT = "deployment"
    NOTE = "note"
    CONFIG_CHANGE = "config_change"
    FEATURE = "feature"
    OTHER = "other"


class ProjectTagLink(SQLModel, table=True):
    """Junction table for project-tag many-to-many relationship.

    Uniqueness on (project_id, tag) is enforced by the composite primary key
    below; this is the column set that ``add_tag`` uses for
    ``INSERT ... ON CONFLICT DO NOTHING``. SQLModel.metadata.create_all()
    installs the constraint on PostgreSQL; existing SQLite DBs rely on the
    composite PK being part of the table definition since v0.5.x.
    """
    __tablename__ = "project_tags"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    tag: str = Field(primary_key=True)


class ProjectShortnameLink(SQLModel, table=True):
    """Junction table for project-shortname many-to-many relationship.

    Same uniqueness note as :class:`ProjectTagLink`: the composite primary
    key on (project_id, shortname) is the column set used by
    ``add_shortname`` for ``INSERT ... ON CONFLICT DO NOTHING``.
    """
    __tablename__ = "project_shortnames"

    project_id: str = Field(foreign_key="projects.project_id", primary_key=True)
    shortname: str = Field(primary_key=True)


class CriticalNoteModel(SQLModel, table=True):
    """SQLModel table for critical notes entries."""
    __tablename__ = "critical_notes"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_agent: str = Field(default="")
    category: str = Field(default="")
    priority: str = Field(default="")
    summary: str = Field(default="")
    reference: str | None = Field(default=None)
    # ── Lifecycle columns (critical-notes-retrieval Phase 1, 2026-09-15) ──
    # Additive columns from architecture-recommendation §5.1. DDL lineage:
    #   * fresh DBs (both drivers) get them from create_all() right here;
    #   * existing SQLite DBs get them from the ordered migration
    #     ``daemon/migrations/versions/20260915_120000_critical_notes_lifecycle.sql``
    #     (runner is SQLite-only);
    #   * existing PG DBs get them from the idempotent
    #     ``_ensure_postgres_columns`` mirror in ``daemon/manager.py``
    #     (partial indexes are NOT expressible via create_all — they are
    #     mirrored there with ``CREATE INDEX IF NOT EXISTS ... WHERE ...``).
    #
    # ``superseded_by_id`` is a SOFT self-reference (plain TEXT, no DB-level
    # FK): a hard FK would give dialect-divergent ON DELETE behavior (PG
    # enforces, SQLite defaults OFF) and would make the cascade question a
    # database-internal side effect. Integrity is enforced leader-only in
    # the tool layer (``daemon/tools/critical_notes.py``) — the only write
    # surface. Removal semantics (incl. what happens to rows pointing at a
    # removed id) are documented EXACTLY at the ``project_cn_remove`` site.
    #
    # ``last_reviewed_at`` is backfilled ``= created_at`` by both the
    # migration and the PG mirror (NULL-guarded UPDATE). It is declared
    # non-optional here so fresh lineages are NOT NULL; the migration-added
    # column is nullable on legacy lineages (SQLite cannot ADD COLUMN NOT
    # NULL without a constant default) — every write path populates it, and
    # read surfaces treat NULL as "fall back to created_at", so the drift
    # is behaviorally inert.
    pinned: bool = Field(default=False)
    pinned_at: str | None = Field(default=None)
    pinned_by: str | None = Field(default=None)
    superseded_by_id: str | None = Field(default=None)
    last_reviewed_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    detail_ref: str | None = Field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_agent": self.source_agent,
            "category": self.category,
            "priority": self.priority,
            "summary": self.summary,
            "reference": self.reference,
            "pinned": self.pinned,
            "pinned_at": self.pinned_at,
            "pinned_by": self.pinned_by,
            "superseded_by_id": self.superseded_by_id,
            "last_reviewed_at": self.last_reviewed_at,
            "detail_ref": self.detail_ref,
        }


class CriticalNoteEmbeddingModel(SQLModel, table=True):
    """Cached per-critical-note embedding (critical-notes-retrieval Phase 2).

    Holds ONE embedding row per note (the ``note_id`` FK is the primary
    key). Mirrors the side-table pattern used by
    :class:`daemon.repositories.skill.models.SkillEmbedding` — pure
    JSON float arrays, no numpy / bytes / pickle (the project
    standard for JSON-shaped columns).

    Phase-2 write-time embed (§4.2):
      * ``add_critical_note`` triggers a best-effort embed of
        ``summary + reference[:800]`` (NOT the unbounded ``detail_ref`` —
        list/router reads carry full detail).
      * Embedding failure NEVER blocks the write — the note lives in
        BM25-only state until a lazy-mint or explicit backfill covers
        it.
      * Once a row exists, repeated lazy mints are a no-op
        (``WHERE embedding IS NULL`` filter on the backfill).

    Storage wording is dialect-neutral (§4.2 [#6]): the JSON float array
    goes through the same cross-driver :class:`JSONBType` adapter that
    ``skill_embeddings`` uses, so PG / SQLite schemas stay byte-
    equivalent for the runtime path. New-table-only via
    ``SQLModel.metadata.create_all()`` — NO ordered ``.sql`` migration
    needed for additive Phase 2 surface area.

    Attributes:
        note_id: PK + FK-self to ``critical_notes.id``
            (``ON DELETE CASCADE`` so removing a note clears its
            cached embedding in the same transaction). Mirrors the
            ``skill_embeddings`` ``ON DELETE CASCADE`` shape.
        embedding: Vector as a list of floats (length depends on
            embedding model). Cross-driver JSON via
            :class:`~daemon.repositories.infra.types.JSONBType`.
        model: Embedding model name used to produce this vector
            (rationale: changing models invalidates every cached
            embedding; the audit column makes that re-mint
            observable).
        dims: Captured vector length — redundant with ``len(embedding)``
            but a separate column keeps the model provider's
            reported dims queryable without deserializing the
            array.
        minted_at: ISO-8601 timestamp. Set both at write-time embed
            and at lazy mint / explicit backfill so a stale row is
            easy to spot (``OLD minted_at + active migration`` =
            candidate for re-mint).
    """

    __tablename__ = "critical_note_embeddings"

    note_id: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("critical_notes.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    model: str = Field(sa_column=Column(String, nullable=False), max_length=128)
    dims: int = Field(sa_column=Column(Integer, nullable=False))
    minted_at: str = Field(default_factory=_now_iso)


class ProjectMetadataRecord(SQLModel, table=True):
    """Dedicated table for project metadata key-value pairs."""
    __tablename__ = "project_metadata_records"

    id: int | None = Field(default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True))
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    meta_key: str = Field(sa_column=Column(String, nullable=False))
    meta_value: Any = Field(sa_column=Column(JSONBType, nullable=True))
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        UniqueConstraint("project_id", "meta_key", name="uq_project_metadata_project_key"),
    )


class Project(SQLModel, table=True):
    """SQLModel Project table - internal ORM representation."""
    __tablename__ = "projects"

    project_id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    # F15: removed `unique=True` here. The UniqueConstraint in
    # ``__table_args__`` below is the authoritative source for the
    # UNIQUE(name) constraint. Keeping both would create two UNIQUE
    # constraints on the column (one declared inline on Field, one
    # declared via UniqueConstraint), which is wasteful and
    # confusing — and on some dialects generates two separate
    # index names, complicating IntegrityError inspection.
    name: str = Field(index=True)
    project_type: str = Field(default="general")
    status: str = Field(default=ProjectStatus.ACTIVE.value)

    main_directory: str | None = None

    related_directories: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONBType)
    )

    description: str | None = None

    job_queue_paused: bool = Field(default=False, description="Whether job queue is paused for this project")

    # Use 'project_metadata' to avoid conflict with SQLAlchemy's reserved 'metadata'
    project_metadata: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSONBType)
    )

    relationships: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column(JSONBType)
    )

    creator_instance_id: str | None = None
    creator_agent_id: str | None = None

    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    __table_args__ = (
        # Explicit UniqueConstraint on ``name`` for two reasons:
        # 1. SQLModel.metadata.create_all() will emit the constraint for
        #    PostgreSQL (the default v0.5.2+ dialect), closing the H14
        #    check-then-insert race that previously lost concurrent
        #    ``Project.create`` calls with the same name.
        # 2. Existing SQLite DBs need a backfill via migration
        #    20260619_000006_add_project_constraints.sql, since
        #    SQLModel does not retroactively add the constraint.
        UniqueConstraint("name", name="uq_projects_name"),
        CheckConstraint(
            "status IN ('active', 'paused', 'completed', 'archived')",
            name="ck_projects_status_valid",
        ),
    )
    
    # Runtime-only attributes (not stored in DB)
    _tags: list[str] = []
    _shortnames: list[str] = []
    
    @property
    def tags(self) -> list[str]:
        return getattr(self, '_tags', [])
    
    @tags.setter
    def tags(self, value: list[str]):
        self._tags = value
    
    @property
    def shortnames(self) -> list[str]:
        return getattr(self, '_shortnames', [])
    
    @shortnames.setter
    def shortnames(self, value: list[str]):
        self._shortnames = value
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "project_type": self.project_type,
            "status": self.status,
            "main_directory": self.main_directory,
            "related_directories": list(self.related_directories) if self.related_directories else [],
            "description": self.description,
            "job_queue_paused": self.job_queue_paused,
            "tags": list(self._tags),
            "shortnames": list(self._shortnames),
            "metadata": dict(self.project_metadata),
            "relationships": dict(self.relationships),
            "creator_instance_id": self.creator_instance_id,
            "creator_agent_id": self.creator_agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectHistoryEntry(SQLModel, table=True):
    """SQLModel ProjectHistoryEntry table - tracks project history entries."""
    __tablename__ = "project_history"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True)
    )
    entry_type: str = Field()
    summary: str = Field(max_length=300)
    details: str | None = Field(default=None, max_length=5000)
    source_agent: str | None = Field(default=None)
    source_instance_id: str | None = Field(default=None)
    entry_metadata: dict | None = Field(
        default=None,
        sa_column=Column(JSONBType)
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "entry_type": self.entry_type,
            "summary": self.summary,
            "details": self.details,
            "source_agent": self.source_agent,
            "source_instance_id": self.source_instance_id,
            "entry_metadata": self.entry_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
