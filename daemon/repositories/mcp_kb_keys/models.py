"""MCP-KB key SQLModel model — oauth2-auth Phase 3 (T3.11 / D20a).

MODULE-OWNED storage for the MCP-KB module-lane keys (decisions.md
D20a): deliberately NOT ``AuthConfig``, NOT ``auth_bearer_tokens`` —
zero OAuth2 config delta ([REQ] §6.6) and hard lane isolation (a KB key
can never cross-authenticate an OAuth2 lane and vice versa; D20 is a
HARD requirement). The table has its own per-table migration per the
house convention (``daemon/migrations/versions/20261011_030100_create_mcp_kb_keys.sql``).

Schema conventions mirror the P2 auth tables: TEXT ISO-8601 UTC
timestamps (INV-7), hash-only secret material (NFR-13 —
``id_hash`` is ``SHA-256(token)`` hex; the plaintext ``kbk_…`` exists
only at mint time in the CRUD response and the operator's store).
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Column, Index, Text
from sqlmodel import Field, SQLModel


class McpKbKey(SQLModel, table=True):
    """One MCP-KB module key (hash-only storage; D20a/D20b).

    Lifecycle: minted via the OAuth2-gated ``/api/mcp/kb/keys`` CRUD
    (T3.12), verified by the module-lane mount guard (T3.11), soft-
    revoked or hard-deleted via the same CRUD. No background sweeper in
    v1 — keys are long-lived by design and rotation is explicit
    (mint-new → revoke-old); the table is small and operator-managed.
    """

    __tablename__ = "mcp_kb_keys"

    __table_args__ = (
        Index(
            "idx_mcp_kb_keys_id_hash",
            "id_hash",
            unique=True,
        ),
    )

    id: str = Field(
        sa_column=Column("id", Text, primary_key=True),
        description="URL-safe random id (uuid4 hex) — CRUD handle",
    )
    id_hash: str = Field(
        sa_column=Column("id_hash", Text, nullable=False),
        description="SHA-256(token) hex — plaintext kbk_… NEVER stored",
    )
    label: Optional[str] = Field(
        default=None,
        sa_column=Column("label", Text, nullable=True, default=None),
        description="Operator-friendly name (who/what uses this key)",
    )
    created_at: str = Field(
        sa_column=Column("created_at", Text, nullable=False),
        description="now_utc_iso() at mint",
    )
    last_used_at: Optional[str] = Field(
        default=None,
        sa_column=Column("last_used_at", Text, nullable=True, default=None),
        description="now_utc_iso() at last successful mount verification",
    )
    revoked_at: Optional[str] = Field(
        default=None,
        sa_column=Column("revoked_at", Text, nullable=True, default=None),
        description="Soft-revocation marker — NULL = active",
    )


__all__ = ["McpKbKey"]
