"""The ``mcp_gateway_audit`` SQLModel table — the remote-command audit.

SPEC.md §8.4 asks the center to log every forwarded command with
**who / machine / tool / args-hash**. The repository's audit convention
is DB TABLES (``secrets_audit`` behind the ``SecretService``), so the
audit of this subsystem is a table too.

THE ARGUMENTS ARE HASHED, NEVER STORED (§8.4)
---------------------------------------------

The table has NO column for the arguments. A typed password must not end
up in an audit row because it was the argument to a tool — the row
carries ``args_hash`` (the SHA-256 of the canonicalised arguments, lower
-case hex) and nothing else. There is deliberately no "args" column to
fill in by accident.

TIMESTAMPS
----------

``created_at`` is an ISO-8601 TEXT stamp — the lineage of the
``secrets_audit`` / ``instance_ops_audit`` tables, which deliberately
carry ISO-8601 TEXT rather than a naive timestamp column. Nothing here
binds a naive timestamp, so the PG session timezone cannot shift a digit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class McpGatewayAudit(SQLModel, table=True):
    """One audited remote command (append-only).

    Attributes:
        id: Primary key (UUID4).
        created_at: ISO-8601 timestamp (UTC).
        agent_id: WHO asked — the agent identity.
        instance_id: The instance of that agent.
        machine_id: The machine the command ran on.
        tool: The name of the remote tool.
        args_hash: The SHA-256 of the canonicalised arguments. The
            arguments themselves are NEVER stored (§8.4).
        outcome: ``ok`` / ``error`` / ``denied`` / ``timeout``.
        session_id: The session the command ran in.
        error_code: The typed error code, when the command failed.
        detail: Advisory human text. Never the raw arguments.
    """

    __tablename__ = "mcp_gateway_audit"
    __table_args__ = (
        Index(
            "idx_mcp_gateway_audit_agent_created",
            "agent_id",
            "created_at",
        ),
        Index(
            "idx_mcp_gateway_audit_machine_created",
            "machine_id",
            "created_at",
        ),
        Index(
            "idx_mcp_gateway_audit_tool_created",
            "tool",
            "created_at",
        ),
    )

    id: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("id", Text, primary_key=True),
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        sa_column=Column("created_at", Text, nullable=False),
    )
    agent_id: str = Field(sa_column=Column("agent_id", Text, nullable=False))
    instance_id: str | None = Field(
        default=None,
        sa_column=Column("instance_id", Text, nullable=True),
    )
    machine_id: str = Field(sa_column=Column("machine_id", Text, nullable=False))
    tool: str = Field(sa_column=Column("tool", Text, nullable=False))
    args_hash: str = Field(sa_column=Column("args_hash", Text, nullable=False))
    outcome: str = Field(
        default="ok",
        sa_column=Column("outcome", Text, nullable=False, default="ok"),
    )
    session_id: str | None = Field(
        default=None,
        sa_column=Column("session_id", Text, nullable=True),
    )
    error_code: str | None = Field(
        default=None,
        sa_column=Column("error_code", Text, nullable=True),
    )
    detail: str | None = Field(
        default=None,
        sa_column=Column("detail", Text, nullable=True),
    )


__all__ = ["McpGatewayAudit"]
