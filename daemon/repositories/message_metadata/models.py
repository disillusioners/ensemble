"""SQLAlchemy table definition for the ``message_metadata`` side table.

Phase 1 C2 of the langgraph-checkpoint-perf plan (Solution M).

The :class:`MessageMetadata` row carries a per-(thread_id, message_id)
timestamp + a nullable ``seq`` column reserved for the Phase 2 PERF-2
cursor pagination workstream. The PK is the composite
``(thread_id, message_id)`` so an ``INSERT ... ON CONFLICT DO NOTHING``
write collapses re-taps on revive + compaction to no-ops at the
constraint level (first-write-wins). Decisions D3 (idempotency) + D5
(``seq`` option value) bind the schema.

DUAL-DRIVER CONTRACT (decisions.md D2)
--------------------------------------
The model is created on every backend by
``SQLModel.metadata.create_all()`` at startup. ``create_all`` is called
from the manager-level repo wiring — the model is imported at module
top-level inside ``daemon/repositories/message_metadata/__init__.py``,
which is in turn imported from ``daemon/repositories/__init__.py``
(the existing convention for new tables). No
``_ensure_postgres_columns`` block is required for a brand-new table —
the model definition + the CREATE TABLE IF NOT EXISTS line in
``daemon/manager.py::_ensure_postgres_columns()`` together guarantee the
table + index exist on the live PG database. The matching SQLite
migration lives at
``daemon/migrations/versions/20260825_000001_create_message_metadata.sql``.

Tool messages are NEVER tapped, so the table never holds a row for a
``ToolMessage`` id. Display invisibility is the
``serialize_message`` ``type=='tool'`` skip at
``daemon/persistence.py:406`` — verified at implementation time per
LD-D2.
"""

from __future__ import annotations

from sqlmodel import Field, Index, SQLModel


class MessageMetadata(SQLModel, table=True):
    """Per-(thread_id, message_id) timestamp row.

    The row's primary key is the composite ``(thread_id, message_id)``,
    so a second ``INSERT ... ON CONFLICT DO NOTHING`` for the same pair
    is a no-op (D3). The ``seq`` column is reserved nullable for Phase 2
    PERF-2 cursor pagination; Phase 1 always writes ``None`` (D5).

    Attributes:
        thread_id: LangGraph ``config["configurable"]["thread_id"]`` —
            equals ``instance_id`` per the project's
            ``thread_id == instance_id`` invariant. TEXT (not UUID) per
            the dual-driver convention.
        message_id: The LangChain ``BaseMessage.id`` (a UUID4 string).
        created_at: ISO-8601 UTC stamp from the tap-site
            ``datetime.now(timezone.utc).isoformat()`` call. The first
            tap wins; re-taps under ON CONFLICT DO NOTHING preserve the
            original stamp (D3, D17, the Critical 8 stability test).
        seq: Reserved nullable for Phase 2 PERF-2. Always ``None`` in
            Phase 1 writes (D5, D-s1).
    """

    __tablename__ = "message_metadata"
    __table_args__ = (
        # The companion ``ix_message_metadata_thread`` index on the
        # ``thread_id`` column — backs the ``get_for_thread`` read
        # primitive (PR3 C1 read-flip will use it; PR2 ships the
        # primitive without callers). Name MUST match the SQLite
        # migration's ``CREATE INDEX IF NOT EXISTS ix_message_metadata_thread``
        # and the PG ``_ensure_postgres_columns`` mirror statement —
        # the dual-driver contract (D2) is "table exists + index name
        # matches".
        Index("ix_message_metadata_thread", "thread_id"),
    )

    thread_id: str = Field(primary_key=True, max_length=128)
    message_id: str = Field(primary_key=True, max_length=128)
    created_at: str = Field(nullable=False, max_length=64)
    seq: int | None = Field(default=None, nullable=True)


__all__ = ["MessageMetadata"]
