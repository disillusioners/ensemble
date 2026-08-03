"""SQLModel table for the pending-experience queue (C3).

Phase 2 of the Project Blueprint evolution. This table accumulates
incremental blueprint update signals (``experience`` / ``history`` /
``manual``) that the blueprinter worker drains in batches via a
durable claim/acknowledge contract.

State machine (C3):

    available ──(claim_batch)──► claimed ──(acknowledge_batch)──► applied
                                                                       │
                                                                       ▼
                                                       (periodic cleanup_processed)
                                                                       │
                                                                       ▼
                                                                [hard-deleted]
    claimed ──(lease timeout)──► retryable ──(claim_batch)──► claimed
    retryable ──(retry_count >= MAX_RETRIES)──► abandoned

Key invariants:

* ``claim_batch`` is atomic per record (single Session transaction).
  Oldest-first by ``created_at``.
* ``acknowledge_batch`` is idempotent and scoped to ``run_token``.
  Records NOT in the ack list remain ``claimed`` until the lease
  expires and ``mark_retryable`` transitions them.
* Acknowledgement sets ``processed_at`` (LEADER DECISION #1) — soft
  delete. A periodic cleanup hard-deletes rows older than N days for
  crash recovery.
* The ``retry_count`` counter is incremented on retry transitions and
  caps the retry chain.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ── State constants (mirrored in the repository's WHERE clauses) ──

PENDING_STATUS_AVAILABLE = "available"
PENDING_STATUS_CLAIMED = "claimed"
PENDING_STATUS_APPLIED = "applied"
PENDING_STATUS_RETRYABLE = "retryable"
PENDING_STATUS_ABANDONED = "abandoned"

#: Statuses that ``claim_batch`` should pick up (in addition to ``available``).
PENDING_CLAIMABLE_STATUSES = (PENDING_STATUS_AVAILABLE, PENDING_STATUS_RETRYABLE)

#: Statuses counted by ``get_pending_count`` (the smart-scan trigger).
PENDING_ACTIVE_STATUSES = (PENDING_STATUS_AVAILABLE, PENDING_STATUS_RETRYABLE)

#: Default lease timeout (minutes) before a ``claimed`` record is
#: reaped back to ``retryable``. Configurable at the call site.
DEFAULT_LEASE_TIMEOUT_MINUTES = 30.0

#: Maximum number of retries before a record is moved to ``abandoned``.
DEFAULT_MAX_RETRIES = 3


class BlueprintPendingUpdate(SQLModel, table=True):
    """A pending incremental update for a project's blueprint.

    Records are accumulated by writers (e.g. ``experience``,
    ``history``) and drained by the blueprinter worker through a
    durable claim/acknowledge contract (C3). The worker's
    ``run_token`` ties a specific batch to its acknowledgements so
    concurrent claims cannot accidentally double-process the same
    row.

    Fields
    ------
    id:
        UUID4 primary key. Stable across the record's lifetime.
    project_id:
        Owning project. Indexed for per-project scans.
    source_type:
        Origin signal — ``experience`` | ``history`` | ``manual``.
    source_payload:
        Original event payload (JSONB). Preserved verbatim so the
        blueprinter can read what was enqueued without joining
        against the source table.
    status:
        State machine position. ``available`` for new rows;
        ``claimed`` while a worker holds the batch; ``applied`` after
        successful acknowledgement; ``retryable`` after a lease
        timeout; ``abandoned`` after exceeding MAX_RETRIES.
    run_token:
        The token of the batch that claimed this row. ``None`` until
        claimed. Used to scope acknowledgements + idempotent retries.
    created_at:
        Enqueue time (UTC ISO). Oldest-first ordering key.
    claimed_at:
        Time of the most recent claim transition (UTC ISO).
    processed_at:
        Time of acknowledgement (UTC ISO). ``None`` until applied.
        Acts as a soft-delete marker; ``cleanup_processed`` hard-deletes
        rows where ``processed_at < now() - N days``.
    retry_count:
        Number of times this row has been re-claimed after a
        lease-timeout retry.
    """

    __tablename__ = "project_blueprint_pending_updates"
    __table_args__ = (
        Index("ix_bp_pending_project_id", "project_id"),
        Index("ix_bp_pending_created_at", "created_at"),
        Index("ix_bp_pending_status", "status"),
        Index("ix_bp_pending_project_status", "project_id", "status"),
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
    source_type: str = Field(default="experience", max_length=16)
    source_payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("source_payload", JSONBType, nullable=False),
    )
    status: str = Field(default=PENDING_STATUS_AVAILABLE, max_length=16)
    run_token: str | None = Field(default=None, max_length=64)
    created_at: str = Field(default_factory=_now_iso)
    claimed_at: str | None = Field(default=None)
    processed_at: str | None = Field(default=None)
    retry_count: int = Field(default=0)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe view of the row."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "source_type": self.source_type,
            "source_payload": self.source_payload,
            "status": self.status,
            "run_token": self.run_token,
            "created_at": self.created_at,
            "claimed_at": self.claimed_at,
            "processed_at": self.processed_at,
            "retry_count": self.retry_count,
        }
