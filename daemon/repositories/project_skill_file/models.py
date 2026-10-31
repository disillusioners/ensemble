"""SQLModel tables for the project skill source loader (Phase 1 task 3).

Three tables, ONE package (the lane's repo home):

* :class:`ProjectSkillFile` — ``project_skill_files`` — derived index
  metadata ONLY (identity, digests, health). **NO body column** — P1
  is enforced structurally: there is no column a body could be written
  into by accident.
* :class:`ProjectSkillEmbedding` — ``project_skill_embeddings`` — the
  A1 DEDICATED vector table. ``skill_id`` carries the ``pf-<sha16>``
  pseudo-id with **NO ForeignKey** — that absence is A1's whole point:
  the legacy ``skill_embeddings.skill_id`` is
  ``ForeignKey("skills.id", ondelete="CASCADE")``
  (``daemon/repositories/skill/models.py``), which rejects every
  ``pf-`` insert on PG (the FK wall). The real-PG integration pack
  pins that wall RED as documentation.
* :class:`ProjectSkillUsageRecord` — ``project_skill_usage_records`` —
  the D2 companion table (the census chose the companion fork; see
  ``.agents/shared/planning/project_skill_source/../../d2-write-census.md``).
  Mirrors the ``skill_usage_records`` column set minus the FK so the
  Phase-3 ``skill_feedback`` acceptance can keep D-Q5's metrics value
  without ever touching an FK-constrained child table.

Conventions (house): every string column TEXT on both drivers (never
VARCHAR); ISO-8601 TEXT timestamps; JSON-shaped columns via
:class:`~daemon.repositories.infra.types.JSONBType`; the SQLite-side
canonical DDL lives in one ``.sql`` file per table under
``daemon/migrations/versions/`` (house rule: every new SQLModel table,
zero exceptions). The Phase-4 manager wiring owns the pre-``create_all``
model registration; until then fresh-PG test DBs register these models
by importing this module (tests) — the operative artifact per A10.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Boolean, Column, Index, Integer, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectSkillFile(SQLModel, table=True):
    """Derived index metadata for one file skill (P1-safe: no body column).

    A3: the unique key is ``(root_path, project_id, namespace, name)`` —
    two roots bound to one project stay distinct rows; the PK ``id`` is
    the per-host-per-root pseudo-id ``pf-<sha16>`` (A3). The human
    address ``pf:<ns>:<name>`` is UNCHANGED and disambiguated at resolve
    time by the caller's root.
    """

    __tablename__ = "project_skill_files"
    __table_args__ = (
        UniqueConstraint(
            "root_path",
            "project_id",
            "namespace",
            "name",
            name="uq_psf_root_project_ns_name",
        ),
        Index("idx_psf_project_id", "project_id"),
        Index("idx_psf_root_path", "root_path"),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    project_id: str = Field(sa_column=Column("project_id", Text, nullable=False))
    root_path: str = Field(sa_column=Column("root_path", Text, nullable=False))
    namespace: str = Field(sa_column=Column("namespace", Text, nullable=False))
    name: str = Field(sa_column=Column("name", Text, nullable=False))
    address: str = Field(sa_column=Column("address", Text, nullable=False))
    description: str = Field(
        sa_column=Column("description", Text, nullable=False, default="")
    )
    category: Optional[str] = Field(
        default=None, sa_column=Column("category", Text, nullable=True)
    )
    body_path: str = Field(sa_column=Column("body_path", Text, nullable=False))
    manifest_sha256: str = Field(
        sa_column=Column("manifest_sha256", Text, nullable=False)
    )
    body_sha256: str = Field(sa_column=Column("body_sha256", Text, nullable=False))
    body_char_len: int = Field(
        default=0, sa_column=Column("body_char_len", Integer, nullable=False)
    )
    # P1: manifest METADATA only (version/tiers/loads/assets/supersedes/
    # schema_version) — never body text.
    manifest_json: Optional[str] = Field(
        default=None, sa_column=Column("manifest_json", Text, nullable=True)
    )
    valid: bool = Field(
        default=True, sa_column=Column("valid", Boolean, nullable=False, default=True)
    )
    # Q7 per-skill failure reason (NULL = clean; A5 reasons live here too;
    # embedding-refresh failures also surface here per D-Q3).
    last_error: Optional[str] = Field(
        default=None, sa_column=Column("last_error", Text, nullable=True)
    )
    # Q3: last successful embedding refresh (NULL = embeddings pending).
    embedded_at: Optional[str] = Field(
        default=None, sa_column=Column("embedded_at", Text, nullable=True)
    )
    indexed_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("indexed_at", Text, nullable=False),
    )
    updated_at: Optional[str] = Field(
        default=None, sa_column=Column("updated_at", Text, nullable=True)
    )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view (metadata only — no body exists to leak)."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "root_path": self.root_path,
            "namespace": self.namespace,
            "name": self.name,
            "address": self.address,
            "description": self.description,
            "category": self.category,
            "body_path": self.body_path,
            "manifest_sha256": self.manifest_sha256,
            "body_sha256": self.body_sha256,
            "body_char_len": self.body_char_len,
            "manifest_json": self.manifest_json,
            "valid": self.valid,
            "last_error": self.last_error,
            "embedded_at": self.embedded_at,
            "indexed_at": self.indexed_at,
            "updated_at": self.updated_at,
        }


class ProjectSkillEmbedding(SQLModel, table=True):
    """A1 dedicated vector home — ``(trigger_query, embedding)`` rows.

    ``skill_id`` is the opaque ``pf-`` pseudo-id with **NO ForeignKey**
    (A1). Column set mirrors the legacy ``SkillEmbedding``
    (``daemon/repositories/skill/models.py``) minus the FK: an opaque
    hash cleanup is the A4 gc sweep's job, not a DB-level cascade.
    """

    __tablename__ = "project_skill_embeddings"
    __table_args__ = (Index("idx_pse_skill_id", "skill_id"),)

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    skill_id: str = Field(sa_column=Column("skill_id", Text, nullable=False))
    trigger_query: str = Field(
        sa_column=Column("trigger_query", Text, nullable=False)
    )
    embedding: list[float] = Field(
        default_factory=list,
        sa_column=Column("embedding", JSONBType, nullable=False),
    )
    created_at: str = Field(
        default_factory=_utc_now_iso, sa_column=Column("created_at", Text)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "trigger_query": self.trigger_query,
            "embedding": list(self.embedding) if self.embedding else [],
            "created_at": self.created_at,
        }


class ProjectSkillUsageRecord(SQLModel, table=True):
    """D2 companion usage table — metrics value for file skills, no FK.

    Mirrors ``SkillUsageRecord`` (``daemon/repositories/skill/models.py``)
    column-for-column EXCEPT ``skill_id``: TEXT, nullable-FK-free, holding
    the ``pf-`` pseudo-id. ``ab_test_group`` is deliberately omitted —
    file skills never enter A/B (P7; ``ab_test_group=None`` on the
    duck-typed object skips the A/B branch by construction).
    """

    __tablename__ = "project_skill_usage_records"
    __table_args__ = (
        Index("idx_psur_skill_id", "skill_id"),
        Index("idx_psur_instance_id", "instance_id"),
        Index("idx_psur_instance_feedback", "instance_id", "feedback_applied"),
        Index("idx_psur_skill_created", "skill_id", "created_at"),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    skill_id: str = Field(sa_column=Column("skill_id", Text, nullable=False))
    project_id: str = Field(sa_column=Column("project_id", Text, nullable=False))
    instance_id: str = Field(sa_column=Column("instance_id", Text, nullable=False))
    agent_id: str = Field(sa_column=Column("agent_id", Text, nullable=False))

    task_message: Optional[str] = Field(
        default=None, sa_column=Column("task_message", Text, nullable=True)
    )
    selected: bool = Field(
        default=False,
        sa_column=Column("selected", Boolean, nullable=False, default=False),
    )
    applied: bool = Field(
        default=False,
        sa_column=Column("applied", Boolean, nullable=False, default=False),
    )
    task_succeeded: bool = Field(
        default=False,
        sa_column=Column("task_succeeded", Boolean, nullable=False, default=False),
    )
    iterations: int = Field(
        default=0, sa_column=Column("iterations", Integer, nullable=False)
    )
    duration_seconds: int = Field(
        default=0, sa_column=Column("duration_seconds", Integer, nullable=False)
    )
    fallback: bool = Field(
        default=False,
        sa_column=Column("fallback", Boolean, nullable=False, default=False),
    )
    feedback_applied: Optional[bool] = Field(
        default=None,
        sa_column=Column("feedback_applied", Boolean, nullable=True),
    )
    feedback_note: Optional[str] = Field(
        default=None, sa_column=Column("feedback_note", Text, nullable=True)
    )
    created_at: str = Field(
        default_factory=_utc_now_iso, sa_column=Column("created_at", Text)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill_id": self.skill_id,
            "project_id": self.project_id,
            "instance_id": self.instance_id,
            "agent_id": self.agent_id,
            "task_message": self.task_message,
            "selected": self.selected,
            "applied": self.applied,
            "task_succeeded": self.task_succeeded,
            "iterations": self.iterations,
            "duration_seconds": self.duration_seconds,
            "fallback": self.fallback,
            "feedback_applied": self.feedback_applied,
            "feedback_note": self.feedback_note,
            "created_at": self.created_at,
        }
