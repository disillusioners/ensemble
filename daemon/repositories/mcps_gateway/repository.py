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

from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from daemon.services.mcps_gateway.audit import AuditRecord
from daemon.services.timestamps import now_utc_iso

from .models import McpGatewayAudit


class McpGatewayAuditRepository:
    """Writer + readers for the ``mcp_gateway_audit`` ledger."""

    def __init__(self, engine: Engine, read_timeout_ms: int | None = None) -> None:
        self._engine = engine
        # The server-side half of the hung-DB resource bound (QA C9 /
        # Reviewer F2 / Tidier M14): the Phase C audit read is bounded
        # at the REQUEST level by ``asyncio.wait_for`` — but cancelling
        # the request does NOT cancel the query. Without a server-side
        # bound, a HUNG query holds its pooled connection forever (pool
        # exhaustion after a handful of stalled reads). When set
        # (production wires it from ``GatewayConfig.read_timeout_ms`` —
        # the SAME millisecond budget as the request bound) and the
        # dialect is PostgreSQL, ``list_recent`` sets a
        # TRANSACTION-LOCAL ``statement_timeout``: ``SET LOCAL`` cannot
        # leak the GUC back into the pool, and SQLite (the unit-test
        # harness) has no such GUC and is skipped.
        #
        # The coercion happens ONCE, HERE (review nit on C9): the
        # ``> 0`` guard in ``list_recent`` then compares a real int —
        # a string knob (``"1000"``) is coerced (or fails loudly at
        # CONSTRUCTION) instead of raising TypeError at read time.
        self._read_timeout_ms = (
            None if read_timeout_ms is None else int(read_timeout_ms)
        )

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

    def list_recent(
        self, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """The most recent audit rows, newest first.

        ``offset`` skips rows from the HEAD of the newest-first list —
        the pagination seam of the Phase C read surface
        (``GET /api/mcps-gateway/audit``). The order is
        ``created_at DESC, id DESC`` (the ISO-8601 TEXT stamp of the
        audit table — a lexicographic order over a fixed-format stamp
        is a chronological order).

        THE ``id DESC`` TIEBREAKER BUYS PAGING STABILITY, NOT
        CHRONOLOGY (AD8). ``id`` is a uuid4 TEXT — it is uncorrelated
        with time, so within a same-stamp tie the secondary order is
        arbitrary-but-FIXED: no row repeats or vanishes across
        ``limit/offset`` pages (the paging contract the read surface
        exposes). It is NOT a claim about which of two same-stamped
        rows happened first.

        Memory-sink parity (documented, not chased): the memory sink's
        ``AuditRecord`` has no ``id`` and its ``sorted`` is stable, so
        ties there keep insertion order — a benign divergence on the
        unit-test fake, invisible on the wire after the router's
        projection.

        Raises on failure — the service classifies (AD5: exactly one
        swallow layer, and it is not this one).
        """
        with Session(self._engine) as session:
            if (
                self._read_timeout_ms
                and self._read_timeout_ms > 0
                and session.get_bind().dialect.name == "postgresql"
            ):
                # The server-side hung-query bound (see ``__init__``).
                # ``session.connection()`` begins the transaction, so
                # ``SET LOCAL`` is scoped to THIS read and reverts at
                # its end — the pooled connection cannot carry the GUC
                # to the next borrower. ``int()`` coerces, so the SQL
                # cannot be injected through the knob.
                session.connection().exec_driver_sql(
                    f"SET LOCAL statement_timeout = {int(self._read_timeout_ms)}"
                )
            statement = (
                select(McpGatewayAudit)
                .order_by(
                    McpGatewayAudit.created_at.desc(),
                    McpGatewayAudit.id.desc(),
                )
                .limit(max(1, int(limit)))
                .offset(max(0, int(offset)))
            )
            rows = session.exec(statement).all()
            return [row.model_dump() for row in rows]


__all__ = ["McpGatewayAuditRepository"]
