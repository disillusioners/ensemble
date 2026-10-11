"""MCP-KB key repository — oauth2-auth Phase 3 (T3.11 / D20a).

Synchronous repository over the module-owned ``mcp_kb_keys`` table —
the engine-positional constructor + shared-engine discipline of every
repo in this tree; async callers bridge via ``asyncio.to_thread``.
Deliberately SEPARATE from ``AuthSessionRepository`` (D20a: no
cross-store lookups anywhere in the auth code paths — the KB lane
never reads an auth table and the auth lanes never read this one).
"""

from __future__ import annotations

import logging
from typing import Optional, Sequence

from sqlalchemy import delete, update
from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .models import McpKbKey

logger = logging.getLogger(__name__)


class McpKbKeyRepository:
    """CRUD + hash-lookup over ``mcp_kb_keys`` (module-owned store)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_key(self, row: McpKbKey) -> None:
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    def list_keys(self, *, include_revoked: bool = False) -> Sequence[McpKbKey]:
        """All keys, newest first. Revoked rows hidden unless asked."""
        with Session(self._engine) as session:
            statement = select(McpKbKey).order_by(col(McpKbKey.created_at).desc())
            rows = list(session.exec(statement).all())
        if include_revoked:
            return rows
        return [row for row in rows if row.revoked_at is None]

    def get_key(self, key_id: str) -> McpKbKey | None:
        with Session(self._engine) as session:
            return session.get(McpKbKey, key_id)

    def get_active_by_hash(self, id_hash: str) -> McpKbKey | None:
        """Hash lookup — revoked rows never verify (D20d rejection)."""
        with Session(self._engine) as session:
            statement = (
                select(McpKbKey)
                .where(
                    col(McpKbKey.id_hash) == id_hash,
                    col(McpKbKey.revoked_at).is_(None),
                )
            )
            return session.exec(statement).first()

    def revoke_key(self, key_id: str, *, now_iso: str) -> bool:
        """Soft revoke (idempotent — first revocation timestamp wins)."""
        statement = (
            update(McpKbKey)
            .where(
                col(McpKbKey.id) == key_id,
                col(McpKbKey.revoked_at).is_(None),
            )
            .values(revoked_at=now_iso)
        )
        with Session(self._engine) as session:
            result = session.exec(statement)
            session.commit()
            return result.rowcount == 1

    def delete_key(self, key_id: str) -> bool:
        """Hard delete (CRUD DELETE verb)."""
        statement = delete(McpKbKey).where(col(McpKbKey.id) == key_id)
        with Session(self._engine) as session:
            result = session.exec(statement)
            session.commit()
            return result.rowcount > 0

    def touch_key(self, id_hash: str, *, now_iso: str) -> None:
        """Best-effort ``last_used_at`` stamp (never raises; a touch
        failure must not fail a verified request)."""
        try:
            statement = (
                update(McpKbKey)
                .where(col(McpKbKey.id_hash) == id_hash)
                .values(last_used_at=now_iso)
            )
            with Session(self._engine) as session:
                session.exec(statement)
                session.commit()
        except Exception:  # noqa: BLE001 — telemetry only
            logger.debug("[KbKeys] touch failed", exc_info=True)

    def update_label(self, key_id: str, label: Optional[str]) -> McpKbKey | None:
        """Rename (CRUD UPDATE verb — keys are not mutable beyond label)."""
        with Session(self._engine) as session:
            row = session.get(McpKbKey, key_id)
            if row is None:
                return None
            row.label = label
            session.add(row)
            session.commit()
            session.refresh(row)
            return row


__all__ = ["McpKbKeyRepository"]
