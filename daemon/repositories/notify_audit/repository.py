"""The ``notify_audit`` writer — the budget source + audit trail (D8/C3).

The repository behind ``daemon/tools/notify_user.py``. Every method lets
expected failures RAISE: the ledger is the rate-limit budget source, so
the TOOL wraps each call and converts a failure into the fail-closed
typed refusal ``notify_audit_unavailable`` (N2 — no ledger row, no
send). This is the deliberate divergence from the mcp_gateway_audit
best-effort precedent: THERE the ledger failure could only cost
observability; HERE it could bypass the caps.

Method map (C3 commit-before-count, serialized by the B2 lock in the
tool — the DB serialization is the durable restart/crash backstop):

============================  ===========================================
Method                        Semantics
============================  ===========================================
``insert_accepted``           The write-ahead INSERT (own transaction).
                              Returns the row id — the budget row.
``count_accepted_for_target`` The rolling-hour COUNT (fresh statement).
                              ONLY rows in the ACCEPTED CLASS consume
                              budget (N3) — ``accepted`` (still
                              standing) plus the settled terminal
                              forms ``dispatched`` / ``delivery_failed``
                              / ``delivery_unknown`` (the predicate is
                              ``ACCEPTED_CLASS_OUTCOMES``); NULL
                              ``target_hash`` rows are excluded
                              explicitly (fold-able 9).
``update_outcome``            Terminal UPDATE of the own row.
``write_refusal``             The refusal ledger row (``outcome=
                              "refused"`` + ``refusal_reason``). The
                              refusal is the security signal (N3) —
                              refusal rows NEVER consume budget.
``last_accepted_class_created_at``  The ≥60s interval baseline
                              (read-before-insert; the pinned
                              "last-accepted class" reading —
                              refused/rate_limited rows do not
                              extend the cooldown).
``sweep_stale_accepted``      The N4 crash-orphan sweep: ``accepted``
                              rows older than the threshold with no
                              terminal transition are re-marked
                              terminal ``delivery_unknown`` (they read
                              as UNKNOWN, not as false deliveries).
============================  ===========================================
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from .models import (
    ACCEPTED_CLASS_OUTCOMES,
    NotifyAudit,
    OUTCOME_ACCEPTED,
    OUTCOME_DELIVERY_UNKNOWN,
    OUTCOME_REFUSED,
)


class NotifyAuditRepository:
    """Writer + readers for the ``notify_audit`` ledger.

    Constructed with the daemon engine (the same engine the instance
    repository uses). Unlike the instance_ops writer, methods here raise
    on failure by design — the caller (the tool) owns the fail-closed
    conversion, because ONLY the tool knows whether a send is at stake.

    There is deliberately NO logger in this module: every raise is
    logged by the ONE caller (``daemon/tools/notify_user.py`` — the
    write-ahead insert failure, the sweep failure and every refusal
    write all log there), so a failure is reported once, in one place,
    with the tool's own context.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── writers ───────────────────────────────────────────────────────

    def insert_accepted(
        self,
        *,
        agent_id: str,
        instance_id: str,
        source_id: str | None,
        target_hash: str | None,
        body_length: int,
        body_sha256: str,
    ) -> str:
        """The write-ahead ``accepted`` INSERT — COMMITTED in its own
        transaction (C3 commit-before-count). Returns the row id.

        Crash semantics (N4): a crash after this commit and before the
        terminal update leaves a durable ``accepted`` row — it counts
        toward M (conservative, under-send biased) until the sweep
        re-marks it ``delivery_unknown``.
        """
        row = NotifyAudit(
            agent_id=agent_id,
            instance_id=instance_id,
            action="notify_user",
            source_id=source_id,
            target_hash=target_hash,
            outcome=OUTCOME_ACCEPTED,
            body_length=int(body_length),
            body_sha256=body_sha256,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            # Capture the id INSIDE the session — commit expires the row's
            # attributes and a post-close read raises DetachedInstanceError
            # (the instance_ops write_audit_ahead lesson, kept).
            return row.id

    def update_outcome(
        self,
        row_id: str,
        outcome: str,
        *,
        error_code: str | None = None,
        refusal_reason: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Terminal UPDATE of the own write-ahead row. Raises on
        failure (the tool's timeout path converts a failed update into
        a log + reliance on the N4 sweep)."""
        with Session(self._engine) as session:
            row = session.get(NotifyAudit, row_id)
            if row is None:
                raise LookupError(f"notify_audit row {row_id} not found")
            row.outcome = outcome
            if error_code is not None:
                row.error_code = error_code[:256]
            if refusal_reason is not None:
                row.refusal_reason = refusal_reason[:128]
            if detail is not None:
                row.detail = detail[:512]
            session.add(row)
            session.commit()

    def write_refusal(
        self,
        *,
        agent_id: str,
        instance_id: str,
        source_id: str | None,
        target_hash: str | None,
        refusal_reason: str,
        body_length: int,
        body_sha256: str,
        detail: str | None = None,
    ) -> str:
        """The refusal ledger row (``outcome="refused"``). Refusal rows
        NEVER consume budget (N3) — they are the security signal."""
        row = NotifyAudit(
            agent_id=agent_id,
            instance_id=instance_id,
            action="notify_user",
            source_id=source_id,
            target_hash=target_hash,
            outcome=OUTCOME_REFUSED,
            refusal_reason=refusal_reason,
            body_length=int(body_length),
            body_sha256=body_sha256,
            detail=detail[:512] if detail else None,
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            return row.id

    # ── readers / the budget rails ────────────────────────────────────

    def last_accepted_class_created_at(self, *, instance_id: str) -> str | None:
        """The newest ``created_at`` among the calling instance's rows
        whose CURRENT outcome is accepted-class (``accepted`` standing,
        ``dispatched``, ``delivery_failed``, ``delivery_unknown``).

        This is the pinned interval baseline (fold-able 4): a refused
        or ``rate_limited``-flipped attempt does NOT extend the ≥60s
        cooldown — only an attempt that actually consumed a budget slot
        does. Returns ``None`` when the instance has never reached
        dispatch.
        """
        stmt = (
            select(func.max(NotifyAudit.created_at))
            .where(NotifyAudit.instance_id == instance_id)
            .where(
                NotifyAudit.outcome.in_(list(ACCEPTED_CLASS_OUTCOMES))
            )
        )
        with Session(self._engine) as session:
            return session.exec(stmt).one()

    def count_accepted_for_target(
        self, *, target_hash: str, since_iso: str
    ) -> int:
        """Rows that CONSUMED a budget slot for THIS resolved
        destination within the window, INCLUDING crash orphans (N4) and
        EXCLUDING NULL ``target_hash`` rows explicitly (fold-able 9 —
        pre-resolution refusals can never pool into a cap).

        The predicate is the ACCEPTED-CLASS set (C3 + N3 + N4):
        ``accepted`` (the write-ahead row, still standing), plus the
        terminal states a settled accepted row reaches
        (``dispatched`` / ``delivery_failed`` / ``delivery_unknown``).
        ``rate_limited`` and ``refused`` rows are EXCLUDED — a refused
        burst must never self-DoS the hour, and a rate-limited flip
        means the slot was never spent.

        (Reading only ``outcome == "accepted"`` would be WRONG: a
        settled row leaves the accepted class, so the cap would never
        accrue — the budget would reset on every successful send.)
        """
        stmt = (
            select(func.count())
            .select_from(NotifyAudit)
            .where(NotifyAudit.target_hash == target_hash)
            .where(NotifyAudit.target_hash.is_not(None))  # fold-able 9
            .where(
                NotifyAudit.outcome.in_(list(ACCEPTED_CLASS_OUTCOMES))
            )
            .where(NotifyAudit.created_at > since_iso)
        )
        with Session(self._engine) as session:
            return int(session.exec(stmt).one())

    def sweep_stale_accepted(
        self, *, older_than_seconds: int
    ) -> int:
        """The N4 crash-orphan sweep — re-mark ``accepted`` rows with no
        terminal transition as terminal ``delivery_unknown``.

        "Stale" = the write-ahead INSERT is older than
        ``older_than_seconds`` (the tool DERIVES that threshold as
        ``max(120s, 12 x the dispatch bound)`` — so a live dispatch can
        never be swept, whatever the (tunable) bound is set to).
        Returns the number of rows re-marked. Best-effort by contract:
        the TOOL calls this defensively before its gates; a sweep
        failure is logged, never raised here beyond propagating — the
        caller decides.
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        ).isoformat()
        with Session(self._engine) as session:
            rows = session.exec(
                select(NotifyAudit)
                .where(NotifyAudit.outcome == OUTCOME_ACCEPTED)
                .where(NotifyAudit.created_at < cutoff)
            ).all()
            if not rows:
                return 0
            for row in rows:
                row.outcome = OUTCOME_DELIVERY_UNKNOWN
                row.error_code = row.error_code or "notify_orphan_swept"
                row.detail = (
                    row.detail
                    or "write-ahead row never reached a terminal outcome "
                    "(crash orphan) — swept to delivery_unknown (N4)"
                )
                session.add(row)
            session.commit()
            return len(rows)
