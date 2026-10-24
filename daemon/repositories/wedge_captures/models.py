"""The ``wedge_captures`` SQLModel table (D10 / A3).

ONE ROW PER CAPTURE — an INDEX, never the payload.

The row holds a PATH REFERENCE plus a small metrics snapshot. The
bytes live on disk, in the capture directory. The incident's own
lesson is the reason: a multi-megabyte blob in the DB is how the
checkpoint store reached ~13 GB / ~96.5k rows. This table stores a
path and a few hundred bytes of metadata (D10).

THE UNIQUENESS INVARIANT (A3)
-----------------------------
``capture_dir`` and ``capture_id`` are UNIQUE — in the model AND in
the ``.sql`` migration. The reconciler (T12) re-inserts pending
sidecars with ``ON CONFLICT (capture_dir) DO NOTHING``, which needs
the constraint to exist.

ALL STRING COLUMNS ARE TEXT
---------------------------
On both drivers — never ``VARCHAR`` (the ``instance_ops_audit``
precedent). The JSON column uses the house ``JSONBType`` (JSONB on
PostgreSQL, JSON on SQLite).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, Integer, Text
from sqlmodel import Field, SQLModel

from daemon.repositories.infra.types import JSONBType


def _new_uuid() -> str:
    """A fresh row id (UUID4 string)."""
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    """The row's creation timestamp (ISO-8601, UTC)."""
    return datetime.now(timezone.utc).isoformat()


class WedgeCapture(SQLModel, table=True):
    """One capture's metadata row.

    Attributes:
        id: Primary key (UUID4).
        capture_id: The capture's id — the directory name. UNIQUE.
        capture_dir: The ABSOLUTE capture directory. UNIQUE.
        trigger_reason: One of the canonical reason tokens
            (``sustained_cpu`` / ``sustained_loop_lag`` /
            ``thread_concentration`` / ``manual``).
        trigger_source: ``manual`` or the automatic reason token.
        captured_at: When the capture was taken (ISO-8601).
        boot_epoch: The boot epoch the capture belongs to (naive-UTC
            ISO string).
        host: The host name.
        release_version: The daemon's release version.
        summary: A one-line human summary.
        auto_continue_mode: The effective AutoContinue mode of the
            capture's epoch (``on`` / ``off`` / ``unknown``).
        file_count: How many artifacts the capture wrote.
        total_bytes: The capture directory's total size.
        metrics_snapshot: A small JSON snapshot of the capture's
            metrics — NEVER the payload.
        created_at: The row's creation timestamp.
    """

    __tablename__ = "wedge_captures"
    __table_args__ = (
        Index("idx_wedge_captures_captured_at", "captured_at"),
        Index("idx_wedge_captures_trigger_reason", "trigger_reason"),
        Index("idx_wedge_captures_capture_dir", "capture_dir", unique=True),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    capture_id: str = Field(
        sa_column=Column("capture_id", Text, nullable=False, unique=True),
    )
    capture_dir: str = Field(
        sa_column=Column("capture_dir", Text, nullable=False, unique=True),
    )
    trigger_reason: str = Field(
        sa_column=Column("trigger_reason", Text, nullable=False),
    )
    trigger_source: str = Field(
        default="",
        sa_column=Column("trigger_source", Text, nullable=False, default=""),
    )
    captured_at: str = Field(
        sa_column=Column("captured_at", Text, nullable=False),
    )
    boot_epoch: str | None = Field(
        default=None,
        sa_column=Column("boot_epoch", Text, nullable=True),
    )
    host: str | None = Field(
        default=None,
        sa_column=Column("host", Text, nullable=True),
    )
    release_version: str | None = Field(
        default=None,
        sa_column=Column("release_version", Text, nullable=True),
    )
    summary: str | None = Field(
        default=None,
        sa_column=Column("summary", Text, nullable=True),
    )
    auto_continue_mode: str | None = Field(
        default=None,
        sa_column=Column("auto_continue_mode", Text, nullable=True),
    )
    file_count: int | None = Field(
        default=None,
        sa_column=Column("file_count", Integer, nullable=True),
    )
    total_bytes: int | None = Field(
        default=None,
        sa_column=Column("total_bytes", Integer, nullable=True),
    )
    metrics_snapshot: Any | None = Field(
        default=None,
        sa_column=Column("metrics_snapshot", JSONBType, nullable=True),
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )


__all__ = ["WedgeCapture"]
