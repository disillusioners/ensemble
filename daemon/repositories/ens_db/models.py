"""SQLModel table definitions for the ``ens-db`` tool family.

Currently exposes:

* :class:`RepairLog` — append-only audit log for every dry-run and
  committed repair performed via :func:`daemon.tools.ens_db_tools.create_ens_db_tools`.
  Mirrors the :class:`daemon.repositories.infra.models.InfraAssetHistory`
  pattern (append-only, JSONB snapshots, indexed on
  ``(target_table, timestamp)``) so the existing repository tooling
  patterns remain coherent.

Council disagreement (2.10b — checklist item 4): ``infra.py:226-234``
vs ``infra/models.py:256 + infra/repository.py:540-541``. This module
pins the model location (``daemon/repositories/ens_db/models.py``)
because the change-by audit pattern matches
``InfraAssetHistory.changed_by`` at ``infra/models.py:374-412``: a
nullable string ``created_by_instance_id`` set to the calling agent's
instance id at write time, indexed on ``(target, timestamp)``.
The ``infra.py`` path (asset audit) is NOT the right precedent — it is
in-process audit emitted by a service, not a persisted SQLModel row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _utc_now_iso() -> str:
    """Return the current UTC time as ISO-8601 string (no microseconds)."""
    return datetime.now(timezone.utc).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


class RepairLog(SQLModel, table=True):
    """Append-only audit log for ``ens_db_repair_execute`` invocations.

    Every dry-run AND every committed repair writes a row here with a
    full SQL snapshot, the calling instance id, the dry-run flag, and
    the outcome. The audit ``INSERT`` happens in the SAME transaction
    as the repair on the SAME connection — fail-closed (architect §3.2):
    an audit-write failure rolls back the repair, never commits
    un-audited DDL/DML.

    Partial application must be reconstructible from this table
    (``repair_log.before_snapshot`` + ``repair_log.after_snapshot`` +
    ``repair_log.outcome``). Idempotent ``DO$$``-only DDL/DML is the
    rule that keeps the reconstructibility tractable (architect §3.4
    + §7.3 — pause of another instance does NOT roll back executed
    DML, so recovery happens by replaying the idempotent recipe).

    Attributes:
        id: Primary key (UUID4).
        created_at: ISO-8601 timestamp (UTC).
        created_by_instance_id: Instance id of the agent that wrote
            the row. Mirrors ``InfraAssetHistory.changed_by``.
        created_by_agent_id: Agent id of the caller.
        sql_text: The raw SQL the agent supplied. Stored verbatim —
            the audit row must be self-describing.
        sql_class: One of ``"DDL"``, ``"DML"``, ``"DRY_RUN"``.
        sql_hash: Short fingerprint of ``sql_text`` (e.g. SHA-256
            prefix) so duplicate submissions are greppable without
            indexing the full text.
        dry_run: True if the row records a dry-run preview; False
            for a committed / rolled-back / errored repair attempt.
        before_snapshot: JSONB view of the affected rows BEFORE the
            repair. ``None`` for DDL (no row image) and for INSERT.
        after_snapshot: JSONB view of the affected rows AFTER the
            repair. Captured via ``RETURNING *`` for DML.
        outcome: One of ``"previewed"``, ``"committed"``,
            ``"rolled_back"``, ``"error"``.
        error_message: When ``outcome == "error"``, the structured
            refusal message (class name only, never SQL text).
            ``None`` otherwise.
        target_table: The schema-qualified table name (or DDL
            identifier) the repair targeted. Indexed.
        nonce: Single-use confirm nonce borrowed from
            ``upgrade_tools.py:1937-2006`` primitives; ``None`` for
            previewed dry-runs that minted their own nonce.
        ttl_expires_at: ISO-8601 timestamp marking nonce expiry
            (5 minutes from issuance). ``None`` when no nonce was
            minted (preview-only rows without confirm binding).
    """

    __tablename__ = "repair_log"
    __table_args__ = (
        Index(
            "idx_repair_log_target_timestamp",
            "target_table",
            "created_at",
        ),
        Index(
            "idx_repair_log_created_by",
            "created_by_instance_id",
        ),
    )

    id: str = Field(
        default_factory=_new_uuid,
        primary_key=True,
        max_length=64,
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        max_length=64,
    )
    # Mirrors ``InfraAssetHistory.changed_by`` at infra/models.py:412.
    created_by_instance_id: str | None = Field(default=None, max_length=64)
    created_by_agent_id: str | None = Field(default=None, max_length=64)

    sql_text: str = Field(sa_column=Column(String, nullable=False))
    sql_class: str = Field(sa_column=Column(String, nullable=False), max_length=16)
    sql_hash: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    dry_run: bool = Field(default=False)

    # JSONB snapshots — SQLModel annotations use ``Any`` here so the
    # ``sa_column=Column(JSONBType, ...)`` declaration is the source
    # of truth for the on-disk type (mirrors ``InfraAssetHistory``
    # fields at infra/models.py:395-410). Annotating as ``dict`` or
    # ``list[dict]`` triggers SQLModel type-introspection that fails
    # on the ``dict`` type itself; the SA column carries the column
    # type unambiguously.
    before_snapshot: Any | None = Field(
        default=None,
        sa_column=Column("before_snapshot", JSONBType, nullable=True),
    )
    after_snapshot: Any | None = Field(
        default=None,
        sa_column=Column("after_snapshot", JSONBType, nullable=True),
    )

    outcome: str = Field(
        default="previewed",
        sa_column=Column(String, nullable=False),
        max_length=16,
    )
    error_message: str | None = Field(default=None, max_length=512)

    target_table: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=128,
    )

    nonce: str | None = Field(default=None, max_length=64)
    ttl_expires_at: str | None = Field(default=None, max_length=64)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row (audit-log inspection)."""
        return {
            "id": self.id,
            "created_at": self.created_at,
            "created_by_instance_id": self.created_by_instance_id,
            "created_by_agent_id": self.created_by_agent_id,
            "sql_text": self.sql_text,
            "sql_class": self.sql_class,
            "sql_hash": self.sql_hash,
            "dry_run": self.dry_run,
            "before_snapshot": dict(self.before_snapshot) if self.before_snapshot else None,
            "after_snapshot": dict(self.after_snapshot) if self.after_snapshot else None,
            "outcome": self.outcome,
            "error_message": self.error_message,
            "target_table": self.target_table,
            "nonce": self.nonce,
            "ttl_expires_at": self.ttl_expires_at,
        }
