"""Shared Context Metadata model (Phase 1).

This module defines the ``SharedContextMetadata`` SQLModel used by
the Shared Context Metadata KV system. It is a thin, generic
key-value store keyed by ``(context_key, meta_key)`` where
``context_key`` is an opaque caller-supplied partition identifier
(e.g. a session id, an instance id, a project tag) and ``meta_key``
is the per-context key.

The design mirrors :class:`ProjectMetadataRecord` — same
``sa_column=Column(...)`` style, same ``JSONBType`` payload
column, same ISO-8601 timestamp defaults, and a composite
``UniqueConstraint`` on ``(context_key, meta_key)`` to prevent
silent duplicates at the database boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Integer, String, UniqueConstraint
from sqlmodel import SQLModel, Field

from daemon.repositories.infra.types import JSONBType


class SharedContextMetadata(SQLModel, table=True):
    """Generic metadata KV row for any caller-supplied context key.

    Mirrors :class:`daemon.repositories.project.models.ProjectMetadataRecord`
    in shape (id PK, partition column, meta_key, JSONB payload,
    ISO timestamps, composite unique constraint) but is intentionally
    partition-agnostic: any string can serve as ``context_key``. This
    makes the table suitable for cross-cutting callers (session-level
    scratch space, per-instance memory, per-tenant settings) that do
    not have their own dedicated metadata table.

    The composite ``UniqueConstraint`` on
    ``(context_key, meta_key)`` is the authoritative guard against
    duplicates; the repository's ``set_many`` upsert path relies on
    this constraint being present on every supported dialect.
    """
    __tablename__ = "shared_context_metadata"

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    context_key: str = Field(
        sa_column=Column(String, nullable=False, index=True),
    )
    meta_key: str = Field(
        sa_column=Column(String, nullable=False),
    )
    meta_value: Any = Field(
        sa_column=Column(JSONBType, nullable=True),
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    __table_args__ = (
        UniqueConstraint("context_key", "meta_key", name="uq_shared_context_metadata_key"),
    )