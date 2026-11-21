"""SQLModel table for the aggregated tool-usage ledger (Phase 1).

One row per ``(day, project_id, tool_name, agent_id)`` — a durable,
aggregated counter of EXECUTED tool calls. NAMES ONLY is a frozen
invariant: there is deliberately NO argument / argv / content /
tool_call_id / instance_id / session_id column, and the observation sink
signature (``daemon/services/tool_usage_observation.py``) has no argument
slot either. The schema and the type enforce the same guarantee.

DUAL-DRIVER CONTRACT (D5; memoir / message_metadata precedent)
---------------------------------------------------------------
The model is registered into ``SQLModel.metadata`` by the late-import
block in ``daemon/manager.py`` BEFORE ``create_all`` — that is the PG
source of truth on existing databases. Fresh SQLite databases pick the
table up from the migration runner via
``daemon/migrations/versions/20261121_020000_create_tool_usage_daily.sql``.
Brand-new table ⇒ NO ``_ensure_postgres_columns`` mirror (create_all
OWNS it). All string columns are TEXT on both drivers; ``call_count`` is
BIGINT on PG and INTEGER (int64) on SQLite via SQLAlchemy ``BigInteger``.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, Index, Text
from sqlmodel import Field, SQLModel

#: Sentinel for an un-attributable agent (D1 — no agent dir is named
#: ``unknown``; mirrors the ``instance_id`` fallback idiom).
UNKNOWN_AGENT = "unknown"

#: Sentinel for an un-attributable project (D16, SUPERSEDES D11's ``""``).
#: Self-documenting, unambiguous against real UUID project ids, matches
#: the house reserved-name idiom (``uuid5(..., "__system_default__")``).
NONE_PROJECT = "__none__"


class ToolUsageDaily(SQLModel, table=True):
    """One UTC-day's aggregated call count for one tool / agent / project.

    The four-part primary key is the upsert conflict target. ``call_count``
    and ``updated_at`` are in NO index, so counter increments qualify as
    HOT updates on PostgreSQL.
    """

    __tablename__ = "tool_usage_daily"

    # Index names + column lists are byte-identical to the two
    # ``CREATE INDEX IF NOT EXISTS`` statements in the migration file
    # (3-site lockstep precedent: memoir / room).
    __table_args__ = (
        Index("idx_tool_usage_daily_project_day", "project_id", "day"),
        Index("idx_tool_usage_daily_tool_day", "tool_name", "day"),
    )

    day: str = Field(
        sa_column=Column("day", Text, primary_key=True),
        description="ISO UTC date YYYY-MM-DD (PK part).",
    )
    project_id: str = Field(
        sa_column=Column("project_id", Text, primary_key=True),
        description=(
            "Project id (PK part); sentinel '__none__' for un-attributable "
            "calls (D16)."
        ),
    )
    tool_name: str = Field(
        sa_column=Column("tool_name", Text, primary_key=True),
        description="Executed tool's NAME (PK part). NAMES ONLY — no args.",
    )
    agent_id: str = Field(
        sa_column=Column("agent_id", Text, primary_key=True),
        description="Agent id (PK part); sentinel 'unknown' when unresolved.",
    )
    call_count: int = Field(
        default=0,
        sa_column=Column("call_count", BigInteger, nullable=False, default=0),
        description="Aggregated at-least-once execution counter.",
    )
    updated_at: str | None = Field(
        default=None,
        sa_column=Column("updated_at", Text, nullable=True),
        description="ISO-8601 UTC stamp of the last flush (nullable).",
    )


__all__ = ["ToolUsageDaily", "UNKNOWN_AGENT", "NONE_PROJECT"]
