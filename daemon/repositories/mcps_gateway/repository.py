"""The ``mcp_gateway_audit`` writer — the remote-command audit sink.

The sink is the DB-backed implementation of
:class:`daemon.services.mcps_gateway.audit.AuditSink`. It is deliberately
SEPARATE from the secrets lineage's "audit in the same transaction"
pattern: this audit describes a REMOTE side effect on ANOTHER host, so
there is no local transaction to join. The command has already happened
(or already failed) by the time the row could be written.

Therefore:

* the write NEVER aborts a command — the caller (``McpGatewayService``)
  catches the failure, logs it loudly and counts it;
* the arguments are HASHED (§8.4) — the row has no arguments column at
  all, so there is nothing to fill in by accident.

The writer is defensive on the READ side (safe defaults) and loud on the
WRITE side (the exception is the signal).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from daemon.services.mcps_gateway.audit import AuditRecord
from daemon.services.timestamps import now_utc_iso

from .models import McpGatewayAudit

logger = logging.getLogger(__name__)


class McpGatewayAuditRepository:
    """Writer + readers for the ``mcp_gateway_audit`` ledger."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── write ────────────────────────────────────────────────────────────

    def record(self, record: AuditRecord) -> None:
        """Write ONE audit row.

        Raises on failure — the caller decides what a failure means. The
        gateway service logs it, counts it, and lets the command stand.
        """
        row = McpGatewayAudit(
            # P3: the column is NOT NULL — a missing stamp must never
            # become None (the default of the model is an empty string,
            # which is not a timestamp either). The daemon-wide UTC
            # convention fills the gap.
            created_at=record.created_at or now_utc_iso(),
            agent_id=record.agent_id,
            instance_id=record.instance_id or None,
            machine_id=record.machine_id,
            tool=record.tool,
            args_hash=record.args_hash,
            outcome=record.outcome,
            session_id=record.session_id or None,
            error_code=record.error_code or None,
            detail=record.detail or None,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()

    # ── read ─────────────────────────────────────────────────────────────

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """The most recent audit rows, newest first. Safe on failure."""
        try:
            with Session(self._engine) as session:
                statement = (
                    select(McpGatewayAudit)
                    .order_by(McpGatewayAudit.created_at.desc())
                    .limit(max(1, int(limit)))
                )
                rows = session.exec(statement).all()
                return [row.model_dump() for row in rows]
        except Exception as exc:  # noqa: BLE001 — the read side is defensive
            logger.warning("the remote-command audit could not be read: %s", exc)
            return []


__all__ = ["McpGatewayAuditRepository"]
