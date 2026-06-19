"""SQLModel table definitions for the infra asset repository.

Three tables make up the JSONB document model (Option B, locked):

* :class:`InfraAssetType` — global registry of asset-type schemas.
  No ``project_id``: the same type definitions are valid in every
  project, which keeps cross-project tooling consistent.
* :class:`InfraAsset` — main storage. Per-project
  ``UNIQUE(project_id, type, name)`` constraint, self-FK for
  ``parent_asset_id`` (parent/child asset hierarchies within a
  project), and JSONB columns for ``attributes`` /
  ``relationships`` (the "document" payload).
* :class:`InfraAssetHistory` — append-only change log. Every
  create / update / delete on :class:`InfraAsset` writes a row
  here with a full snapshot and the diff (``changed_fields``,
  ``old_values``, ``new_values``).

GIN indexes live in :attr:`InfraAsset.__table_args__` with
``postgresql_using="gin"`` so SQLAlchemy emits them on
PostgreSQL and silently skips them on SQLite (which has no
GIN). This matches the "PostgreSQL is the default, SQLite is for
dev" rule.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from .types import JSONBType


# ============================================================
# Enums
# ============================================================


class InfraChangeType(str, enum.Enum):
    """History row classification.

    Mirrors the lifecycle of a row in :class:`InfraAsset`:

    * ``created`` — first write of the asset.
    * ``updated`` — any subsequent field change.
    * ``deleted`` — last write before the row is removed; the
      snapshot is preserved on the history row for audit.
    """

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Return ``True`` iff ``value`` is a known change type."""
        return value in cls._value2member_map_


# ============================================================
# Models
# ============================================================


class InfraAssetType(SQLModel, table=True):
    """Global type-registry row.

    Holds the JSON-Schema-style definition of an infra asset type
    (``server``, ``k8s_cluster``, ``datacenter``, …). The registry
    is intentionally project-less: every project sees the same
    set of valid types so that DevOps tools (and DevOps agents
    reasoning about cross-project resources) can use the same
    vocabulary everywhere.

    Attributes:
        name: Primary key. Also the value used in
            :attr:`InfraAsset.type`.
        description: Human-readable one-liner surfaced in the
            DevOps tool layer.
        schema_doc: Optional JSON-Schema-shaped document
            describing the expected shape of ``attributes`` for
            assets of this type. Stored under the ``schema_json``
            column in the database (the locked design name); the
            Python attribute is ``schema_doc`` because
            ``schema_json`` shadows a SQLModel/Pydantic
            serialization method and would emit a ``UserWarning``
            on every import.
        created_at: ISO-8601 timestamp, immutable.
        updated_at: ISO-8601 timestamp; bumped on every
            ``update_asset`` call (along with ``version``).
        created_by / updated_by: Audit columns — instance_id of
            the agent that wrote the row.
        version: Optimistic-locking counter. Starts at ``1`` on
            insert and is incremented by every ``update_asset``.
            Callers that supply ``expected_version`` to
            :meth:`SQLModelInfraRepository.update_asset` get a
            check-and-increment; concurrent edits to the same
            asset then raise ``ValueError`` instead of silently
            clobbering each other. NOT NULL DEFAULT 1 — backfilled
            by the M5 migration on existing SQLite rows.
    """

    __tablename__ = "infra_asset_types"

    name: str = Field(primary_key=True, max_length=128)
    description: str = Field(default="")
    schema_doc: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("schema_json", JSONBType, nullable=False),
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "name": self.name,
            "description": self.description,
            "schema_json": dict(self.schema_doc) if self.schema_doc else {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# Module-level Column kept as a reference for use in
# InfraAsset.__mapper_args__. SQLAlchemy's mapper_coercions only
# accepts a Column expression (or a string key) for version_id_col —
# it rejects the Pydantic-FieldInfo-wrapped attribute that SQLModel
# exposes as ``InfraAsset.version``. We can't reference
# ``__table__.c.version`` at class definition time either, so we
# define the Column once and reuse it as the ``sa_column=`` value
# (which deduplicates it into the Table) and as the ``version_id_col``
# target. ``default=1`` preserves the M5 design choice: every fresh
# INSERT starts at version 1 so the first concurrent-modification
# check sees a stable initial value regardless of which dialect
# wrote the row.
_infra_asset_version_col = Column("version", Integer, nullable=False, default=1, server_default="1")


class InfraAsset(SQLModel, table=True):
    """A single infrastructure asset.

    The "document" payload lives in two JSONB columns:

    * ``attributes`` — type-specific structured data (cpu count,
      IP, environment, …). Queryable with the operators listed in
      :meth:`SQLModelInfraRepository.search_assets`.
    * ``relationships`` — links to other entities (other
      ``InfraAsset`` rows, projects, instances). Always a dict
      of the form ``{entity_type: [id, ...]}`` to keep the
      schema predictable; consumers that need a flat list can
      flatten it.

    Self-FK ``parent_asset_id`` enables parent/child asset
    hierarchies (e.g. a ``k8s_cluster`` parent with ``server``
    children). ``ON DELETE SET NULL`` means deleting a parent
    does not cascade — children survive, they just lose the
    link. The repository's ``delete_asset`` method writes a
    history row with the full snapshot before the row is
    removed, so the hierarchy is reconstructable from history
    if needed.

    Attributes:
        id: Primary key. UUID4 by default; callers can supply
            a deterministic value via ``asset_id`` on
            :meth:`SQLModelInfraRepository.create_asset`.
        project_id: Owning project. ``ON DELETE CASCADE`` so a
            project wipe cleans up its assets automatically.
        type: Asset type — must match a row in
            :class:`InfraAssetType` for the asset to be
            considered well-formed (enforced at the tool layer,
            not the DB).
        name: Human-readable name, unique within
            ``(project_id, type)``.
        parent_asset_id: Optional self-FK. ``ON DELETE SET NULL``.
        attributes: JSONB document of type-specific fields.
        relationships: JSONB document of cross-entity links.
        created_at / updated_at: ISO-8601 timestamps.
        created_by / updated_by: Audit columns — instance_id of
            the agent that wrote the row.
    """

    __tablename__ = "infra_assets"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "type",
            "name",
            name="uq_infra_assets_project_type_name",
        ),
        # GIN indexes for JSONB containment / path queries. The
        # ``postgresql_using`` argument is a PostgreSQL-specific
        # Index option; SQLAlchemy ignores it on non-PostgreSQL
        # dialects, so the index is silently skipped on SQLite
        # (which has no GIN).
        Index(
            "idx_infra_assets_attributes_gin",
            "attributes",
            postgresql_using="gin",
        ),
        Index(
            "idx_infra_assets_relationships_gin",
            "relationships",
            postgresql_using="gin",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    project_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("projects.project_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    type: str = Field(sa_column=Column(String, nullable=False, index=True), max_length=128)
    name: str = Field(sa_column=Column(String, nullable=False), max_length=256)
    parent_asset_id: str | None = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("infra_assets.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    attributes: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("attributes", JSONBType, nullable=False),
    )
    relationships: dict[str, list[str]] = Field(
        default_factory=dict,
        sa_column=Column("relationships", JSONBType, nullable=False),
    )

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    created_by: str | None = Field(default=None, max_length=64)
    updated_by: str | None = Field(default=None, max_length=64)
    # M5: optimistic-locking counter. Bumped by every update_asset
    # call — atomically (raw-SQL check-and-increment) when the
    # caller supplies ``expected_version``, and via the ORM's
    # version_id_col auto-increment when it doesn't. Starts at 1
    # on insert so the first concurrent-modification check sees a
    # stable initial value regardless of which dialect wrote the
    # row. Defined as a Python default + sa_column default so that
    # SQLModel.metadata.create_all() emits ``DEFAULT 1 NOT NULL``
    # on PostgreSQL fresh-DB creation AND existing SQLite rows
    # get backfilled to 1 by the 20260619_000005 migration.
    version: int = Field(default=1, sa_column=_infra_asset_version_col)

    # SQLAlchemy ORM configuration: declare the version column as
    # the mapper's ``version_id_col`` so the unit-of-work machinery
    # auto-emits ``AND version = :expected_version`` on UPDATE/DELETE
    # and auto-increments the version on every flush. This brings
    # InfraAsset to parity with Task / JobItem, which already
    # configure ``version_id_col`` — the manual check-and-increment
    # in ``update_asset``'s atomic raw-SQL path remains
    # defense-in-depth, and the legacy ORM-flush path now inherits
    # the auto-increment for free (the repository no longer bumps
    # ``asset.version`` manually).
    __mapper_args__ = {"version_id_col": _infra_asset_version_col}

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type,
            "name": self.name,
            "parent_asset_id": self.parent_asset_id,
            "attributes": dict(self.attributes) if self.attributes else {},
            "relationships": {
                k: list(v) for k, v in (self.relationships or {}).items()
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
        }


class InfraAssetHistory(SQLModel, table=True):
    """Append-only change log for :class:`InfraAsset` rows.

    Every :class:`InfraAsset` create / update / delete writes a
    row here. The ``snapshot`` column captures the full asset
    state at the time of the change so the row is
    self-describing — reconstructing the asset at any past
    point in time does not require walking the history chain
    forward from ``created``.

    Attributes:
        id: Primary key (UUID4).
        asset_id: ``InfraAsset.id`` this row is about.
            ``ON DELETE SET NULL`` — NOT cascade — so that the
            ``deleted`` history row written by
            :meth:`SQLModelInfraRepository.delete_asset` survives
            the asset's removal. After the asset is deleted,
            ``asset_id`` becomes NULL and the row's
            ``snapshot`` (a full dict copy of the asset at
            delete time) is the only surviving link to the
            now-gone row. The
            :meth:`SQLModelInfraRepository.record_change`
            helper still relies on the FK for referential
            integrity at insert time, so this is not a free
            string column.
        project_id: Denormalized copy of the asset's
            ``project_id`` at write time. Lets history queries
            filter by project without joining to
            ``infra_assets``. ``ON DELETE CASCADE`` matches
            the project_history pattern (a project wipe
            cleans up its asset history too).
        change_type: One of ``InfraChangeType`` values.
        snapshot: Full JSONB view of the asset at this point
            in time (``None`` is allowed for the ``created``
            case before the asset is fully formed, but in
            practice the repository always populates it).
        changed_fields: List of attribute / relationship keys
            that changed (only meaningful for ``updated``).
        old_values: Dict of pre-change values (only for
            ``updated``).
        new_values: Dict of post-change values (only for
            ``updated``).
        changed_by: ``instance_id`` of the agent that wrote
            the change. Optional — empty for system-driven
            changes.
        timestamp: ISO-8601 timestamp. Indexed for
            ``ORDER BY timestamp DESC`` queries.
    """

    __tablename__ = "infra_asset_history"
    __table_args__ = (
        Index(
            "idx_infra_asset_history_asset_timestamp",
            "asset_id",
            "timestamp",
        ),
        Index(
            "idx_infra_asset_history_project_timestamp",
            "project_id",
            "timestamp",
        ),
    )

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    # ``ON DELETE SET NULL`` so the audit row written by
    # ``delete_asset`` survives the asset's removal. The
    # snapshot in the row preserves the asset state including
    # its ID; the FK only enforces referential integrity while
    # the asset still exists.
    asset_id: str | None = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("infra_assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    project_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("projects.project_id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    change_type: str = Field(
        default=InfraChangeType.CREATED.value,
        sa_column=Column(String, nullable=False, index=True),
        max_length=16,
    )

    snapshot: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("snapshot", JSONBType, nullable=True),
    )
    changed_fields: list[str] | None = Field(
        default=None,
        sa_column=Column("changed_fields", JSONBType, nullable=True),
    )
    old_values: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("old_values", JSONBType, nullable=True),
    )
    new_values: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column("new_values", JSONBType, nullable=True),
    )

    changed_by: str | None = Field(default=None, max_length=64)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "asset_id": self.asset_id,
            "project_id": self.project_id,
            "change_type": self.change_type,
            "snapshot": dict(self.snapshot) if self.snapshot else None,
            "changed_fields": list(self.changed_fields) if self.changed_fields else None,
            "old_values": dict(self.old_values) if self.old_values else None,
            "new_values": dict(self.new_values) if self.new_values else None,
            "changed_by": self.changed_by,
            "timestamp": self.timestamp,
        }
